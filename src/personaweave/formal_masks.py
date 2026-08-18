from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

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

MAX_CONTOURS = 64
MAX_POINTS_PER_CONTOUR = 64
MAX_EDITABLE_CANVAS_FRACTION = 0.50
MIN_FACE_OVERLAP_FRACTION = 0.20
MASK_CONFIDENCE_THRESHOLD = 0.85
CONTOUR_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class FormalMaskFaceScope:
    face_instance_id: str
    person_id: str
    person_code: str
    bbox: BoundingBox
    permissions: AttributePermission


@dataclass(frozen=True, slots=True)
class ValidatedCanonicalMask:
    width: int
    height: int
    png_bytes: bytes
    sha256: str
    proposal: SemanticMaskProposal
    review_reasons: ReviewReasonSet

@dataclass(frozen=True, slots=True)
class CompiledMaskArtifact:
    evidence: CanonicalMaskEvidence
    png_bytes: bytes


@dataclass(frozen=True, slots=True)
class FormalCompilationResult:
    contract: CompiledBatchContract
    masks: tuple[CompiledMaskArtifact, ...]


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    epsilon = 1e-12
    return (
        min(a[0], b[0]) - epsilon <= p[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= p[1] <= max(a[1], b[1]) + epsilon
        and abs(_orientation(a, b, p)) <= epsilon
    )


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if ((o1 > 0 > o2) or (o1 < 0 < o2)) and ((o3 > 0 > o4) or (o3 < 0 < o4)):
        return True
    return any(
        (
            abs(value) <= 1e-12 and _on_segment(start, end, point)
            for value, start, end, point in (
                (o1, a, b, c),
                (o2, a, b, d),
                (o3, c, d, a),
                (o4, c, d, b),
            )
        )
    )


def _polygon_points(contour: SemanticMaskContour) -> list[tuple[float, float]]:
    points = [(point.x, point.y) for point in contour.points]
    if len(set(points)) != len(points):
        raise ValueError("semantic mask polygon contains duplicate vertices")
    area_twice = sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1], strict=True)
    )
    if abs(area_twice) <= 1e-8:
        raise ValueError("semantic mask polygon has zero area")
    edges = list(zip(points, points[1:] + points[:1], strict=True))
    for first_index, (a, b) in enumerate(edges):
        for second_index, (c, d) in enumerate(edges):
            if second_index <= first_index:
                continue
            if second_index in {first_index + 1, (first_index - 1) % len(edges)}:
                continue
            if first_index == 0 and second_index == len(edges) - 1:
                continue
            if _segments_intersect(a, b, c, d):
                raise ValueError("semantic mask polygon is self-intersecting")
    return points


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    inside = False
    x, y = point
    for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        if _on_segment(a, b, point):
            return True
        if (a[1] > y) != (b[1] > y):
            crossing_x = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < crossing_x:
                inside = not inside
    return inside


def _validate_exclusion_hole(
    exclusion: list[tuple[float, float]],
    includes: list[list[tuple[float, float]]],
) -> None:
    containing = next(
        (
            include
            for include in includes
            if all(_point_in_polygon(point, include) for point in exclusion)
        ),
        None,
    )
    if containing is None:
        raise ValueError("semantic mask exclusion must be a hole inside a matching include contour")
    for a, b in zip(exclusion, exclusion[1:] + exclusion[:1], strict=True):
        for c, d in zip(containing, containing[1:] + containing[:1], strict=True):
            if _segments_intersect(a, b, c, d):
                raise ValueError("semantic mask exclusion cannot touch or cross its include boundary")


def _pixel_polygon(points: list[tuple[float, float]], width: int, height: int) -> list[tuple[int, int]]:
    return [
        (round(x * (width - 1)), round(y * (height - 1)))
        for x, y in points
    ]


def _bbox_white_pixels(image: Image.Image, bbox: BoundingBox) -> int:
    left = max(0, bbox.x)
    top = max(0, bbox.y)
    right = min(image.width, bbox.x + bbox.width)
    bottom = min(image.height, bbox.y + bbox.height)
    if left >= right or top >= bottom:
        return 0
    return sum(
        1
        for value in image.crop((left, top, right, bottom)).get_flattened_data()
        if value
    )


def _load_source_size(source_path: Path) -> tuple[int, int]:
    with Image.open(source_path) as source:
        orientation = source.getexif().get(274)
        if orientation is not None and orientation != 1:
            raise ValueError("formal mask source has non-identity EXIF orientation")
        if "A" in source.getbands():
            raise ValueError("formal mask source must not have an alpha channel")
        if "transparency" in source.info:
            raise ValueError("formal mask source must not contain transparency")
        source.load()
        width, height = source.size
    if width <= 0 or height <= 0 or width > 32768 or height > 32768:
        raise ValueError("formal mask source dimensions are unsupported")
    return width, height


