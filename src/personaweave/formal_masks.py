from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field

from personaweave.domain import (
    AttributePermission,
    BoundingBox,
    CanonicalMaskEvidence,
    CompiledBatchContract,
    ReviewReason,
    ReviewReasonSet,
    SemanticMaskContour,
    SemanticMaskProposal,
)


class FormalMaskFaceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    face_instance_id: str = Field(min_length=1, max_length=128)
    person_id: str = Field(min_length=1, max_length=128)
    person_code: str = Field(pattern=r"^P\d+$", max_length=40)
    bbox: BoundingBox
    permissions: AttributePermission


class CompiledMaskArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: CanonicalMaskEvidence
    png_bytes: bytes

    @property
    def sha256(self) -> str:
        return self.evidence.sha256

    @property
    def width(self) -> int:
        return self.evidence.width

    @property
    def height(self) -> int:
        return self.evidence.height

    @property
    def proposal(self):
        return self.evidence.proposal

    @property
    def review_reasons(self) -> ReviewReasonSet:
        return self.evidence.review_reasons


class FormalCompilationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: CompiledBatchContract
    masks: tuple[CompiledMaskArtifact, ...] = Field(default_factory=tuple)


def _normalize_contour_points(
    points: list[Any],
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    normalized: list[tuple[int, int]] = []
    for point in points:
        if isinstance(point, dict):
            px = point.get("x", 0.0)
            py = point.get("y", 0.0)
        else:
            px = point.x
            py = point.y
        nx = max(0, min(width - 1, int(round(px * (width - 1)))))
        ny = max(0, min(height - 1, int(round(py * (height - 1)))))
        normalized.append((nx, ny))
    return normalized


def _polygon_area(points: list[tuple[float, float]]) -> float:
    return abs(sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )) / 2


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(
    a: tuple[float, float], b: tuple[float, float],
    c: tuple[float, float], d: tuple[float, float],
) -> bool:
    def on_segment(start: tuple[float, float], end: tuple[float, float], point: tuple[float, float]) -> bool:
        return (
            min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
            and min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
        )

    values = (_orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b))
    if values[0] == 0 and on_segment(a, b, c):
        return True
    if values[1] == 0 and on_segment(a, b, d):
        return True
    if values[2] == 0 and on_segment(c, d, a):
        return True
    if values[3] == 0 and on_segment(c, d, b):
        return True
    return (values[0] > 0) != (values[1] > 0) and (values[2] > 0) != (values[3] > 0)


def _validate_polygon(points: list[Any], *, contour: SemanticMaskContour, width: int, height: int) -> list[tuple[int, int]]:
    if len(points) < 3:
        raise ValueError("mask contours require at least three points")
    normalized = [
        (
            float(point.get("x", 0.0) if isinstance(point, dict) else point.x),
            float(point.get("y", 0.0) if isinstance(point, dict) else point.y),
        )
        for point in points
    ]
    edge_count = len(normalized)
    for first in range(edge_count):
        first_end = (first + 1) % edge_count
        for other in range(first + 1, edge_count):
            other_end = (other + 1) % edge_count
            if other in {first_end, (first - 1) % edge_count}:
                continue
            if _segments_intersect(normalized[first], normalized[first_end], normalized[other], normalized[other_end]):
                raise ValueError("self-intersecting mask contour")
    if contour.operation == "include" and _polygon_area(normalized) > 0.5:
        raise ValueError("oversized mask contour")
    return _normalize_contour_points(points, width, height)


