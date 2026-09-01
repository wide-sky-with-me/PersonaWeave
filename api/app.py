"""
PersonaWeave FastAPI Application Entrypoint.
Provides RESTful endpoints for batch/synchronous high-fidelity AI face swapping.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from api.inference_limiter import InferenceLimiter, inference_concurrency_from_env
from api.portrait_library import build_portrait_library_catalog, resolve_asset_path
from api.refinement_task_manager import RefinementTaskManager
from api.schemas import (
    AIImageModelCatalog,
    AIImageModelItem,
    BatchTaskCreateData,
    BatchTaskCreateRequest,
    BatchTaskCreateResponse,
    BatchTaskListResponse,
    ClusterOccurrence,
    DetectedFaceItem,
    FaceClusterData,
    FaceClusterRequest,
    FaceClusterResponse,
    FaceDetectionData,
    FaceDetectionRequest,
    FaceDetectionResponse,
    HealthResponse,
    IdentityClusterItem,
    ImageFacesMapping,
    RefinementAcceptRequest,
    RefinementRetryRequest,
    RefinementReviewData,
    RefinementReviewRequest,
    RefinementReviewResponse,
    RefinementTaskCreateRequest,
    RefinementTaskCreateResponse,
    RefinementTaskResponse,
    SyncSwapData,
    SyncSwapRequest,
    SyncSwapResponse,
    TaskDetailResponse,
    VLMArbitrateData,
    VLMArbitrateRequest,
    VLMArbitrateResponse,
)
from api.task_manager import TaskManager, load_image_from_source
from core.face_metrics import calculate_reference_quality, calculate_yaw_ratio, describe_reference_quality
from core.pipeline import PersonaWeavePipeline
from core.refinement_service import RefinementService
from utils.gpu_helper import get_optimal_providers

logger = logging.getLogger("personaweave.refinement")
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes the PersonaWeave model pipeline once during service startup."""
    print("[PersonaWeave API] Loading AI models & initialization...")
    pipeline = PersonaWeavePipeline()
    inference_limiter = InferenceLimiter(inference_concurrency_from_env())
    task_manager = TaskManager(pipeline=pipeline, inference_limiter=inference_limiter)
    refinement_service = RefinementService()
    refinement_task_manager = RefinementTaskManager(refinement_service, inference_limiter=inference_limiter)
    app.state.inference_limiter = inference_limiter
    app.state.pipeline = pipeline
    app.state.task_manager = task_manager
    app.state.refinement_service = refinement_service
    app.state.refinement_task_manager = refinement_task_manager
    print("[PersonaWeave API] Ready to serve inference requests.")
    yield
    await refinement_task_manager.shutdown()
    print("[PersonaWeave API] Shutting down...")


app = FastAPI(
    title="PersonaWeave API",
    description="Industrial-Grade Batch Targeted Face Swapping & Harmonization RESTful API Service",
    version="0.1.0",
    lifespan=lifespan,
)

# Enable CORS for web clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("PERSONAWEAVE_CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["System"])
@app.get("/api/v1/health", response_model=HealthResponse, tags=["System"])
async def health_check(request: Request):
    """Health check endpoint reporting pipeline readiness and execution providers."""
    providers = get_optimal_providers()
    provider_names = [p if isinstance(p, str) else p[0] for p in providers]
    pipeline_ready = hasattr(request.app.state, "pipeline") and request.app.state.pipeline is not None
    return HealthResponse(
        status="ok",
        app_name="PersonaWeave",
        version="0.1.0",
        pipeline_ready=pipeline_ready,
        providers=provider_names,
        gpu_available="CUDAExecutionProvider" in provider_names,
    )


@app.get("/api/v1/ai/models", response_model=AIImageModelCatalog, tags=["System"])
async def list_ai_image_models(request: Request):
    """Expose configured image aliases and non-secret provider metadata to the web client."""
    gateway = request.app.state.refinement_service.gateway
    return AIImageModelCatalog(
        default_alias=gateway.default_image_alias,
        models=[
            AIImageModelItem(
                alias=alias,
                provider=config.provider,
                model=str(config.params.get("model", "")),
                display_name=config.display_name,
                description=config.description,
                is_default=alias == gateway.default_image_alias,
            )
            for alias, config in gateway.image_edits.items()
        ],
    )


