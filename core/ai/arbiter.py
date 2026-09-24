"""
Multi-Modal Visual Large Language Model (VLM) Identity Arbiter for PersonaWeave.
Uses the configured AIGateway identity-arbitration role and fallback chain.
Performs semantic visual reasoning over face, hair, clothing, and body features to
resolve ambiguous cross-photo person tracking.
"""

from collections.abc import Awaitable, Callable

import cv2
import numpy as np
from pydantic import BaseModel, Field

from core.ai.contracts import IdentityArbitration
from core.ai.gateway import AIGateway


class VLMArbiterResult(BaseModel):
    matched_person_index: int | None = Field(
        default=None, description="The 1-based index of the matched candidate person box in the target image"
    )
    confidence: float = Field(default=0.0, description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(
        default="", description="Chain-of-thought explanation of visual attributes matched or excluded"
    )
    attributes_matched: list[str] = Field(
        default_factory=list, description="List of matching visual cues, e.g. clothing_color, hairstyle, accessories"
    )
    model_used: str = Field(default="", description="The VLM model name that performed the arbitration")
    is_success: bool = Field(default=False, description="Whether the VLM successfully completed arbitration")


class VLMIdentityArbiter:
    """
    Arbitrates ambiguous person identity across photos by combining biometric face cues
    with clothing, hair, posture, and contextual semantics using VLMs.
    """

    def __init__(self, gateway: AIGateway | None = None):
        self.gateway = gateway or AIGateway()

    def _annotate_target_image(self, target_bgr: np.ndarray, candidates: list[dict]) -> np.ndarray:
        """
        Draws bright bounding boxes and numbered badge tags (#1, #2, #3...) on candidate faces
        so the VLM can distinctly recognize each candidate.
        """
        annotated = target_bgr.copy()
        _h, w = annotated.shape[:2]

        for cand in candidates:
            idx = cand.get("index", 1)
            bbox = cand.get("bbox", [0, 0, 0, 0])
            x1, y1, x2, y2 = [int(v) for v in bbox]

            # Draw prominent cyan bounding box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 200, 0), max(2, int(w / 600)))

            # Draw numbered pill tag above box
            tag_text = f"Candidate #{idx}"
            font = cv2.FONT_HERSHEY_SIMPLEX
            scale = max(0.5, w / 1600)
            thickness = max(1, int(scale * 2))
            (tw, th), _ = cv2.getTextSize(tag_text, font, scale, thickness)

            badge_y1 = max(0, y1 - th - 12)
            badge_y2 = y1
            cv2.rectangle(
                annotated,
                (x1, badge_y1),
                (x1 + tw + 16, badge_y2),
                (20, 20, 20),
                -1,
            )
            cv2.rectangle(
                annotated,
                (x1, badge_y1),
                (x1 + tw + 16, badge_y2),
                (255, 200, 0),
                1,
            )
            cv2.putText(
                annotated,
                tag_text,
                (x1 + 8, badge_y2 - 6),
                font,
                scale,
                (255, 255, 255),
                thickness,
                cv2.LINE_AA,
            )

        return annotated

    def _encode_bgr_to_base64_url(self, img_bgr: np.ndarray, max_dim: int = 1280) -> str:
        """Resizes and encodes BGR image to JPEG base64 data URL."""
        import base64

        h, w = img_bgr.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / float(max(h, w))
            img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

        _, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        b64_str = base64.b64encode(buf).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"

    async def arbitrate(
        self,
        reference_image_bgr: np.ndarray,
        reference_bbox: list[float] | None,
        target_image_bgr: np.ndarray,
        candidates: list[dict],
    ) -> VLMArbiterResult:
        """
        Invokes multi-modal LLM to cross-examine reference person against target photo candidates.
        """
        active_model = self.gateway.vision_alias("identity_arbitration")
        try:
            # 1. Prepare reference image / crop
            ref_h, ref_w = reference_image_bgr.shape[:2]
            if reference_bbox:
                rx1, ry1, rx2, ry2 = [int(v) for v in reference_bbox]
                rfw, rfh = rx2 - rx1, ry2 - ry1
                crop_x1 = max(0, rx1 - int(0.5 * rfw))
                crop_x2 = min(ref_w, rx2 + int(0.5 * rfw))
                crop_y1 = max(0, ry1 - int(0.3 * rfh))
                crop_y2 = min(ref_h, ry2 + int(3.0 * rfh))
                ref_payload_bgr = reference_image_bgr[crop_y1:crop_y2, crop_x1:crop_x2]
            else:
                ref_payload_bgr = reference_image_bgr

            ref_b64_url = self._encode_bgr_to_base64_url(ref_payload_bgr, max_dim=600)

            # 2. Extract individual candidate crops for ultra-high accuracy Re-ID
            tgt_h, tgt_w = target_image_bgr.shape[:2]
            user_content = [
                {
                    "type": "text",
                    "text": "Image 1: Reference Person to identify (examine face, hair, and clothing):",
                },
                {"type": "image_url", "image_url": {"url": ref_b64_url}},
            ]

            for cand in candidates:
                c_idx = cand.get("index", 1)
                c_bbox = cand.get("bbox", [0, 0, 0, 0])
                cx1, cy1, cx2, cy2 = [int(v) for v in c_bbox]
                cfw, cfh = cx2 - cx1, cy2 - cy1
                c_crop_x1 = max(0, cx1 - int(0.5 * cfw))
                c_crop_x2 = min(tgt_w, cx2 + int(0.5 * cfw))
                c_crop_y1 = max(0, cy1 - int(0.3 * cfh))
                c_crop_y2 = min(tgt_h, cy2 + int(3.0 * cfh))
                cand_crop = target_image_bgr[c_crop_y1:c_crop_y2, c_crop_x1:c_crop_x2]
                cand_b64_url = self._encode_bgr_to_base64_url(cand_crop, max_dim=600)

                user_content.append({"type": "text", "text": f"Candidate #{c_idx} Image:"})
                user_content.append({"type": "image_url", "image_url": {"url": cand_b64_url}})

            # Also provide full annotated scene
            annotated_target_bgr = self._annotate_target_image(target_image_bgr, candidates)
            tgt_b64_url = self._encode_bgr_to_base64_url(annotated_target_bgr, max_dim=1280)
            user_content.append({"type": "text", "text": "Full Target Scene Overview:"})
            user_content.append({"type": "image_url", "image_url": {"url": tgt_b64_url}})

            # 3. Formulate Prompt
            system_prompt = (
                "You are an expert biometric identity and visual person re-identification (Re-ID) arbiter. "
                "You will receive the Reference Person image followed by Candidate crops (Candidate #1, Candidate #2, ...) "
                "and the full scene overview.\n\n"
                "Your mission: Identify which Candidate (by their number 1, 2, 3, ...) is the EXACT SAME person as the Reference Person.\n"
                "Carefully examine and cross-verify:\n"
                "1. Clothing: Top/jacket/shirt color, collar, zipper, patterns, vest style\n"
                "2. Hairstyle, hair color, length, parting\n"
                "3. Gender, face shape, profile/features\n"
                "4. Body build & posture\n\n"
                "Respond ONLY with a valid JSON object in this exact schema:\n"
                "{\n"
                '  "matched_person_index": <int (e.g. 1, 2, 3...) or null if none match>,\n'
                '  "confidence": <float between 0.0 and 1.0>,\n'
                '  "reasoning": "<concise step-by-step reasoning explaining why this candidate matches and why others are excluded>",\n'
                '  "attributes_matched": ["clothing_style", "clothing_color", "hairstyle"]\n'
                "}"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            # 4. All model selection, retries, and provider routing are
            # defined by the shared LiteLLM gateway configuration.
            response, parsed = await self.gateway.structured_vision_completion(
                messages=messages,
                contract=IdentityArbitration,
                alias=active_model,
                temperature=0.1,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            active_model = response.model or active_model

            return VLMArbiterResult(
                **parsed.model_dump(),
                model_used=active_model,
                is_success=True,
            )

        except Exception as e:
            return VLMArbiterResult(
                matched_person_index=candidates[0].get("index", 1) if candidates else None,
                confidence=0.5,
                reasoning=f"VLM arbitration call error: {e}. Fallback to ArcFace.",
                model_used=active_model,
                is_success=False,
            )

    async def refine_clusters(
        self,
        raw_images_bgr: list[np.ndarray],
        all_detected_faces: list[list],
        clusters: list[dict],
        min_confidence: float = 0.7,
        *,
        similarity_threshold: float,
        image_loader: Callable[[int], Awaitable[np.ndarray]] | None = None,
    ) -> list[dict]:
        """
        Uses VLM suggestions to arbitrate orphan clusters against incomplete clusters.

        A merge additionally requires cosine similarity between the actual ArcFace
        embeddings of the orphan and the reference shown to the VLM to meet the
        caller's similarity_threshold. Missing or invalid biometric evidence cannot
        be replaced by VLM confidence; min_confidence only gates the suggestion.
        """

        if not np.isfinite(similarity_threshold) or not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("ArcFace similarity_threshold must be finite and between 0 and 1")
        # API clustering supplies immutable disk snapshots, loading only a needed pair.
        # In-memory callers (diagnostics/tests) still supply their explicit image list.
        num_images = len(all_detected_faces) if image_loader else len(raw_images_bgr)
        if num_images < 2:
            return clusters

        try:
            # Identify orphan clusters (size == 1) and incomplete candidate clusters (size > 1 and < num_images)
            orphans = [c for c in clusters if len(c["faces"]) == 1]
            candidates_to_merge = [c for c in clusters if 1 < len(c["faces"]) < num_images]

            for orphan in orphans:
                if orphan not in clusters:
                    continue
                img_idx, face_idx, orphan_face = orphan["faces"][0]

                for target_c in candidates_to_merge:
                    # Check if target_c has no member in img_idx
                    if any(m[0] == img_idx for m in target_c["faces"]):
                        continue

                    # Select the best reference face from target_c (prefer frontal / highest score)
                    best_ref = None
                    best_ref_score = -1.0
                    for r_img_idx, r_face_idx, r_face in target_c["faces"]:
                        score = getattr(r_face, "det_score", 0.9)
                        if score > best_ref_score:
                            best_ref_score = score
                            best_ref = (r_img_idx, r_face_idx, r_face)

                    if best_ref is None:
                        continue

                    ref_img_idx, _ref_face_idx, ref_face = best_ref
                    tgt_candidates = [
                        {"index": i + 1, "bbox": [float(x) for x in f.bbox]}
                        for i, f in enumerate(all_detected_faces[img_idx])
                    ]

                    reference_image = await image_loader(ref_img_idx) if image_loader else raw_images_bgr[ref_img_idx]
                    target_image = await image_loader(img_idx) if image_loader else raw_images_bgr[img_idx]
                    res = await self.arbitrate(
                        reference_image_bgr=reference_image,
                        reference_bbox=[float(x) for x in ref_face.bbox],
                        target_image_bgr=target_image,
                        candidates=tgt_candidates,
                    )
                    del reference_image, target_image

                    if res.is_success and res.matched_person_index == face_idx + 1 and res.confidence >= min_confidence:
                        # VLM confidence is semantic evidence, never an ArcFace score.
                        # Recheck the exact reference used above, not a fabricated/missing mean.
                        vectors = [getattr(face, "embedding", None) for face in (orphan_face, ref_face)]
                        if any(vector is None for vector in vectors):
                            continue
                        try:
                            source, reference = (np.asarray(vector, dtype=np.float64) for vector in vectors)
                        except (TypeError, ValueError):
                            continue
                        if (
                            source.ndim != 1
                            or source.size == 0
                            or source.shape != reference.shape
                            or not np.isfinite(source).all()
                            or not np.isfinite(reference).all()
                        ):
                            continue
                        source_norm, reference_norm = np.linalg.norm(source), np.linalg.norm(reference)
                        if (
                            not np.isfinite([source_norm, reference_norm]).all()
                            or min(source_norm, reference_norm) <= 1e-6
                        ):
                            continue
                        similarity = float(np.dot(source / source_norm, reference / reference_norm))
                        if not np.isfinite(similarity) or similarity < similarity_threshold:
                            continue
                        target_c["faces"].append((img_idx, face_idx, orphan_face))
                        if (
                            "embeddings" in target_c
                            and hasattr(orphan_face, "embedding")
                            and orphan_face.embedding is not None
                        ):
                            target_c["embeddings"].append(orphan_face.embedding)
                            mean_vec = np.mean(target_c["embeddings"], axis=0)
                            target_c["mean_emb"] = mean_vec / (np.linalg.norm(mean_vec) + 1e-6)
                        if orphan in clusters:
                            clusters.remove(orphan)
                        break

            return clusters
        except Exception as e:
            print(f"[VLMIdentityArbiter] Cluster refinement skipped: {e}")
            return clusters