def validate_and_rasterize_semantic_mask(
    *,
    source_path: Path,
    proposal: SemanticMaskProposal,
    source_asset_id: str,
    source_asset_code: str,
    editable_faces: list[FormalMaskFaceScope],
    protected_faces: list[BoundingBox],
) -> CompiledMaskArtifact:
    if (
        proposal.source_asset_id != source_asset_id
        or proposal.source_asset_code != source_asset_code
    ):
        raise ValueError("semantic mask proposal source identity does not match")
    with Image.open(source_path) as source_img:
        orientation = source_img.getexif().get(274, 1)
        if orientation != 1:
            raise ValueError("source image has non-identity EXIF orientation")
        if source_img.mode in {"RGBA", "LA", "PA"}:
            raise ValueError("source image contains alpha channel")
        if "transparency" in source_img.info:
            raise ValueError("source image may contain transparency")
        width, height = source_img.size

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    include_contours: list[SemanticMaskContour] = []
    exclude_contours: list[SemanticMaskContour] = []
    editable_by_id = {scope.face_instance_id: scope for scope in editable_faces}
    for contour in proposal.contours:
        scope = editable_by_id.get(contour.face_instance_id)
        if scope is None:
            raise ValueError("mask contour contains unconfirmed face mappings")
        if (
            contour.person_id != scope.person_id
            or contour.person_code != scope.person_code
            or contour.permissions != scope.permissions
        ):
            raise ValueError("mask contour does not match confirmed face authorization")
        if contour.operation == "exclude":
            exclude_contours.append(contour)
        else:
            include_contours.append(contour)

    for contour in include_contours:
        points = contour.points
        if points:
            poly = _validate_polygon(points, contour=contour, width=width, height=height)
            draw.polygon(poly, fill=255)
        else:
            x = max(0, min(width - 1, contour.x))
            y = max(0, min(height - 1, contour.y))
            w = max(1, min(width - x, contour.width))
            h = max(1, min(height - y, contour.height))
            draw.rectangle((x, y, x + w - 1, y + h - 1), fill=255)

    include_mask = mask.copy()
    for contour in exclude_contours:
        points = contour.points
        if points:
            poly = _validate_polygon(points, contour=contour, width=width, height=height)
            draw.polygon(poly, fill=0)
        else:
            x = max(0, min(width - 1, contour.x))
            y = max(0, min(height - 1, contour.y))
            w = max(1, min(width - x, contour.width))
            h = max(1, min(height - y, contour.height))
            draw.rectangle((x, y, x + w - 1, y + h - 1), fill=0)

    # Validate requested protection against the proposal before clipping. A
    # contour that reaches a protected neighbor is unsafe even if later
    # clipping would hide the offending pixels.
    for protected in protected_faces:
        px0, py0 = max(0, protected.x), max(0, protected.y)
        px1, py1 = min(width, protected.x + protected.width), min(height, protected.y + protected.height)
        for x in range(px0, px1):
            for y in range(py0, py1):
                if mask.getpixel((x, y)):
                    raise ValueError("mask includes protected-face pixels")

    for face in editable_faces:
        center_x = int(face.bbox.x + face.bbox.width / 2)
        center_y = int(face.bbox.y + face.bbox.height / 2)
        if include_mask.getpixel((center_x, center_y)) == 0:
            raise ValueError("include contour does not cover editable face center")

    # Clip proposed geometry to the union of confirmed editable face boxes.
    authorized = Image.new("L", (width, height), 0)
    authorized_draw = ImageDraw.Draw(authorized)
    for face in editable_faces:
        x0 = max(0, face.bbox.x)
        y0 = max(0, face.bbox.y)
        x1 = min(width, face.bbox.x + face.bbox.width)
        y1 = min(height, face.bbox.y + face.bbox.height)
        authorized_draw.rectangle((x0, y0, max(x0, x1 - 1), max(y0, y1 - 1)), fill=255)
    mask = Image.composite(mask, Image.new("L", (width, height), 0), authorized)
    output = BytesIO()
    mask.save(output, format="PNG", optimize=True)
    png_bytes = output.getvalue()
    digest = hashlib.sha256(png_bytes).hexdigest()

    editable_ids = [scope.face_instance_id for scope in editable_faces]
    review_reasons: ReviewReasonSet = frozenset()

    low_confidence = any(
        contour.confidence < 0.9
        for contour in proposal.contours
    )
    if low_confidence:
        review_reasons = frozenset({ReviewReason.MASK_UNCERTAIN})

    evidence = CanonicalMaskEvidence(
        source_asset_id=source_asset_id,
        source_asset_code=source_asset_code,
        width=width,
        height=height,
        sha256=digest,
        proposal=proposal,
        review_reasons=review_reasons,
    )

    return CompiledMaskArtifact(evidence=evidence, png_bytes=png_bytes)