@app.post(
    "/api/v1/batch-tasks",
    response_model=BatchTaskCreateResponse,
    status_code=status.HTTP_200_OK,
    tags=["Batch Swapping"],
)
async def submit_batch_task(req: BatchTaskCreateRequest, request: Request):
    """Submits a new batch face swapping task for asynchronous execution."""
    task_manager: TaskManager = request.app.state.task_manager
    try:
        task_id, estimated_seconds = task_manager.create_batch_task(req)
        return BatchTaskCreateResponse(
            code=200,
            message="Batch task accepted successfully",
            data=BatchTaskCreateData(
                task_id=task_id,
                total_images=req.total_images,
                status=task_manager.get_task(task_id).status,
                estimated_seconds=estimated_seconds,
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get(
    "/api/v1/batch-tasks/{task_id}",
    response_model=TaskDetailResponse,
    tags=["Batch Swapping"],
)
async def get_batch_task_status(task_id: str, request: Request):
    """Retrieves current processing progress, status, and generated result image links for a batch task."""
    task_manager: TaskManager = request.app.state.task_manager
    task_data = task_manager.get_task(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")
    return TaskDetailResponse(code=200, data=task_data)


@app.get("/api/v1/batch-tasks", tags=["Batch Swapping"], deprecated=True)
async def list_recent_tasks(request: Request, limit: int = 50):
    """List complete tasks for backward compatibility; new clients use the history projection."""
    task_manager: TaskManager = request.app.state.task_manager
    return {"code": 200, "data": [task.model_dump(mode="json") for task in task_manager.list_tasks(limit=limit)]}


@app.get(
    "/api/v1/task-history/batch-tasks",
    response_model=BatchTaskListResponse,
    tags=["Batch Swapping"],
)
async def list_batch_task_history(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    """List lightweight persisted history; fetch one task for complete result details."""
    task_manager: TaskManager = request.app.state.task_manager
    return BatchTaskListResponse(data=task_manager.list_task_summaries(limit=limit))


@app.post(
    "/api/v1/swap-sync",
    response_model=SyncSwapResponse,
    status_code=status.HTTP_200_OK,
    tags=["Synchronous Swapping"],
)
async def swap_single_image_sync(req: SyncSwapRequest, request: Request):
    """Synchronously swaps a single image and returns the result immediately."""
    task_manager: TaskManager = request.app.state.task_manager
    inference_limiter: InferenceLimiter = request.app.state.inference_limiter
    try:
        result_dict = await inference_limiter.run(task_manager.execute_sync_swap, req)
        return SyncSwapResponse(
            code=200,
            message="Image swapped successfully",
            data=SyncSwapData(**result_dict),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post(
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
        img = await asyncio.to_thread(load_image_from_source, req.image_url)
        h, w = img.shape[:2]
        detected = await inference_limiter.run(
            pipeline.detector.detect_faces,
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post(
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
        raw_images_data = []
        all_detected_faces = []

        def detect_and_embed_images():
            for url_str in req.image_urls:
                img = load_image_from_source(url_str)
                h, w = img.shape[:2]
                faces = pipeline.detector.detect_faces(img, confidence_threshold=0.3)
                faces.sort(key=lambda f: f.bbox[0])
                for face in faces:
                    pipeline.recognizer.extract_embedding(img, face)
                raw_images_data.append({"width": w, "height": h, "faces": faces, "image_bgr": img})
                all_detected_faces.append(faces)

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
        from core.vlm_arbiter import VLMIdentityArbiter

        arbiter = VLMIdentityArbiter()
        raw_images_bgr = [item["image_bgr"] for item in raw_images_data]
        clusters = await arbiter.refine_clusters(
            raw_images_bgr=raw_images_bgr,
            all_detected_faces=all_detected_faces,
            clusters=clusters,
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


@app.post(
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
    from core.vlm_arbiter import VLMIdentityArbiter

    try:
        ref_img, tgt_img = await asyncio.gather(
            asyncio.to_thread(load_image_from_source, req.reference_image_url),
            asyncio.to_thread(load_image_from_source, req.target_image_url),
        )

        def detect_local_faces():
            tgt_faces = pipeline.detector.detect_faces(tgt_img, confidence_threshold=0.3)
            tgt_faces.sort(key=lambda f: f.bbox[0])
            ref_faces = pipeline.detector.detect_faces(ref_img, confidence_threshold=0.25)
            ref_faces.sort(key=lambda f: f.bbox[0])
            return tgt_faces, ref_faces

        tgt_faces, ref_faces = await inference_limiter.run(detect_local_faces)
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


@app.post("/api/v1/refinement/review", response_model=RefinementReviewResponse, tags=["AI Refinement"])
async def review_swap_result(req: RefinementReviewRequest, request: Request):
    """Ask the configured server-side VLM for one structured whole-image defect review."""
    service: RefinementService = request.app.state.refinement_service
    try:
        swapped = load_image_from_source(req.swapped_image_url)
        original = load_image_from_source(req.original_image_url) if req.original_image_url else None
        person_ids = [person.person_id for person in req.people]
        started = time.perf_counter()
        logger.info("VLM batch review started people=%s image_shape=%s", person_ids, swapped.shape)
        try:
            reports = await asyncio.wait_for(service.review(swapped, req.people, original=original), timeout=120)
        except TimeoutError as exc:
            elapsed = time.perf_counter() - started
            logger.warning("VLM batch review timed out people=%s elapsed=%.2fs", person_ids, elapsed)
            raise HTTPException(status_code=504, detail="AI 分析超过 120 秒，已终止；请稍后重试") from exc
        elapsed = time.perf_counter() - started
        logger.info("VLM batch review completed people=%s elapsed=%.2fs", person_ids, elapsed)
        data = {person_id: RefinementReviewData(**report) for person_id, report in reports.items()}
        return RefinementReviewResponse(data=data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("VLM batch review failed people=%s", [person.person_id for person in req.people])
        raise HTTPException(status_code=502, detail=f"AI review failed: {exc}") from exc


@app.post(
    "/api/v1/refinement/tasks",
    response_model=RefinementTaskCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["AI Refinement"],
)
async def create_refinement_task(req: RefinementTaskCreateRequest, request: Request):
    """Queue an atomic whole-image generative refinement task."""
    manager: RefinementTaskManager = request.app.state.refinement_task_manager
    person_ids = [person.person_id for person in req.people]
    if len(set(person_ids)) != len(person_ids):
        raise HTTPException(status_code=422, detail="Refinement person IDs must be unique")
    configured_aliases = manager.service.gateway.image_edits
    if req.model_alias and req.model_alias not in configured_aliases:
        raise HTTPException(status_code=422, detail="Selected refinement model is not configured")
    return RefinementTaskCreateResponse(data=manager.create(req))


@app.get("/api/v1/refinement/tasks/{task_id}", response_model=RefinementTaskResponse, tags=["AI Refinement"])
async def get_refinement_task(task_id: str, request: Request):
    manager: RefinementTaskManager = request.app.state.refinement_task_manager
    task = manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Refinement task not found")
    return RefinementTaskResponse(data=task)


@app.post("/api/v1/refinement/tasks/{task_id}/cancel", response_model=RefinementTaskResponse, tags=["AI Refinement"])
async def cancel_refinement_task(task_id: str, request: Request):
    manager: RefinementTaskManager = request.app.state.refinement_task_manager
    task = await manager.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Refinement task not found")
    return RefinementTaskResponse(data=task)


@app.post("/api/v1/refinement/tasks/{task_id}/accept", response_model=RefinementTaskResponse, tags=["AI Refinement"])
async def accept_refinement_task(task_id: str, req: RefinementAcceptRequest, request: Request):
    manager: RefinementTaskManager = request.app.state.refinement_task_manager
    try:
        task = await manager.accept(task_id, req.expected_revision, req.candidate_digest)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Refinement task not found")
    return RefinementTaskResponse(data=task)


@app.post(
    "/api/v1/refinement/tasks/{task_id}/people/{person_id}/retry",
    response_model=RefinementTaskResponse,
    tags=["AI Refinement"],
)
async def retry_refinement_person(task_id: str, person_id: int, req: RefinementRetryRequest, request: Request):
    manager: RefinementTaskManager = request.app.state.refinement_task_manager
    try:
        task = await manager.retry_person(task_id, person_id, req.user_instructions, req.model_alias)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Refinement task not found")
    return RefinementTaskResponse(data=task)


@app.get("/api/v1/refinement/tasks/{task_id}/candidate", tags=["AI Refinement"])
async def get_refinement_candidate(task_id: str, request: Request):
    manager: RefinementTaskManager = request.app.state.refinement_task_manager
    path = manager.candidate_path(task_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Refinement candidate is not available")
    return FileResponse(path)


def _safe_file(root: Path, subpath: str, not_found_detail: str):
    base = root.resolve()
    candidate = (base / subpath).resolve()
    if not candidate.is_relative_to(base) or not candidate.is_file():
        raise HTTPException(status_code=404, detail=not_found_detail)
    return FileResponse(candidate)


@app.get("/api/v1/files/{subpath:path}", tags=["Static Files"])
async def get_output_file(subpath: str):
    """Serve generated files without allowing paths outside the output root."""
    return _safe_file(Path("outputs/api_results"), subpath, "File not found")


@app.get("/api/v1/data/{subpath:path}", tags=["Static Files"])
async def get_data_file(subpath: str):
    """Serve sample files without allowing paths outside the data root."""
    return _safe_file(Path("data"), subpath, "Sample file not found")


@app.get("/api/v1/portrait-library", tags=["Portrait Library"])
async def get_portrait_library():
    """Return the indexed local male/female portrait identity corpus."""
    return {"code": 200, "data": build_portrait_library_catalog()}


@app.get("/api/v1/portrait-assets/{asset_id}/thumbnail", tags=["Portrait Library"])
async def get_portrait_asset_thumbnail(asset_id: str):
    """Serve a managed portrait asset's thumbnail, resolved by opaque ID via SQLite."""
    path = resolve_asset_path(asset_id, thumbnail=True)
    if path is None:
        raise HTTPException(status_code=404, detail="Portrait asset thumbnail not found")
    return FileResponse(path)


@app.get("/api/v1/portrait-assets/{asset_id}/content", tags=["Portrait Library"])
async def get_portrait_asset_content(asset_id: str):
    """Serve a managed portrait asset's original image, resolved by opaque ID via SQLite."""
    path = resolve_asset_path(asset_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Portrait asset not found")
    return FileResponse(path)


@app.get("/api/v1/samples", tags=["System"])
async def get_sample_manifest():
    """Returns sample reference photos and target batch image paths."""
    sources = [
        {
            "name": "01.png (正脸)",
            "path": "data/test_target/01.png",
            "url": "/api/v1/data/test_target/01.png",
            "yaw": 0,
        },
        {
            "name": "02.png (微侧)",
            "path": "data/test_target/02.png",
            "url": "/api/v1/data/test_target/02.png",
            "yaw": 15,
        },
        {
            "name": "03.png (微侧)",
            "path": "data/test_target/03.png",
            "url": "/api/v1/data/test_target/03.png",
            "yaw": -15,
        },
    ]
    targets = []
    test_img_dir = "data/test_images"
    if os.path.exists(test_img_dir):
        for fname in sorted(os.listdir(test_img_dir)):
            if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                targets.append(
                    {
                        "name": fname,
                        "path": f"data/test_images/{fname}",
                        "url": f"/api/v1/data/test_images/{fname}",
                    }
                )
    return {"sources": sources, "targets": targets}


@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": exc.detail},
    )
