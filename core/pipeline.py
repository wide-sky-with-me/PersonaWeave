"""
PersonaWeave Core Processing Pipeline (PersonaWeavePipeline).
Coordinates the complete end-to-end face swapping, identification matching,
multi-reference pose-adaptive selection, protected masking, and alpha compositing.
"""

import os
import time
from dataclasses import dataclass, field
from threading import Lock

import cv2
import numpy as np

from core.color_matcher import reinhard_color_transfer
from core.detector import DetectedFace, FaceDetector
from core.face_assignment import resolve_person_mapping_assignment
from core.face_metrics import (
    calculate_reference_quality,
    calculate_yaw_ratio,
    has_visible_face_change,
    incompatible_extreme_pose,
)
from core.face_parser import DenseFaceParser
from core.model_router import RoutingDecision, SwapperModelRouter
from core.occluder import FaceOccluder
from core.recognizer import FaceRecognizer
from core.region_parser import RegionFaceParser
from core.swapper import (
    GHOST_1_256_SPEC,
    HYPERSWAP_1C_256_SPEC,
    INSWAPPER_128_SPEC,
    create_face_swapper,
    create_facefusion_box_mask,
    explode_pixel_boost,
    implode_pixel_boost,
)
from utils.gpu_helper import get_optimal_providers


@dataclass
class SwapResult:
    """Output summary of a processed image."""

    output_image: np.ndarray
    faces_detected: int
    faces_swapped: int
    matched_similarities: list[float]
    elapsed_time_sec: float
    model_routing: list[dict] = field(default_factory=list)
    swapped_person_indices: list[int] = field(default_factory=list)
    requested_person_indices: list[int] = field(default_factory=list)
    person_outcomes: list[dict] = field(default_factory=list)


