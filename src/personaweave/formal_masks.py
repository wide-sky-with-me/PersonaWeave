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


def validate_and_rasterize_semantic_mask(
    *,
    source_path: Path,
    proposal: SemanticMaskProposal,
    source_asset_id: str,
    source_asset_code: str,
    editable_faces: list[FormalMaskFaceScope],
    protected_faces: list[BoundingBox],
) -> CompiledMaskArtifact:
    with Image.open(source_path) as source_img:
        width, height = source_img.size

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    include_contours: list[SemanticMaskContour] = []
    exclude_contours: list[SemanticMaskContour] = []
    for contour in proposal.contours:
        if contour.operation == "exclude":
            exclude_contours.append(contour)
        else:
            include_contours.append(contour)

    for contour in include_contours:
        points = contour.points
        if points:
            poly = _normalize_contour_points(points, width, height)
            if len(poly) >= 3:
                draw.polygon(poly, fill=255)
        else:
            x = max(0, min(width - 1, contour.x))
            y = max(0, min(height - 1, contour.y))
            w = max(1, min(width - x, contour.width))
            h = max(1, min(height - y, contour.height))
            draw.rectangle((x, y, x + w - 1, y + h - 1), fill=255)

    for contour in exclude_contours:
        points = contour.points
        if points:
            poly = _normalize_contour_points(points, width, height)
            if len(poly) >= 3:
                draw.polygon(poly, fill=0)
        else:
            x = max(0, min(width - 1, contour.x))
            y = max(0, min(height - 1, contour.y))
            w = max(1, min(width - x, contour.width))
            h = max(1, min(height - y, contour.height))
            draw.rectangle((x, y, x + w - 1, y + h - 1), fill=0)

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
