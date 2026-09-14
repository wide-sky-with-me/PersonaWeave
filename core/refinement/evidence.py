"""Bounded in-memory evidence construction; never fetch paths, URLs or change person IDs."""

import json

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from core.image_security import MAX_IMAGE_PIXELS
from core.refinement.contracts import MAX_LOCATOR_CHARS, MAX_PEOPLE

MAX_REFERENCES = 8
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_CHARS = 12000


def validate_image(image: np.ndarray) -> np.ndarray:
    """Require a decoded uint8 BGR canvas with a bounded, nonempty pixel count."""
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Evidence must be a decoded uint8 BGR image")
    height, width = image.shape[:2]
    if not height or not width or height * width > MAX_IMAGE_PIXELS:
        raise ValueError("Evidence image dimensions exceed the supported safety bounds")
    return image


def checked_text(value: str, limit: int) -> str:
    """Reject oversized current intent instead of silently dropping its trailing constraints."""
    if not isinstance(value, str):
        raise ValueError("Evidence text must be a string")
    if len(value) > limit:
        raise ValueError(f"Current refinement text exceeds the {limit}-character limit")
    return value.strip()


def bounded_text(value: str, limit: int) -> str:
    """Bound untrusted text without interpreting instructions, image URLs or markup."""
    if not isinstance(value, str):
        raise ValueError("Evidence text must be a string")
    return value.strip()[:limit]


def bounded_history(history: list[dict]) -> list[dict]:
    """Include only recent user/assistant prose; never replay roles, tools or old images."""
    if not isinstance(history, list):
        raise ValueError("History must be a list of lightweight text messages")
    selected = []
    remaining = MAX_HISTORY_CHARS
    for item in reversed(history[-MAX_HISTORY_MESSAGES:]):
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        if not isinstance(item.get("content"), str):
            continue
        text = bounded_text(item["content"], min(2000, remaining))
        if text:
            selected.append({"role": item["role"], "content": text})
            remaining -= len(text)
        if not remaining:
            break
    return list(reversed(selected))


def data_block(data: dict) -> str:
    """Keep application data in an escaped JSON envelope, not interpolated system instructions."""
    payload = json.dumps(data, ensure_ascii=False).replace("<", r"\u003c").replace(">", r"\u003e")
    return f"<untrusted_evidence_json>\n{payload}\n</untrusted_evidence_json>"


def prepare_people(people: list[dict], references: dict[int, list[np.ndarray]]) -> list[dict]:
    """Validate exact stable bindings; list position or fresh detector ordering has no identity meaning."""
    if not isinstance(people, list) or len(people) > MAX_PEOPLE or not isinstance(references, dict):
        raise ValueError("People/references exceed supported evidence bounds")
    result = []
    seen = set()
    for person in people:
        if not isinstance(person, dict):
            raise ValueError("People must be mappings with a stable person_id")
        person_id = person.get("person_id")
        if type(person_id) is not int or person_id < 1 or person_id in seen:
            raise ValueError("Stable person_id values must be unique positive integers")
        seen.add(person_id)
        locator = checked_text(person.get("person_locator", ""), MAX_LOCATOR_CHARS)
        refs = references.get(person_id, [])
        if not isinstance(refs, list) or len(refs) > MAX_REFERENCES:
            raise ValueError("Each person supports at most eight reference images")
        for reference in refs:
            validate_image(reference)
        result.append(
            {
                "person_id": person_id,
                "person_locator": locator,
                "user_focus": checked_text(person.get("user_focus", ""), 1000),
                "reference_count": len(refs),
                "reference_board_label": f"REFERENCE_BOARD_PERSON_{person_id}" if refs else None,
            }
        )
    if any(type(key) is not int or key not in seen for key in references):
        raise ValueError("Reference bindings must name an existing stable person_id")
    return result


def build_reference_board(person_id: int, images: list[np.ndarray]) -> np.ndarray:
    """Fit complete references in labeled tiles; do not crop, redetect faces or mix identities."""
    if type(person_id) is not int or person_id < 1 or not 1 <= len(images) <= MAX_REFERENCES:
        raise ValueError("A reference board requires one stable ID and one to eight images")
    columns = min(2, len(images))
    rows = (len(images) + columns - 1) // columns
    board = Image.new("RGB", (columns * 320, 32 + rows * 352), (24, 24, 27))
    draw = ImageDraw.Draw(board)
    draw.text((12, 10), f"TARGET IDENTITY: PERSON {person_id}", fill="white")
    for index, image in enumerate(images):
        validate_image(image)
        tile = ImageOps.contain(Image.fromarray(image[:, :, ::-1]), (304, 312), Image.Resampling.LANCZOS)
        left, top = (index % columns) * 320, 32 + (index // columns) * 352
        draw.text((left + 8, top + 6), f"PERSON {person_id} / REFERENCE {index + 1}", fill="white")
        board.paste(tile, (left + (320 - tile.width) // 2, top + 32 + (312 - tile.height) // 2))
    return np.asarray(board)[:, :, ::-1].copy()