class PersonaWeavePipeline:
    """Industrial-grade batch targeted face swapping pipeline."""

    def __init__(
        self,
        models_dir: str | None = None,
        providers: list[str] | None = None,
        detection_threshold: float = 0.50,
        swapper_model: str = "inswapper_128",
    ):
        """
        Initialize the complete pipeline models.

        Args:
            models_dir: Directory where ONNX models are stored.
            providers: ONNX execution providers list.
            detection_threshold: Confidence threshold for SCRFD face detector.
            swapper_model: Stable swapper adapter name, such as inswapper_128 or hyperswap_1c_256.
        """
        if models_dir is None:
            models_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

        if providers is None:
            providers = get_optimal_providers()

        self.models_dir = models_dir
        self.providers = providers

        # Path to models
        detector_path = os.path.join(models_dir, "buffalo_l", "det_10g.onnx")
        if not os.path.exists(detector_path):
            detector_path = os.path.join(models_dir, "buffalo_l", "scrfd_10g_bnkps.onnx")
        recognizer_path = os.path.join(models_dir, "buffalo_l", "w600k_r50.onnx")

        print(f"Initializing PersonaWeave Pipeline (Execution Providers: {self.providers})...")
        self.detector = FaceDetector(detector_path, providers=self.providers, confidence_threshold=detection_threshold)
        self.recognizer = FaceRecognizer(recognizer_path, providers=self.providers)
        self.swapper = create_face_swapper(swapper_model, models_dir, providers=self.providers)
        self.swapper_model = swapper_model
        self._swapper_cache = {swapper_model: self.swapper}
        self._swapper_lock = Lock()
        available_models = {
            spec.name
            for spec in (INSWAPPER_128_SPEC, HYPERSWAP_1C_256_SPEC, GHOST_1_256_SPEC)
            if os.path.exists(os.path.join(models_dir, spec.model_filename))
        }
        if not os.path.exists(os.path.join(models_dir, "crossface_ghost.onnx")):
            available_models.discard(GHOST_1_256_SPEC.name)
        self.model_router = SwapperModelRouter(available_models)
        parser_path = os.path.join(models_dir, "buffalo_l", "2d106det.onnx")
        if os.path.exists(parser_path):
            self.parser = DenseFaceParser(parser_path, providers=self.providers)
        else:
            self.parser = None
        occluder_path = os.path.join(models_dir, "xseg_1.onnx")
        if os.path.exists(occluder_path):
            try:
                self.occluder = FaceOccluder(occluder_path, providers=self.providers)
            except Exception as exc:
                print(f"[Warning] Failed to load XSeg model: {exc}")
                self.occluder = None
        else:
            self.occluder = None
        region_parser_path = os.path.join(models_dir, "bisenet_resnet_34.onnx")
        if os.path.exists(region_parser_path):
            try:
                self.region_parser = RegionFaceParser(region_parser_path, providers=self.providers)
            except Exception as exc:
                print(f"[Warning] Failed to load BiSeNet region parser: {exc}")
                self.region_parser = None
        else:
            self.region_parser = None
        print("PersonaWeave Pipeline initialized successfully.")

    def get_swapper(self, model_name: str):
        """Return a cached adapter, loading an available model at most once."""
        with self._swapper_lock:
            swapper = self._swapper_cache.get(model_name)
            if swapper is None:
                swapper = create_face_swapper(model_name, self.models_dir, providers=self.providers)
                self._swapper_cache[model_name] = swapper
            return swapper

    def route_swapper(
        self,
        target_face: DetectedFace,
        requested_model: str,
        allow_research_models: bool,
    ) -> RoutingDecision:
        """Select a backend from measurable face properties and policy flags."""
        width = float(target_face.bbox[2] - target_face.bbox[0])
        height = float(target_face.bbox[3] - target_face.bbox[1])
        confidence = target_face.similarity if target_face.similarity > 0 else target_face.det_score
        return self.model_router.select(
            requested_model,
            face_size=max(width, height),
            yaw_ratio=calculate_yaw_ratio(target_face.kps),
            identity_confidence=float(confidence),
            allow_research_models=allow_research_models,
        )

    def load_source_references(self, image_paths: list[str]) -> list[dict]:
        """
        Loads multiple reference photos, extracts their face landmarks, ArcFace embeddings, and head poses.

        Args:
            image_paths: List of file paths to source identity photos.

        Returns:
            List of dicts with keys: 'path', 'image', 'face', 'embedding', 'yaw'.
        """
        references = []
        for path in image_paths:
            if not os.path.exists(path):
                print(f"[Warning] Source image not found: {path}")
                continue
            img = cv2.imread(path)
            if img is None:
                print(f"[Warning] Failed to load image: {path}")
                continue

            faces = self.detector.detect_faces(img, max_num=1, confidence_threshold=0.25)
            if not faces:
                print(f"[Warning] No face detected in reference image: {path}")
                continue

            emb = self.recognizer.extract_embedding(img, faces[0])
            references.append(
                {
                    "path": path,
                    "image": img,
                    "face": faces[0],
                    "embedding": emb,
                    "yaw": calculate_yaw_ratio(faces[0].kps),
                    "quality_score": calculate_reference_quality(img, faces[0]),
                }
            )

        if not references:
            raise ValueError("No valid faces could be extracted from the provided reference photos.")

        return references

    def create_identity_gallery(self, image_paths: list[str]) -> np.ndarray:
        """
        Builds a single fused identity anchor embedding from multiple reference images.
        """
        refs = self.load_source_references(image_paths)
        embs = [r["embedding"] for r in refs]
        weights = [r.get("quality_score", 0.5) for r in refs]
        return self.recognizer.build_gallery_embedding(embs, weights)

    def extract_target_reference_embedding(self, image_path: str) -> np.ndarray:
        """
        Extracts the identity embedding of a specific target person to be replaced.
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Target reference image not found: {image_path}")

        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Failed to decode image from: {image_path}")

        faces = self.detector.detect_faces(img, max_num=1, confidence_threshold=0.25)
        if not faces:
            raise ValueError(f"No face detected in target reference image: {image_path}")

        return self.recognizer.extract_embedding(img, faces[0])

    def select_best_reference_embedding(
        self,
        target_face: DetectedFace,
        source_references: list[dict],
        gallery_embedding: np.ndarray,
        mode: str = "best-match",
    ) -> np.ndarray:
        """Select a quality-weighted identity embedding with pose compatibility."""
        qualities = np.asarray([float(ref.get("quality_score", 0.5)) for ref in source_references], dtype=np.float32)
        qualities = np.clip(qualities, 0.05, 1.0)
        if mode == "gallery" or len(source_references) == 1:
            return gallery_embedding

        if mode == "best-match":
            target_yaw = calculate_yaw_ratio(target_face.kps)
            scored = []
            for ref, quality in zip(source_references, qualities, strict=True):
                pose_compatibility = max(0.0, 1.0 - abs(target_yaw - float(ref.get("yaw", 0.0))) / 0.8)
                scored.append((0.7 * float(quality) + 0.3 * pose_compatibility, ref))
            best_reference = max(scored, key=lambda item: item[0])[1]
            best_embedding = best_reference["embedding"]
            gallery_weight = 0.80
            blended_embedding = gallery_weight * gallery_embedding + (1.0 - gallery_weight) * best_embedding
            return blended_embedding / max(np.linalg.norm(blended_embedding), 1e-6)

        return gallery_embedding

    def swap_single_face_facefusion(
        self,
        canvas: np.ndarray,
        target_face: DetectedFace,
        source_embedding: np.ndarray,
        *,
        swapper,
        enable_dense_mask: bool,
        manual_mask: np.ndarray | None,
        identity_blend_strength: float = 0.72,
        enable_color_transfer: bool = True,
        color_transfer_mode: str = "boundary",
    ) -> np.ndarray:
        """Swap one face using FaceFusion-style alignment, pixel boost, mask intersection, and alpha paste-back."""
        model_width, model_height = swapper.spec.input_size
        face_size = max(
            float(target_face.bbox[2] - target_face.bbox[0]),
            float(target_face.bbox[3] - target_face.bbox[1]),
        )
        # Interleaved pixel boost is useful up to a 256px working crop, but pushing a
        # 128px model to 512px means four independent tiles per axis. Real-image
        # regressions showed that the seams between those tiles suppress facial
        # high-frequency detail on already-large faces. Keep one 256px working
        # crop for medium/large faces and use the model's native size for small
        # faces; 256px models therefore never need interleaved boosting.
        boost_total = max(1, 256 // model_width) if face_size >= 96 else 1
        boost_size = (model_width * boost_total, model_height * boost_total)

        if swapper.spec.crop_template in {"inswapper_128", "arcface_128"}:
            destination = np.array(
                [
                    [0.36167656, 0.40387734],
                    [0.63696719, 0.40235469],
                    [0.50019687, 0.56044219],
                    [0.38710391, 0.72160547],
                    [0.61507734, 0.72034453],
                ],
                dtype=np.float32,
            )
        else:
            destination = np.array(
                [
                    [0.35473214, 0.45658929],
                    [0.64526786, 0.45658929],
                    [0.5, 0.61154464],
                    [0.37913393, 0.776875],
                    [0.62086607, 0.776875],
                ],
                dtype=np.float32,
            )
        destination *= np.asarray(boost_size, dtype=np.float32)
        affine_matrix = cv2.estimateAffinePartial2D(
            target_face.kps.astype(np.float32), destination, method=cv2.RANSAC, ransacReprojThreshold=100
        )[0]
        if affine_matrix is None:
            raise ValueError("Could not estimate canonical face alignment")
        # INTER_AREA is only well-defined for shrinking; per OpenCV's own documented behavior it
        # degrades to near-INTER_NEAREST when the destination is larger than the source content,
        # which is exactly what pixel-boost enlargement does for small/medium faces. Choose the
        # interpolation from the transform's actual scale factor instead of hardcoding a
        # downscale-only flag.
        transform_scale = float(np.sqrt(abs(np.linalg.det(affine_matrix[:, :2]))))
        crop_interpolation = cv2.INTER_AREA if transform_scale <= 1.0 else cv2.INTER_CUBIC
        crop = cv2.warpAffine(
            canvas, affine_matrix, boost_size, flags=crop_interpolation, borderMode=cv2.BORDER_REPLICATE
        )

        outputs = []
        model_masks = []
        for tile in implode_pixel_boost(crop, boost_total, swapper.spec.input_size):
            inferred = swapper.infer_aligned(tile, source_embedding)
            if isinstance(inferred, tuple):
                outputs.append(inferred[0])
                model_masks.append(np.asarray(inferred[1], dtype=np.float32))
            else:
                outputs.append(inferred)
        swapped_crop = explode_pixel_boost(outputs, boost_total, swapper.spec.input_size)

        masks = [create_facefusion_box_mask(boost_size, blur=0.3)]
        if model_masks:
            # HyperSwap is a native 256px model, so its learned face mask and the aligned
            # crop share one tile. Intersecting it prevents the generic box mask from
            # pasting hallucinated profile pixels outside the model's trusted region.
            model_mask = model_masks[0]
            if model_mask.ndim == 3:
                model_mask = np.squeeze(model_mask)
            masks.append(cv2.resize(model_mask, boost_size, interpolation=cv2.INTER_LINEAR).clip(0.0, 1.0))
        if enable_dense_mask and self.parser is not None:
            try:
                landmarks = self.parser.extract_106_landmarks(canvas, target_face)
                aligned = cv2.transform(landmarks.reshape(-1, 1, 2), affine_matrix).reshape(-1, 2)
                aligned_512 = aligned * np.asarray([512 / boost_size[0], 512 / boost_size[1]], dtype=np.float32)
                anatomical = self.parser.generate_anatomical_mask_512(aligned_512)
                masks.append(cv2.resize(anatomical, boost_size, interpolation=cv2.INTER_LINEAR))
            except Exception as exc:
                print(f"[Warning] Dense face mask failed; using box mask: {exc}")
        if self.occluder is not None:
            try:
                masks.append(self.occluder.create_occlusion_mask(crop))
            except Exception as exc:
                print(f"[Warning] Occlusion mask failed; using remaining masks: {exc}")
        if self.region_parser is not None:
            try:
                # XSeg only catches foreign objects (hands, props); it does not know "these
                # pixels are eyeglasses" as a semantic class, so glasses routinely warp/melt
                # during a swap with no protection at all. BiSeNet semantic parsing closes that
                # gap by explicitly protecting the glasses region.
                masks.append(self.region_parser.create_region_protection_mask(crop, protected_regions=("glasses",)))
            except Exception as exc:
                print(f"[Warning] Region protection mask failed; using remaining masks: {exc}")
        crop_mask = np.minimum.reduce(masks).clip(0.0, 1.0)

        # Boundary-ring color correction: only the narrow transition ring at the mask edge is
        # nudged toward the original crop's LAB statistics; the swapped face's own interior tone
        # is never touched. This targets seam/exposure-mismatch discoloration between the source
        # identity photo and the target scene without risking the whole-face skin-tone/identity
        # drift a full-face color transfer would cause -- keeping overall color and saturation
        # intact is a hard requirement here, not just a nice-to-have.
        if enable_color_transfer and color_transfer_mode != "off":
            try:
                # Keep color transfer confined to a narrow, low-opacity edge ring.
                # A wider/high-opacity ring made the transition darker and less
                # saturated than both the generated face and target skin in real
                # 8-25 regressions. The interior remains byte-for-byte untouched
                # by color transfer.
                harmonized_crop = reinhard_color_transfer(swapped_crop, crop, blend_ratio=0.45, face_mask=crop_mask)
                erosion_size = max(3, round(boost_size[0] * 15 / 512))
                inner_mask = cv2.erode(crop_mask, np.ones((erosion_size, erosion_size), dtype=np.uint8))
                ring_mask = np.clip(crop_mask - inner_mask, 0.0, 1.0)
                ring_sigma = max(1.0, boost_size[0] * 2.5 / 512.0)
                ring_mask = cv2.GaussianBlur(ring_mask, (0, 0), ring_sigma)
                ring_mask = np.minimum(ring_mask, 0.45)[:, :, np.newaxis]
                swapped_crop = np.clip(
                    ring_mask * harmonized_crop.astype(np.float32)
                    + (1.0 - ring_mask) * swapped_crop.astype(np.float32),
                    0,
                    255,
                ).astype(np.uint8)
            except Exception as exc:
                print(f"[Warning] Boundary color correction failed; using unmodified swap: {exc}")

        inverse = cv2.invertAffineTransform(affine_matrix)
        height, width = canvas.shape[:2]
        warped_face = cv2.warpAffine(
            swapped_crop, inverse, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        warped_mask = cv2.warpAffine(crop_mask, inverse, (width, height), flags=cv2.INTER_LINEAR).clip(0.0, 1.0)
        # InSwapper needs enough alpha to make the requested identity visible. Faces at
        # or above the 96px pixel-boost threshold have a 256px working crop and can safely
        # use a stronger composite. Only genuinely tiny native-resolution faces retain a
        # more conservative blend; the former blanket 0.18 alpha for every face below
        # 320px made ordinary group portraits effectively no-op.
        if swapper.spec.name == "inswapper_128":
            yaw = abs(calculate_yaw_ratio(target_face.kps))
            blend_strength = float(np.clip(identity_blend_strength, 0.0, 1.0))
            if face_size < 96:
                blend_strength *= 0.45 / 0.72
            elif yaw > 0.55:
                blend_strength *= 0.65 / 0.72
            warped_mask *= min(blend_strength, 1.0)
        if manual_mask is not None:
            protection = manual_mask
            if protection.shape[:2] != (height, width):
                protection = cv2.resize(protection, (width, height), interpolation=cv2.INTER_LINEAR)
            if protection.ndim == 3:
                protection = protection[:, :, 0]
            protection = protection.astype(np.float32)
            if protection.max() > 1.0:
                protection /= 255.0
            warped_mask *= 1.0 - protection.clip(0.0, 1.0)
        alpha = warped_mask[:, :, np.newaxis]
        result = canvas.astype(np.float32) * (1.0 - alpha) + warped_face.astype(np.float32) * alpha
        return np.clip(result, 0, 255).astype(np.uint8)

    def process_image(
        self,
        image: np.ndarray,
        source_references: list[dict],
        gallery_embedding: np.ndarray,
        target_embedding: np.ndarray | None = None,
        similarity_threshold: float = 0.35,
        identity_blend_strength: float = 0.72,
        source_mode: str = "best-match",
        enable_color_transfer: bool = True,
        color_transfer_mode: str = "boundary",
        enable_dense_mask: bool = True,
        swapper_model: str | None = None,
        allow_research_models: bool = False,
        manual_mask: np.ndarray | None = None,
    ) -> SwapResult:
        """
        Processes a target image with selective face swapping.

        Args:
            image: Full original BGR image.
            source_references: List of source reference photo dicts.
            gallery_embedding: 512-d fused identity embedding.
            target_embedding: Target person embedding to filter matching faces (if None, swaps all).
            similarity_threshold: Threshold for identity matching.
            identity_blend_strength: Visible source-identity strength for InSwapper compositing.
            source_mode: 'best-match' or 'gallery'.
            enable_color_transfer: Whether to apply LAB lighting and skin tone harmonization.
            enable_dense_mask: Whether to use 106-point dense anatomical masking.
            manual_mask: Optional manual inpaint mask array.

        Returns:
            SwapResult object.
        """
        swapper_model = swapper_model or self.swapper_model
        t0 = time.time()
        canvas = image.copy()

        # 1. Detect all faces
        detected_faces = self.detector.detect_faces(canvas)
        if not detected_faces:
            return SwapResult(
                output_image=canvas,
                faces_detected=0,
                faces_swapped=0,
                matched_similarities=[],
                elapsed_time_sec=time.time() - t0,
            )

        # 2. Match faces
        faces_to_swap: list[tuple[DetectedFace, float]] = []

        if target_embedding is not None:
            for face in detected_faces:
                self.recognizer.extract_embedding(canvas, face)
                sim = self.recognizer.compute_similarity(face.embedding, target_embedding)
                face.similarity = sim
                if sim >= similarity_threshold:
                    faces_to_swap.append((face, sim))
        else:
            for face in detected_faces:
                self.recognizer.extract_embedding(canvas, face)
            faces_to_swap = [(face, 1.0) for face in detected_faces]

        # 3. Route and swap each matched face independently.
        matched_similarities = []
        model_routing = []
        spatial_indices = {
            id(face): index
            for index, face in enumerate(sorted(detected_faces, key=lambda candidate: candidate.bbox[0]), start=1)
        }
        swapped_person_indices = []
        for face, similarity in faces_to_swap:
            src_emb = self.select_best_reference_embedding(
                target_face=face,
                source_references=source_references,
                gallery_embedding=gallery_embedding,
                mode=source_mode,
            )
            decision = self.route_swapper(face, swapper_model, allow_research_models)
            routed_swapper = self.get_swapper(decision.selected)
            model_routing.append(
                {
                    "requested": decision.requested,
                    "selected": decision.selected,
                    "reason": decision.reason,
                    "fallback": decision.fallback,
                }
            )
            candidate = self.swap_single_face_facefusion(
                canvas=canvas,
                target_face=face,
                source_embedding=src_emb,
                swapper=routed_swapper,
                enable_dense_mask=enable_dense_mask,
                manual_mask=manual_mask,
                identity_blend_strength=identity_blend_strength,
                enable_color_transfer=enable_color_transfer,
                color_transfer_mode=color_transfer_mode,
            )
            if has_visible_face_change(canvas, candidate, face.bbox):
                canvas = candidate
                matched_similarities.append(similarity)
                swapped_person_indices.append(spatial_indices[id(face)])

        elapsed = time.time() - t0
        return SwapResult(
            output_image=canvas,
            faces_detected=len(detected_faces),
            faces_swapped=len(matched_similarities),
            matched_similarities=matched_similarities,
            elapsed_time_sec=elapsed,
            model_routing=model_routing,
            swapped_person_indices=swapped_person_indices,
        )

    def process_image_multi_mapping(
        self,
        image: np.ndarray,
        mappings: list[dict],
        identity_blend_strength: float = 0.72,
        enable_color_transfer: bool = True,
        color_transfer_mode: str = "boundary",
        enable_dense_mask: bool = True,
        swapper_model: str | None = None,
        allow_research_models: bool = False,
        manual_mask: np.ndarray | None = None,
    ) -> SwapResult:
        """
        Processes a group image where multiple detected faces are mapped to different source identities.

        Each task-level mapping carries a stable target identity embedding. The matching
        face is resolved independently in every image rather than reusing a positional
        index that only belongs to one photo. ``identity_blend_strength`` controls the
        visible InSwapper identity contribution while preserving pose-specific safety scaling.
        """
        swapper_model = swapper_model or self.swapper_model
        t0 = time.time()
        canvas = image.copy()

        detected_faces = self.detector.detect_faces(canvas)
        requested_person_indices = [int(mapping["target_index"]) for mapping in mappings]
        if not detected_faces:
            return SwapResult(
                output_image=canvas,
                faces_detected=0,
                faces_swapped=0,
                matched_similarities=[],
                elapsed_time_sec=time.time() - t0,
                requested_person_indices=requested_person_indices,
                person_outcomes=[
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": "no_faces_detected",
                    }
                    for person_index in requested_person_indices
                ],
            )

        detected_faces.sort(key=lambda f: f.bbox[0])
        for f_i, face in enumerate(detected_faces):
            face.index = f_i + 1
            self.recognizer.extract_embedding(canvas, face)

        # Resolve every mapping's target face with one globally optimal one-to-one assignment
        # instead of a greedy per-mapping pick (see resolve_person_mapping_assignment).
        valid_mappings = [(i, m) for i, m in enumerate(mappings) if m.get("target_embedding") is not None]
        assignment: dict[int, tuple[int, float]] = {}
        if valid_mappings and detected_faces:
            similarity_matrix = np.array(
                [
                    [
                        self.recognizer.compute_similarity(face.embedding, mapping["target_embedding"])
                        for face in detected_faces
                    ]
                    for _, mapping in valid_mappings
                ],
                dtype=np.float32,
            )
            thresholds = [mapping.get("similarity_threshold", 0.35) for _, mapping in valid_mappings]
            local_assignment = resolve_person_mapping_assignment(similarity_matrix, thresholds)
            assignment = {valid_mappings[local_row][0]: pair for local_row, pair in local_assignment.items()}

        swapped_count = 0
        matched_similarities = []
        model_routing = []
        swapped_person_indices = []
        person_outcomes = []
        for mapping_index, mapping in enumerate(mappings):
            person_index = int(mapping["target_index"])
            match = assignment.get(mapping_index)
            if match is None:
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": "identity_match_below_threshold",
                    }
                )
                continue
            face_index, similarity = match
            face = detected_faces[face_index]
            src_refs = mapping.get("source_references", [])
            gal_emb = mapping.get("gallery_embedding")
            if gal_emb is None and src_refs:
                gal_emb = self.recognizer.build_gallery_embedding([r["embedding"] for r in src_refs])
            if gal_emb is None:
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": "source_identity_unavailable",
                    }
                )
                continue

            pose_incompatible, closest_pose_gap = incompatible_extreme_pose(face, src_refs)
            if pose_incompatible:
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": "extreme_pose_without_matching_reference",
                        "target_yaw": round(calculate_yaw_ratio(face.kps), 4),
                        "closest_source_yaw_gap": None if closest_pose_gap is None else round(closest_pose_gap, 4),
                    }
                )
                continue

            src_mode = mapping.get("source_mode", "best-match")
            src_emb = self.select_best_reference_embedding(
                target_face=face,
                source_references=src_refs,
                gallery_embedding=gal_emb,
                mode=src_mode,
            )
            decision = self.route_swapper(face, swapper_model, allow_research_models)
            routed_swapper = self.get_swapper(decision.selected)
            model_routing.append(
                {
                    "requested": decision.requested,
                    "selected": decision.selected,
                    "reason": decision.reason,
                    "fallback": decision.fallback,
                }
            )
            candidate = self.swap_single_face_facefusion(
                canvas=canvas,
                target_face=face,
                source_embedding=src_emb,
                swapper=routed_swapper,
                enable_dense_mask=enable_dense_mask,
                manual_mask=manual_mask,
                identity_blend_strength=identity_blend_strength,
                enable_color_transfer=enable_color_transfer,
                color_transfer_mode=color_transfer_mode,
            )
            if has_visible_face_change(canvas, candidate, face.bbox):
                canvas = candidate
                swapped_count += 1
                matched_similarities.append(float(similarity))
                swapped_person_indices.append(person_index)
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SWAPPED",
                        "reason": "visible_change_confirmed",
                        "model": decision.selected,
                    }
                )
            else:
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": "no_visible_change",
                        "model": decision.selected,
                    }
                )

        elapsed = time.time() - t0
        return SwapResult(
            output_image=canvas,
            faces_detected=len(detected_faces),
            faces_swapped=swapped_count,
            matched_similarities=matched_similarities,
            elapsed_time_sec=elapsed,
            model_routing=model_routing,
            swapped_person_indices=swapped_person_indices,
            requested_person_indices=requested_person_indices,
            person_outcomes=person_outcomes,
        )
