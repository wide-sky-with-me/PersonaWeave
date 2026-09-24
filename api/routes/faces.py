"""HTTP endpoints for faces; application composition lives in api.app."""

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, Request, status

from api.inference_limiter import InferenceLimiter
from api.media import load_image_from_source
from api.schemas import (
    ClusterOccurrence,
    DetectedFaceItem,
    FaceClusterData,
    FaceClusterRequest,
    FaceClusterResponse,
    FaceDetectionData,
    FaceDetectionRequest,
    FaceDetectionResponse,
    IdentityClusterItem,
    ImageFacesMapping,
    VLMArbitrateData,
    VLMArbitrateRequest,
    VLMArbitrateResponse,
)
from core.face_metrics import calculate_reference_quality, calculate_yaw_ratio, describe_reference_quality
from core.memory_budget import require_memory
from core.pipeline import PersonaWeavePipeline

router = APIRouter()


@router.post(
    "/api/v1/detect-faces",
    response_model=FaceDetectionResponse,
    status_code=status.HTTP_200_OK,
    tags=["Face Detection"],
)
async def detect_image_faces(req: FaceDetectionRequest, request: Request):
    """Detects all faces in an image and returns bounding boxes sorted left-to-right."""
    pipeline: PersonaWeavePipeline = request.app.state.pipeline
    inference_limiter: InferenceLimiter = request.app.state.inference_limiter
    try:

        def detect_one():
            img = load_image_from_source(req.image_url)
            h, w = img.shape[:2]
            detected = pipeline.detector.detect_faces(
                img,
                confidence_threshold=req.confidence_threshold,
            )
            # Sort faces from left to right (x1)
            detected.sort(key=lambda f: f.bbox[0])

            face_items: list[DetectedFaceItem] = []
            for i, face in enumerate(detected):
                yaw = calculate_yaw_ratio(face.kps) if face.kps is not None else 0.5
                quality_score = calculate_reference_quality(img, face)
                face_size = max(float(face.bbox[2] - face.bbox[0]), float(face.bbox[3] - face.bbox[1]))
                quality_notes = []
                if face_size < 128:
                    quality_notes.append("face_too_small")
                if face.det_score < 0.75:
                    quality_notes.append("low_detection_confidence")
                if abs(calculate_yaw_ratio(face.kps)) > 0.45:
                    quality_notes.append("large_pose")
                kps_list = [[float(p[0]), float(p[1])] for p in face.kps] if face.kps is not None else None
                face_items.append(
                    DetectedFaceItem(
                        index=i + 1,
                        bbox=[float(x) for x in face.bbox],
                        kps=kps_list,
                        score=round(float(face.det_score), 4),
                        yaw=round(yaw, 3),
                        quality_score=round(quality_score, 3),
                        quality_tier=describe_reference_quality(quality_score),
                        quality_notes=quality_notes,
                    )
                )

            return FaceDetectionResponse(
                code=200,
                data=FaceDetectionData(
                    image_width=w,
                    image_height=h,
                    faces=face_items,
                ),
            )

        return await inference_limiter.run(detect_one)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/v1/cluster-faces",
    response_model=FaceClusterResponse,
    status_code=status.HTTP_200_OK,
    tags=["Face Detection & Clustering"],
)
async def cluster_batch_faces(req: FaceClusterRequest, request: Request):
    """
    Detects faces across all images in a batch and performs ArcFace identity clustering
    to assign consistent Global Person IDs (#1, #2, #3...) across all photos.
    """
    pipeline: PersonaWeavePipeline = request.app.state.pipeline
    inference_limiter: InferenceLimiter = request.app.state.inference_limiter
    import numpy as np

    try:
        with TemporaryDirectory(prefix="personaweave-cluster-") as directory:
            raw_images_data = []
            all_detected_faces = []

            def detect_and_embed_images():
                raw_images_data.clear()
                all_detected_faces.clear()
                for index, url_str in enumerate(req.image_urls):
                    img = load_image_from_source(url_str)
                    h, w = img.shape[:2]
                    faces = pipeline.detector.detect_faces(img, confidence_threshold=0.3)
                    faces.sort(key=lambda f: f.bbox[0])
                    for face in faces:
                        pipeline.recognizer.extract_embedding(img, face)
                    path = Path(directory) / f"{index}.npy"
                    np.save(path, img, allow_pickle=False)
                    raw_images_data.append({"width": w, "height": h, "faces": faces, "path": path})
                    all_detected_faces.append(faces)
                    del img  # Do not hold the preceding full-size frame during the next decode.

            await inference_limiter.run(detect_and_embed_images)
            # Cross-Image Identity Clustering
            clusters = []

            for img_idx, faces in enumerate(all_detected_faces):
                for face_idx, face in enumerate(faces):
                    if face.embedding is None:
                        continue
                    best_cluster = None
                    best_sim = -1.0
                    for c in clusters:
                        sim = pipeline.recognizer.compute_similarity(face.embedding, c["mean_emb"])
                        if sim > best_sim and sim >= req.similarity_threshold:
                            best_sim = sim
                            best_cluster = c

                    if best_cluster is not None:
                        best_cluster["embeddings"].append(face.embedding)
                        mean_vec = np.mean(best_cluster["embeddings"], axis=0)
                        best_cluster["mean_emb"] = mean_vec / (np.linalg.norm(mean_vec) + 1e-6)
                        best_cluster["faces"].append((img_idx, face_idx, face))
                    else:
                        clusters.append(
                            {
                                "embeddings": [face.embedding],
                                "mean_emb": face.embedding,
                                "faces": [(img_idx, face_idx, face)],
                            }
                        )

            # Automatic Multi-Modal VLM Semantic Cluster Refinement
            from core.ai.arbiter import VLMIdentityArbiter

            arbiter = VLMIdentityArbiter()

            async def load_snapshot(index):
                def read_snapshot():
                    item = raw_images_data[index]
                    require_memory(item["width"] * item["height"] * 4)
                    return np.load(item["path"], allow_pickle=False)

                return await inference_limiter.run(read_snapshot)

            clusters = await arbiter.refine_clusters(
                raw_images_bgr=[],
                image_loader=load_snapshot,
                all_detected_faces=all_detected_faces,
                clusters=clusters,
                similarity_threshold=req.similarity_threshold,
            )

            # Sort clusters by occurrence frequency descending
            clusters.sort(key=lambda c: len(c["faces"]), reverse=True)

            # Map each face to its global_person_id
            face_to_global_id = {}
            cluster_items = []

            for new_id, c in enumerate(clusters, start=1):
                occurrences = []
                for img_idx, face_idx, face in c["faces"]:
                    face_to_global_id[(img_idx, face_idx)] = new_id
                    occurrences.append(
                        ClusterOccurrence(
                            image_index=img_idx,
                            face_index=face_idx + 1,
                            bbox=[float(x) for x in face.bbox],
                        )
                    )
                cluster_items.append(
                    IdentityClusterItem(
                        global_person_id=new_id,
                        frequency=len(occurrences),
                        occurrences=occurrences,
                    )
                )

            # Build response mapping for each image
            image_mappings = []
            for img_idx, img_info in enumerate(raw_images_data):
                face_items = []
                for face_idx, face in enumerate(img_info["faces"]):
                    gid = face_to_global_id.get((img_idx, face_idx), face_idx + 1)
                    yaw = calculate_yaw_ratio(face.kps) if face.kps is not None else 0.5
                    kps_list = [[float(p[0]), float(p[1])] for p in face.kps] if face.kps is not None else None
                    face_items.append(
                        DetectedFaceItem(
                            index=face_idx + 1,
                            global_person_id=gid,
                            bbox=[float(x) for x in face.bbox],
                            kps=kps_list,
                            score=round(float(face.det_score), 4),
                            yaw=round(yaw, 3),
                        )
                    )
                image_mappings.append(
                    ImageFacesMapping(
                        image_index=img_idx,
                        image_width=img_info["width"],
                        image_height=img_info["height"],
                        faces=face_items,
                    )
                )

            return FaceClusterResponse(
                code=200,
                data=FaceClusterData(
                    total_clusters=len(clusters),
                    clusters=cluster_items,
                    images=image_mappings,
                ),
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/api/v1/vlm/arbitrate",
    response_model=VLMArbitrateResponse,
    status_code=status.HTTP_200_OK,
    tags=["VLM Multi-Modal Arbiter"],
)
async def arbitrate_identity(req: VLMArbitrateRequest, request: Request):
    """
    Invokes Multi-Modal VLM (via LiteLLM) to cross-verify target person identity
    using face, hair, clothing, body, and scene context.
    """
    pipeline: PersonaWeavePipeline = request.app.state.pipeline
    inference_limiter: InferenceLimiter = request.app.state.inference_limiter
    from core.ai.arbiter import VLMIdentityArbiter

    try:

        def detect_local_faces():
            ref_img = load_image_from_source(req.reference_image_url)
            tgt_img = load_image_from_source(req.target_image_url)
            tgt_faces = pipeline.detector.detect_faces(tgt_img, confidence_threshold=0.3)
            tgt_faces.sort(key=lambda f: f.bbox[0])
            ref_faces = pipeline.detector.detect_faces(ref_img, confidence_threshold=0.25)
            ref_faces.sort(key=lambda f: f.bbox[0])
            return ref_img, tgt_img, tgt_faces, ref_faces

        ref_img, tgt_img, tgt_faces, ref_faces = await inference_limiter.run(detect_local_faces)
        candidates = [{"index": i + 1, "bbox": [float(x) for x in f.bbox]} for i, f in enumerate(tgt_faces)]
        ref_bbox = None
        if ref_faces:
            idx = min(len(ref_faces) - 1, (req.reference_person_index - 1) if req.reference_person_index else 0)
            ref_bbox = [float(x) for x in ref_faces[idx].bbox]

        arbiter = VLMIdentityArbiter()
        result = await arbiter.arbitrate(
            reference_image_bgr=ref_img,
            reference_bbox=ref_bbox,
            target_image_bgr=tgt_img,
            candidates=candidates,
        )

        return VLMArbitrateResponse(
            code=200,
            data=VLMArbitrateData(
                matched_person_index=result.matched_person_index,
                confidence=result.confidence,
                reasoning=result.reasoning,
                attributes_matched=result.attributes_matched,
                model_used=result.model_used,
                is_success=result.is_success,
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
