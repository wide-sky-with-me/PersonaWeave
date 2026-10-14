"""
PersonaWeave Core Processing Pipeline (PersonaWeavePipeline).
Coordinates the complete end-to-end face swapping, identification matching,
multi-reference pose-adaptive selection, protected masking, and alpha compositing.
"""

import os
import time
from dataclasses import dataclass, field
from threading import Lock

import numpy as np

from core.detector import DetectedFace, FaceDetector
from core.face_assignment import resolve_person_mapping_assignment
from core.face_execution import composite_in_rows as composite_in_rows
from core.face_execution import swap_single_face_facefusion as execute_single_face
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
from core.restorer import CodeFormerRestorer
from core.swap_diagnostics import select_source_embedding
from core.swapper import GHOST_1_256_SPEC, HYPERSWAP_1C_256_SPEC, INSWAPPER_128_SPEC, create_face_swapper
from utils.gpu_helper import get_optimal_providers
from utils.image_io import read_image


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
        restorer_path = os.path.join(models_dir, CodeFormerRestorer.MODEL_FILENAME)
        if os.path.exists(restorer_path):
            try:
                self.restorer = CodeFormerRestorer(restorer_path, providers=self.providers)
            except Exception as exc:
                print(f"[Warning] Failed to load CodeFormer restoration model: {exc}")
                self.restorer = None
        else:
            self.restorer = None
        print("PersonaWeave Pipeline initialized successfully.")

    def execution_providers(self) -> dict[str, list[str]]:
        """Report loaded session providers, including CPU fallback; not a kernel profiler."""
        sessions = {
            "detector": self.detector.model.session,
            "recognizer": self.recognizer.model.session,
        }
        if self.parser is not None:
            sessions["landmarks"] = self.parser.model.session
        for name, component in (
            ("occlusion", self.occluder),
            ("regions", self.region_parser),
            ("restoration", self.restorer),
        ):
            if component is not None:
                sessions[name] = component.session
        with self._swapper_lock:
            for name, swapper in self._swapper_cache.items():
                sessions[name] = swapper.session
                if name == GHOST_1_256_SPEC.name:
                    sessions["crossface_ghost"] = swapper.converter
        return {name: session.get_providers() for name, session in sessions.items()}

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
            Small reference feature dictionaries; decoded source pixels are released after extraction.
        """
        references = []
        for path in image_paths:
            if not os.path.exists(path):
                print(f"[Warning] Source image not found: {path}")
                continue
            img = read_image(path)
            if img is None:
                print(f"[Warning] Failed to load image: {path}")
                continue

            faces = self.detector.detect_faces(img, max_num=1, confidence_threshold=0.25)
            if not faces:
                print(f"[Warning] No face detected in reference image: {path}")
                del img
                continue

            emb = self.recognizer.extract_embedding(img, faces[0])
            references.append(
                {
                    "path": path,
                    "face": faces[0],
                    "embedding": emb,
                    "yaw": calculate_yaw_ratio(faces[0].kps),
                    "quality_score": calculate_reference_quality(img, faces[0]),
                }
            )

            del img

        if not references:
            raise ValueError("No valid faces could be extracted from the provided reference photos.")

        return references

    def extract_target_reference_embedding(self, image_path: str) -> np.ndarray:
        """
        Extracts the identity embedding of a specific target person to be replaced.
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Target reference image not found: {image_path}")

        img = read_image(image_path)
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
        *,
        gallery_reference_weights: list[float] | None = None,
        diagnostics: dict | None = None,
    ) -> np.ndarray:
        """Keep ndarray output while optionally reporting the actual source recipe."""
        embedding, recipe = select_source_embedding(
            calculate_yaw_ratio(target_face.kps),
            source_references,
            gallery_embedding,
            mode,
            gallery_reference_weights,
        )
        if diagnostics is not None:
            diagnostics.update(recipe)
        return embedding

    def swap_single_face_facefusion(
        self,
        canvas: np.ndarray,
        target_face: DetectedFace,
        source_embedding: np.ndarray,
        *,
        swapper,
        enable_dense_mask: bool,
        manual_mask: np.ndarray | None,
        identity_blend_strength: float = 0.9,
        enable_color_transfer: bool = True,
        color_transfer_mode: str = "boundary",
        color_transfer_ring_strength: float = 0.45,
        color_transfer_ring_width_px: float = 15.0,
        enable_face_restoration: bool = False,
        restoration_fidelity_weight: float = 0.75,
        restoration_strength: float = 0.5,
        diagnostics: dict | None = None,
    ) -> np.ndarray:
        """Execute one protected aligned swap while preserving the pipeline's public override boundary."""
        return execute_single_face(
            canvas,
            target_face,
            source_embedding,
            swapper=swapper,
            enable_dense_mask=enable_dense_mask,
            manual_mask=manual_mask,
            identity_blend_strength=identity_blend_strength,
            enable_color_transfer=enable_color_transfer,
            color_transfer_mode=color_transfer_mode,
            color_transfer_ring_strength=color_transfer_ring_strength,
            color_transfer_ring_width_px=color_transfer_ring_width_px,
            enable_face_restoration=enable_face_restoration,
            restoration_fidelity_weight=restoration_fidelity_weight,
            restoration_strength=restoration_strength,
            diagnostics=diagnostics,
            parser=self.parser if enable_dense_mask else None,
            occluder=self.occluder,
            region_parser=self.region_parser,
            restorer=self.restorer if enable_face_restoration else None,
        )

    def process_image(
        self,
        image: np.ndarray,
        source_references: list[dict],
        gallery_embedding: np.ndarray,
        target_embedding: np.ndarray | None = None,
        similarity_threshold: float = 0.35,
        identity_blend_strength: float = 0.9,
        source_mode: str = "best-match",
        enable_color_transfer: bool = True,
        color_transfer_mode: str = "boundary",
        color_transfer_ring_strength: float = 0.45,
        color_transfer_ring_width_px: float = 15.0,
        enable_dense_mask: bool = True,
        swapper_model: str | None = None,
        allow_research_models: bool = False,
        manual_mask: np.ndarray | None = None,
        enable_face_restoration: bool = False,
        restoration_fidelity_weight: float = 0.75,
        restoration_strength: float = 0.5,
        gallery_reference_weights: list[float] | None = None,
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
        person_outcomes = []
        spatial_indices = {
            id(face): index
            for index, face in enumerate(sorted(detected_faces, key=lambda candidate: candidate.bbox[0]), start=1)
        }

        if target_embedding is not None:
            for face in detected_faces:
                self.recognizer.extract_embedding(canvas, face)
                sim = self.recognizer.compute_similarity(face.embedding, target_embedding)
                face.similarity = sim
                if sim >= similarity_threshold:
                    faces_to_swap.append((face, sim))
            if not faces_to_swap:
                best_face = max(detected_faces, key=lambda candidate: candidate.similarity)
                person_outcomes.append(
                    {
                        "person_index": spatial_indices[id(best_face)],
                        "status": "SKIPPED",
                        "reason": "identity_match_below_threshold",
                        "diagnostics": {
                            "matching": {
                                "best_similarity": float(best_face.similarity),
                                "assigned_similarity": None,
                                "threshold": float(similarity_threshold),
                                "face_index": None,
                            }
                        },
                    }
                )
        else:
            for face in detected_faces:
                self.recognizer.extract_embedding(canvas, face)
            faces_to_swap = [(face, 1.0) for face in detected_faces]

        # 3. Route and swap each matched face independently.
        matched_similarities = []
        model_routing = []
        swapped_person_indices = []
        for face, similarity in faces_to_swap:
            diagnostics = {
                "matching": {
                    "best_similarity": float(similarity) if target_embedding is not None else None,
                    "assigned_similarity": float(similarity) if target_embedding is not None else None,
                    "threshold": float(similarity_threshold) if target_embedding is not None else None,
                    "face_index": spatial_indices[id(face)],
                },
                "source_selection": {},
                "mask": {},
            }
            src_emb = self.select_best_reference_embedding(
                target_face=face,
                source_references=source_references,
                gallery_embedding=gallery_embedding,
                mode=source_mode,
                gallery_reference_weights=gallery_reference_weights,
                diagnostics=diagnostics["source_selection"],
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
                color_transfer_ring_strength=color_transfer_ring_strength,
                color_transfer_ring_width_px=color_transfer_ring_width_px,
                enable_face_restoration=enable_face_restoration,
                restoration_fidelity_weight=restoration_fidelity_weight,
                restoration_strength=restoration_strength,
                diagnostics=diagnostics["mask"],
            )
            if has_visible_face_change(canvas, candidate, face.bbox):
                canvas = candidate
                matched_similarities.append(similarity)
                swapped_person_indices.append(spatial_indices[id(face)])
                status, reason = "SWAPPED", "visible_change_confirmed"
            else:
                status, reason = "SKIPPED", "no_visible_change"
            person_outcomes.append(
                {
                    "person_index": spatial_indices[id(face)],
                    "status": status,
                    "reason": reason,
                    "model": decision.selected,
                    "diagnostics": diagnostics,
                }
            )

        elapsed = time.time() - t0
        return SwapResult(
            output_image=canvas,
            faces_detected=len(detected_faces),
            faces_swapped=len(matched_similarities),
            matched_similarities=matched_similarities,
            elapsed_time_sec=elapsed,
            model_routing=model_routing,
            swapped_person_indices=swapped_person_indices,
            requested_person_indices=sorted(outcome["person_index"] for outcome in person_outcomes),
            person_outcomes=sorted(person_outcomes, key=lambda outcome: outcome["person_index"]),
        )

    def process_image_multi_mapping(
        self,
        image: np.ndarray,
        mappings: list[dict],
        identity_blend_strength: float = 0.9,
        enable_color_transfer: bool = True,
        color_transfer_mode: str = "boundary",
        color_transfer_ring_strength: float = 0.45,
        color_transfer_ring_width_px: float = 15.0,
        enable_dense_mask: bool = True,
        swapper_model: str | None = None,
        allow_research_models: bool = False,
        manual_mask: np.ndarray | None = None,
        enable_face_restoration: bool = False,
        restoration_fidelity_weight: float = 0.75,
        restoration_strength: float = 0.5,
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
                        "person_index": int(mapping["target_index"]),
                        "status": "SKIPPED",
                        "reason": "no_faces_detected"
                        if mapping.get("target_embedding") is not None
                        else "target_reference_unavailable",
                        "diagnostics": {
                            "matching": {
                                "best_similarity": None,
                                "assigned_similarity": None,
                                "threshold": float(mapping.get("similarity_threshold", 0.35)),
                                "face_index": None,
                            }
                        },
                    }
                    for mapping in mappings
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
        matching_rows = {}
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
            matching_rows = {
                mapping_index: similarity_matrix[local_row]
                for local_row, (mapping_index, _mapping) in enumerate(valid_mappings)
            }
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
            row = matching_rows.get(mapping_index)
            threshold = float(mapping.get("similarity_threshold", 0.35))
            best_similarity = float(np.max(row)) if row is not None and len(row) else None
            diagnostics = {
                "matching": {
                    "best_similarity": best_similarity,
                    "assigned_similarity": float(match[1]) if match is not None else None,
                    "threshold": threshold,
                    "face_index": match[0] + 1 if match is not None else None,
                }
            }
            if match is None:
                reason = (
                    "target_reference_unavailable"
                    if mapping.get("target_embedding") is None
                    else (
                        "assignment_conflict"
                        if best_similarity is not None and best_similarity >= threshold
                        else "identity_match_below_threshold"
                    )
                )
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": reason,
                        "diagnostics": diagnostics,
                    }
                )
                continue
            face_index, similarity = match
            face = detected_faces[face_index]
            src_refs = mapping.get("source_references", [])
            gal_emb = mapping.get("gallery_embedding")
            gallery_weights = mapping.get("gallery_reference_weights")
            if gal_emb is None and src_refs:
                gal_emb = self.recognizer.build_gallery_embedding([r["embedding"] for r in src_refs])
                gallery_weights = [1.0] * len(src_refs)
            if gal_emb is None:
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": "source_identity_unavailable",
                        "diagnostics": diagnostics,
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
                        "diagnostics": diagnostics,
                        "target_yaw": round(calculate_yaw_ratio(face.kps), 4),
                        "closest_source_yaw_gap": None if closest_pose_gap is None else round(closest_pose_gap, 4),
                    }
                )
                continue

            diagnostics.update(source_selection={}, mask={})
            src_mode = mapping.get("source_mode", "best-match")
            src_emb = self.select_best_reference_embedding(
                target_face=face,
                source_references=src_refs,
                gallery_embedding=gal_emb,
                mode=src_mode,
                gallery_reference_weights=gallery_weights,
                diagnostics=diagnostics["source_selection"],
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
                color_transfer_ring_strength=color_transfer_ring_strength,
                color_transfer_ring_width_px=color_transfer_ring_width_px,
                enable_face_restoration=enable_face_restoration,
                restoration_fidelity_weight=restoration_fidelity_weight,
                restoration_strength=restoration_strength,
                diagnostics=diagnostics["mask"],
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
                        "diagnostics": diagnostics,
                    }
                )
            else:
                person_outcomes.append(
                    {
                        "person_index": person_index,
                        "status": "SKIPPED",
                        "reason": "no_visible_change",
                        "model": decision.selected,
                        "diagnostics": diagnostics,
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