def validate_and_rasterize_semantic_mask(
    *,
    source_path: Path,
    proposal: SemanticMaskProposal,
    source_asset_id: str,
    source_asset_code: str,
    editable_faces: list[FormalMaskFaceScope],
    protected_faces: list[BoundingBox],
) -> ValidatedCanonicalMask:
    """Validate model geometry locally and encode the one authoritative binary L PNG."""
    if (proposal.source_asset_id, proposal.source_asset_code) != (
        source_asset_id,
        source_asset_code,
    ):
        raise ValueError("semantic mask proposal returned mismatched source identity")
    if len(proposal.contours) > MAX_CONTOURS:
        raise ValueError("semantic mask proposal exceeds contour limit")
    editable_by_face = {face.face_instance_id: face for face in editable_faces}
    if len(editable_by_face) != len(editable_faces):
        raise ValueError("formal mask editable face scope is not unique")
    if editable_faces and not proposal.contours:
        raise ValueError("semantic mask proposal omitted editable contours")
    if not editable_faces and proposal.contours:
        raise ValueError("semantic mask proposal expands an empty editable scope")

    polygons: list[tuple[SemanticMaskContour, list[tuple[float, float]]]] = []
    includes_by_face: dict[str, list[list[tuple[float, float]]]] = {}
    for contour in proposal.contours:
        if len(contour.points) > MAX_POINTS_PER_CONTOUR:
            raise ValueError("semantic mask contour exceeds point limit")
        scope = editable_by_face.get(contour.face_instance_id)
        if scope is None:
            raise ValueError("semantic mask contour expands beyond confirmed face mappings")
        if (
            contour.person_id != scope.person_id
            or contour.person_code != scope.person_code
            or contour.permissions != scope.permissions
        ):
            raise ValueError("semantic mask contour expands or alters confirmed mapping permissions")
        polygon = _polygon_points(contour)
        polygons.append((contour, polygon))
        if contour.operation == "include":
            includes_by_face.setdefault(contour.face_instance_id, []).append(polygon)

    for face_id in editable_by_face:
        if face_id not in includes_by_face:
            raise ValueError("semantic mask proposal must include every editable face")
    for contour, polygon in polygons:
        if contour.operation == "exclude":
            _validate_exclusion_hole(polygon, includes_by_face.get(contour.face_instance_id, []))

    width, height = _load_source_size(source_path)
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for contour, polygon in polygons:
        if contour.operation == "include":
            draw.polygon(_pixel_polygon(polygon, width, height), fill=255)
    include_mask = mask.copy()
    for contour, polygon in polygons:
        if contour.operation == "exclude":
            draw.polygon(_pixel_polygon(polygon, width, height), fill=0)
    proposed_pixels = sum(1 for value in mask.get_flattened_data() if value)
    canvas_pixels = width * height
    if proposed_pixels > int(canvas_pixels * MAX_EDITABLE_CANVAS_FRACTION):
        raise ValueError("semantic mask authorizes an oversized canvas area")
    if any(_bbox_white_pixels(mask, bbox) for bbox in protected_faces):
        raise ValueError("semantic mask authorizes protected-face pixels")
    face_envelope = Image.new("L", (width, height), 0)
    envelope_draw = ImageDraw.Draw(face_envelope)
    for scope in editable_faces:
        envelope_draw.rectangle(
            (
                scope.bbox.x,
                scope.bbox.y,
                scope.bbox.x + scope.bbox.width - 1,
                scope.bbox.y + scope.bbox.height - 1,
            ),
            fill=255,
        )
    mask = ImageChops.multiply(mask, face_envelope)
    include_mask = ImageChops.multiply(include_mask, face_envelope)

    editable_pixels = sum(1 for value in mask.get_flattened_data() if value)
    if editable_faces and editable_pixels == 0:
        raise ValueError("semantic mask raster is empty")
    for scope in editable_faces:
        center_x = min(width - 1, max(0, scope.bbox.x + scope.bbox.width // 2))
        center_y = min(height - 1, max(0, scope.bbox.y + scope.bbox.height // 2))
        if include_mask.getpixel((center_x, center_y)) == 0:
            raise ValueError(
                "semantic mask include contour must authorize each editable face center"
            )
        overlap = _bbox_white_pixels(mask, scope.bbox)
        required = max(1, int(scope.bbox.width * scope.bbox.height * MIN_FACE_OVERLAP_FRACTION))
        if overlap < required:
            raise ValueError("semantic mask has insufficient overlap with an editable face")

    output = BytesIO()
    mask.save(output, format="PNG", optimize=False, compress_level=9)
    png_bytes = output.getvalue()
    digest = hashlib.sha256(png_bytes).hexdigest()
    reasons: ReviewReasonSet = (
        frozenset({ReviewReason.MASK_UNCERTAIN})
        if proposal.confidence < MASK_CONFIDENCE_THRESHOLD
        or any(
            contour.confidence < CONTOUR_CONFIDENCE_THRESHOLD
            for contour in proposal.contours
        )
        else frozenset()
    )
    return ValidatedCanonicalMask(
        width=width,
        height=height,
        png_bytes=png_bytes,
        sha256=digest,
        proposal=proposal,
        review_reasons=reasons,
    )
