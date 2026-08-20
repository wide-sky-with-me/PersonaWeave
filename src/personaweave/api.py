import base64
import binascii
import hashlib
import json
import mimetypes
import re
import shutil
import sqlite3
import warnings
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from urllib.parse import quote

import httpx
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

from personaweave.config import Settings
from personaweave.domain import (
    AnalysisResult,
    AssetKind,
    BoundingBox,
    CompiledBatchContract,
    MappingDecision,
    SwapResult,
    JobStage,
    BatchStatus,
    bbox_iou,
    source_face_condition_requires_review,
)
from personaweave.assets import AssetResolver
from personaweave.executor import PipelineExecutor, StageExecutionError
from personaweave.providers import InferenceGateway, YunwuMultimodalProvider
from personaweave.providers.gateway import ProviderNotConfigured
from personaweave.storage import Repository
from personaweave.workflow import InvalidTransition, WorkflowService
from personaweave.provider_errors import (
    ProviderExecutionError,
    provider_error,
    provider_error_from_detail,
    provider_error_http_status,
    provider_error_retryable,
)


def contract_compilation_failure(error: Exception) -> tuple[int, str]:
    """Return a bounded user-facing status and message for persisted compile state."""
    if isinstance(error, ProviderExecutionError):
        return provider_error_http_status(error.error), error.error.message
    if isinstance(error, ProviderNotConfigured):
        return 503, str(error)
    if isinstance(error, InvalidTransition):
        return 409, str(error)
    if isinstance(error, httpx.TimeoutException):
        return 504, "正式约束编译等待云端模型超时，请重试。"
    if isinstance(error, httpx.HTTPStatusError):
        return 502, f"正式约束编译服务返回 HTTP {error.response.status_code}，请重试。"
    if isinstance(error, httpx.HTTPError):
        return 502, "正式约束编译服务请求失败，请重试。"
    if isinstance(error, ValidationError):
        diagnostics = []
        for item in error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:8]:
            location = ".".join(str(part) for part in item.get("loc", ()))
            diagnostics.append(
                f"{location or '<root>'} [{item.get('type', 'validation_error')}]"
            )
        detail = "；".join(diagnostics) or "<root> [validation_error]"
        return 502, f"云端模型返回的正式约束结构无效：{detail}。请重试。"
    if isinstance(error, ValueError):
        return 422, (str(error).strip() or "正式约束输入无效。")[:1000]
    return 500, f"正式约束编译失败（{type(error).__name__}），请重试。"


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    task_instruction: str = Field(default="", max_length=4000)
    max_iterations: int = Field(default=3, ge=1, le=10)


class TaskSpecUpdate(BaseModel):
    task_instruction: str = Field(max_length=4000)
    max_iterations: int = Field(default=3, ge=1, le=10)


class PersonMergeRequest(BaseModel):
    person_ids: list[str] = Field(min_length=2)


class PersonSplitRequest(BaseModel):
    face_ids: list[str] = Field(min_length=1)


class ManualFaceCreate(BaseModel):
    bbox: BoundingBox
    person_id: str | None = None


class FaceOccurrenceResolution(BaseModel):
    action: Literal["manual_bbox", "keep_original", "accept_occlusion"]
    bbox: BoundingBox | None = None


class IdentityCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)


class LibraryUseRequest(BaseModel):
    batch_id: str



class TaskContractCompile(BaseModel):
    decisions: list[MappingDecision] | None = None


class TaskContractConfirmation(BaseModel):
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: list[MappingDecision] | None = None
    acknowledged_mask_sha256s: list[
        Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ] = Field(default_factory=list, max_length=128)


class AnalysisCompletion(BaseModel):
    results: list[AnalysisResult]


class SwapCompletion(BaseModel):
    results: list[SwapResult]



def _public_fields(value: object, fields: tuple[str, ...]) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {field: value[field] for field in fields if field in value}

def public_asset_evidence(asset: dict, content_url: str) -> dict:
    result = _public_fields(
        asset,
        (
            "id",
            "batch_id",
            "identity_id",
            "parent_asset_id",
            "kind",
            "display_code",
            "filename",
            "media_type",
            "sha256",
            "created_at",
        ),
    ) or {}
    result["content_url"] = content_url
    return result



def public_target_profile(value: object) -> dict | None:
    return _public_fields(
        value,
        (
            "identity_id",
            "identity_code",
            "apparent_age",
            "face_features",
            "hair",
            "eyewear",
            "accessories",
            "makeup",
            "stable_identity_anchors",
            "variable_traits",
            "unknown_evidence",
            "unsupported_evidence",
            "reference_board_codes",
            "reference_board_digest",
        ),
    )


def public_reference_sufficiency(value: object) -> dict | None:
    return _public_fields(
        value,
        (
            "identity_id",
            "identity_code",
            "reference_count",
            "visible_views",
            "missing_views",
            "confidence",
            "sufficient",
            "requires_manual_review",
            "summary",
        ),
    )


def public_permissions(value: object) -> dict | None:
    return _public_fields(value, ("editable", "protected"))


def _public_reference_assets(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        evidence
        for item in value
        if (evidence := _public_fields(item, ("code", "sha256"))) is not None
    ]


def _public_source_coverage(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        coverage = _public_fields(
            item,
            (
                "source_asset_id",
                "source_asset_code",
                "person_id",
                "person_code",
                "sufficient",
                "requires_manual_review",
                "summary",
            ),
        )
        if coverage is None:
            continue
        occurrences = item.get("occurrences")
        coverage["occurrences"] = (
            [
                evidence
                for occurrence in occurrences
                if (
                    evidence := _public_fields(
                        occurrence,
                        (
                            "face_instance_id",
                            "bbox",
                            "face_area_pixels",
                            "pose",
                            "lighting_occlusion_blur",
                        ),
                    )
                )
                is not None
            ]
            if isinstance(occurrences, list)
            else []
        )
        result.append(coverage)
    return result


def public_compiled_contract(value: object) -> dict | None:
    """Serialize only bounded contract evidence, excluding all instruction/provider prose."""
    if not isinstance(value, dict):
        return None
    try:
        value = CompiledBatchContract.model_validate(value).model_dump(mode="json")
    except ValidationError:
        # The allowlist remains safe for legacy/corrupt evidence while valid
        # canonical contracts receive fresh computed projections.
        pass
    contract = _public_fields(
        value,
        ("version", "max_iterations", "review_reasons", "requires_manual_review"),
    )
    if contract is None:
        return None
    operations = value.get("operations")
    contract["operations"] = []
    if isinstance(operations, list):
        for item in operations:
            operation = _public_fields(
                item,
                (
                    "person_id",
                    "person_code",
                    "action",
                    "target_identity_id",
                    "target_identity_code",
                    "requires_manual_review",
                    "review_reasons",
                ),
            )
            if operation is None:
                continue
            operation["permissions"] = public_permissions(item.get("permissions"))
            operation["target_profile"] = public_target_profile(
                item.get("target_profile")
            )
            operation["reference_assessment"] = public_reference_sufficiency(
                item.get("reference_assessment")
            )
            operation["reference_assets"] = _public_reference_assets(
                item.get("reference_assets")
            )
            operation["source_reference_coverage"] = _public_source_coverage(
                item.get("source_reference_coverage")
            )
            contract["operations"].append(operation)
    identities = value.get("identities")
    contract["identities"] = []
    if isinstance(identities, list):
        for item in identities:
            if not isinstance(item, dict):
                continue
            contract["identities"].append(
                {
                    "profile": public_target_profile(item.get("profile")),
                    "reference_assessment": public_reference_sufficiency(
                        item.get("reference_assessment")
                    ),
                    "reference_assets": _public_reference_assets(
                        item.get("reference_assets")
                    ),
                }
            )
    sources = value.get("source_assets")
    contract["source_assets"] = []
    if isinstance(sources, list):
        for item in sources:
            evidence = _public_fields(
                item,
                (
                    "asset_id",
                    "code",
                    "sha256",
                    "editable_face_instance_ids",
                    "requires_authorization_mask",
                ),
            )
            if evidence is None:
                continue
            mask = item.get("authorization_mask") if isinstance(item, dict) else None
            if isinstance(mask, dict):
                evidence["authorization_mask"] = _public_fields(
                    mask,
                    (
                        "sha256",
                        "width",
                        "height",
                        "review_reasons",
                        "status",
                        "requires_manual_review",
                    ),
                )
            contract["source_assets"].append(evidence)
    return contract


def public_batch_evidence(batch: dict) -> dict:
    result = _public_fields(
        batch,
        (
            "id",
            "name",
            "status",
            "max_iterations",
            "task_instruction",
            "contract_hash",
            "confirmed_at",
            "contract_compile_status",
            "contract_compile_error",
            "contract_compile_started_at",
            "contract_compile_finished_at",
            "requires_manual_review",
            "analysis_review_issues",
            "deleted_at",
            "created_at",
            "updated_at",
        ),
    ) or {}
    result["compiled_contract"] = public_compiled_contract(
        batch.get("compiled_contract")
    )
    return result


def public_identity_evidence(identity: dict) -> dict:
    result = _public_fields(
        identity,
        ("id", "batch_id", "display_code", "label", "created_at"),
    ) or {}
    result["target_profile"] = public_target_profile(identity.get("target_profile"))
    result["reference_sufficiency"] = public_reference_sufficiency(
        identity.get("reference_sufficiency")
    )
    return result




def build_router(repository: Repository, settings: Settings) -> APIRouter:
    gateway = InferenceGateway(
        settings.inference_base_url,
        settings.inference_api_key,
        settings.formal_image_timeout_seconds,
    )
    vision_client = httpx.AsyncClient(
        timeout=settings.multimodal_timeout_seconds,
        transport=httpx.AsyncHTTPTransport(retries=2),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            try:
                await gateway.aclose()
            finally:
                await vision_client.aclose()

    router = APIRouter(prefix="/api/v1", lifespan=lifespan)
    yunwu = YunwuMultimodalProvider(
        settings.yunwu_base_url,
        settings.yunwu_api_key,
        settings.yunwu_vision_model,
        settings.multimodal_timeout_seconds,
        api_mode=settings.yunwu_vision_api_mode,
        api_path=settings.yunwu_vision_api_path,
        client=vision_client,
    )
    reviewer_a = YunwuMultimodalProvider(
        settings.yunwu_base_url,
        settings.yunwu_api_key,
        settings.yunwu_reviewer_model_a,
        settings.multimodal_timeout_seconds,
        api_mode=settings.yunwu_vision_api_mode,
        api_path=settings.yunwu_vision_api_path,
        client=vision_client,
    )
    reviewer_b = YunwuMultimodalProvider(
        settings.yunwu_base_url,
        settings.yunwu_api_key,
        settings.yunwu_reviewer_model_b,
        settings.multimodal_timeout_seconds,
        api_mode=settings.yunwu_vision_api_mode,
        api_path=settings.yunwu_vision_api_path,
        client=vision_client,
    )
    quality_validators = (
        (reviewer_a, reviewer_b)
        if reviewer_a.configured and reviewer_b.configured
        else None
    )
    asset_resolver = AssetResolver(
        settings.asset_root,
        (
            (settings.asset_legacy_root,)
            if settings.asset_legacy_root is not None
            else ()
        ),
    )
    workflow = WorkflowService(
        repository,
        settings.formal_image_model,
        settings.formal_image_api_mode,
        settings.formal_image_api_path,
        settings.formal_image_size,
        settings.formal_image_response_format,
        contract_compiler=yunwu,
        semantic_mask_compiler=yunwu,
        asset_resolver=asset_resolver,
        contract_compilation_concurrency=settings.contract_compilation_concurrency,
    )
    executor = PipelineExecutor(
        repository,
        workflow,
        gateway,
        asset_resolver,
        settings.worker_concurrency,
        advisory_validator=yunwu if yunwu.configured else None,
        quality_validators=quality_validators,
        advisory_preview_max_edge=settings.advisory_preview_max_edge,
    )

    def preview_data_uri(content: bytes, media_type: str | None) -> str:
        with Image.open(BytesIO(content)) as image:
            rgb = image.convert("RGB")
            rgb.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
            output = BytesIO()
            rgb.save(output, format="JPEG", quality=88, optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def public_asset(asset: dict) -> dict:
        return public_asset_evidence(
            asset,
            f"/api/v1/assets/{asset['id']}/content",
        )

    def public_identity(identity: dict) -> dict:
        return {
            **public_identity_evidence(identity),
            "references": [
                public_asset(asset) for asset in identity.get("references", [])
            ],
        }

    def public_batch(batch: dict) -> dict:
        result = public_batch_evidence(batch)
        contract = result.get("compiled_contract")
        if not isinstance(contract, dict):
            return result
        masks = {
            record["source_asset_id"]: record
            for record in repository.list_formal_masks(batch["id"])
        }
        for source in contract.get("source_assets", []):
            record = masks.get(source.get("asset_id"))
            mask = source.get("authorization_mask")
            if record is None or not isinstance(mask, dict):
                continue
            mask["status"] = record["status"]
            mask["preview_url"] = (
                f"/api/v1/batches/{batch['id']}/formal-masks/"
                f"{record['sha256']}/content"
            )
        return result

    def public_library_asset(asset: dict) -> dict:
        return {
            **{key: value for key, value in asset.items() if key != "storage_uri"},
            "content_url": f"/api/v1/material-library/assets/{asset['id']}/content",
        }

    def public_library_item(item: dict) -> dict:
        return {
            **{key: value for key, value in item.items() if key != "assets"},
            "assets": [public_library_asset(asset) for asset in item.get("assets", [])],
        }

    def public_person(person: dict) -> dict:
        item = dict(person)
        item["confirmed"] = bool(item.get("confirmed"))
        item["faces"] = [
            {
                **face,
                "needs_confirmation": bool(face.get("needs_confirmation", True)),
                "crop_url": f"/api/v1/faces/{face['id']}/crop",
                "localization_preview_url": (
                    f"/api/v1/faces/{face['id']}/localization-preview"
                    if face.get("latest_localization_attempt")
                    and (
                        not face["latest_localization_attempt"].get("accepted")
                        or source_face_condition_requires_review(
                            face["latest_localization_attempt"].get(
                                "source_face_condition"
                            ),
                            legacy_unrecoverable_occlusion=bool(
                                face["latest_localization_attempt"].get(
                                    "unrecoverable_occlusion"
                                )
                            ),
                        )
                    )
                    else None
                ),
            }
            for face in person.get("faces", [])
        ]
        return item

    def legacy_trash_directory(entry_name: str) -> Path:
        if Path(entry_name).name != entry_name:
            raise HTTPException(400, "invalid recycle-bin entry")
        trash_root = (settings.asset_root.resolve() / ".trash").resolve()
        target = (trash_root / entry_name).resolve()
        if target.parent != trash_root or not target.is_dir():
            raise HTTPException(404, "legacy recycle-bin entry not found")
        return target

    def load_legacy_manifest(directory: Path) -> dict | None:
        manifest_path = directory / ".personaweave-trash.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict) or not isinstance(manifest.get("name"), str):
            return None
        return manifest

    def safe_legacy_file(directory: Path, relative_path: str) -> Path:
        path = (directory / relative_path).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise HTTPException(404, "recycled image not found")
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(415, "recycled file is not a supported image")
        return path

    def sha256_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()

    def public_job(job: dict) -> dict:
        result = {
            key: value
            for key, value in job.items()
            if key not in {"input", "output"}
        }
        result["error_retryable"] = provider_error_retryable(job.get("error_code"))
        if job.get("stage") == JobStage.ANALYZE and job.get("output"):
            result["output"] = {
                "detection_audit": job["output"].get("detection_audit"),
                "localization_attempts": job["output"].get(
                    "localization_attempts", []
                ),
            }
        elif job.get("stage") == JobStage.SWAP and job.get("output"):
            result["output"] = {
                "result_asset_id": job["output"].get("result_asset_id"),
                "generation_review": job["output"].get("generation_review"),
                "authorization_mask_sha256": job["output"].get(
                    "authorization_mask_sha256"
                ),
                "authorization_mask_width": job["output"].get(
                    "authorization_mask_width"
                ),
                "authorization_mask_height": job["output"].get(
                    "authorization_mask_height"
                ),
                "review_reasons": job["output"].get("review_reasons", []),
                "requires_manual_review": bool(
                    job["output"].get("review_reasons")
                ),
            }
        elif job.get("stage") == JobStage.VALIDATE and job.get("output"):
            result["output"] = job["output"]
        return result

    def save_upload(batch_id: str, upload: UploadFile, category: str) -> tuple[str, str, str]:
        directory = settings.asset_root / batch_id / category
        directory.mkdir(parents=True, exist_ok=True)
        temporary_path = directory / f"upload-{uuid4()}.tmp"
        hasher = hashlib.sha256()
        size = 0
        try:
            with temporary_path.open("wb") as target:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "image exceeds configured upload limit")
                    hasher.update(chunk)
                    target.write(chunk)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(temporary_path) as image:
                        image.verify()
                        image_format = (image.format or "").upper()
            except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning) as error:
                raise HTTPException(415, "uploaded file is not a supported safe image") from error
            format_map = {
                "JPEG": (".jpg", "image/jpeg"),
                # Camera JPEGs can contain an MPF index and are reported by
                # Pillow as MPO even though their primary image is JPEG.
                "MPO": (".jpg", "image/jpeg"),
                "PNG": (".png", "image/png"),
                "WEBP": (".webp", "image/webp"),
            }
            if image_format not in format_map:
                raise HTTPException(415, "only JPEG, PNG, and WebP images are accepted")
            suffix, media_type = format_map[image_format]
            digest = hasher.hexdigest()
            path = directory / f"{digest}{suffix}"
            if path.exists():
                temporary_path.unlink()
            else:
                try:
                    temporary_path.replace(path)
                except (FileExistsError, PermissionError):
                    # Concurrent identical uploads may race on the same
                    # digest path. Once the destination exists, the upload
                    # is already durably represented.
                    if path.exists():
                        temporary_path.unlink(missing_ok=True)
                    else:
                        raise
            return path.resolve().as_uri(), digest, media_type
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    @router.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "providers": {
                "inference_gateway": {"configured": gateway.configured},
                "yunwu_multimodal_analysis": {
                    "configured": yunwu.configured,
                    "model": settings.yunwu_vision_model,
                    "api_mode": settings.yunwu_vision_api_mode,
                    "api_path": settings.yunwu_vision_api_path,
                    "default_person_analysis": True,
                },
                "yunwu_preview_image": {
                    "configured": bool(settings.yunwu_api_key and settings.yunwu_image_model),
                    "model": settings.yunwu_image_model,
                    "api_mode": settings.yunwu_image_api_mode,
                    "api_path": settings.yunwu_image_api_path,
                    "size": settings.yunwu_image_size,
                    "response_format": settings.yunwu_image_response_format,
                    "timeout_seconds": settings.yunwu_image_timeout_seconds,
                },
                "yunwu_formal_image": {
                    "configured": bool(settings.yunwu_api_key and settings.formal_image_model),
                    "model": settings.formal_image_model,
                    "api_mode": settings.formal_image_api_mode,
                    "api_path": settings.formal_image_api_path,
                    "size": settings.formal_image_size,
                    "response_format": settings.formal_image_response_format,
                    "timeout_seconds": settings.formal_image_timeout_seconds,
                },
            },
        }

    @router.get("/providers/yunwu/models")
    async def list_yunwu_models() -> dict:
        try:
            models = await yunwu.list_model_records()
        except ProviderNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except Exception as error:
            raise HTTPException(502, "failed to query Yunwu models") from error
        def roles_for(endpoint_types: list[str]) -> list[str]:
            normalized = {item.strip().lower() for item in endpoint_types}
            roles = []
            if normalized.intersection({"openai", "openai-response"}):
                roles.append("conversation")
            if any(
                "image" in item or "绘图" in item or "dall-e" in item
                for item in normalized
            ):
                roles.append("image_generation")
            if any("edit" in item or "编辑" in item for item in normalized):
                roles.append("image_edit")
            return roles

        enriched = [
            {**model, "roles": roles_for(model["supported_endpoint_types"])}
            for model in models
        ]
        return {
            "count": len(enriched),
            "configured": {
                "vision": {
                    "model": settings.yunwu_vision_model,
                    "api_mode": settings.yunwu_vision_api_mode,
                    "api_path": settings.yunwu_vision_api_path,
                },
                "preview_image": {
                    "model": settings.yunwu_image_model,
                    "api_mode": settings.yunwu_image_api_mode,
                    "api_path": settings.yunwu_image_api_path,
                    "size": settings.yunwu_image_size,
                    "response_format": settings.yunwu_image_response_format,
                    "timeout_seconds": settings.yunwu_image_timeout_seconds,
                },
                "formal_image": {
                    "model": settings.formal_image_model,
                    "api_mode": settings.formal_image_api_mode,
                    "api_path": settings.formal_image_api_path,
                    "size": settings.formal_image_size,
                    "response_format": settings.formal_image_response_format,
                    "timeout_seconds": settings.formal_image_timeout_seconds,
                },
            },
            "conversation_candidates": [
                model for model in enriched if "conversation" in model["roles"]
            ],
            "image_candidates": [
                model
                for model in enriched
                if {"image_generation", "image_edit"}.intersection(model["roles"])
            ],
            "models": enriched,
        }

    @router.post("/previews/replace-all")
    async def preview_replace_all(
        source: Annotated[UploadFile, File()],
        description: Annotated[str, Form(min_length=1, max_length=2000)],
        reference: Annotated[UploadFile | None, File()] = None,
        max_iterations: Annotated[int, Form(ge=1, le=10)] = 3,
    ):
        if not settings.inference_base_url:
            raise HTTPException(503, "inference gateway is not configured")

        async def read_preview(upload: UploadFile) -> bytes:
            content = await upload.read(settings.max_upload_bytes + 1)
            if len(content) > settings.max_upload_bytes:
                raise HTTPException(413, "preview image exceeds configured upload limit")
            if not content or not (upload.content_type or "").startswith("image/"):
                raise HTTPException(415, "preview requires a supported image upload")
            return content

        source_content = await read_preview(source)
        reference_content = None
        files = [
            (
                "source",
                (source.filename or "source.png", source_content, source.content_type),
            )
        ]
        if reference is not None:
            reference_content = await read_preview(reference)
            files.append(
                (
                    "reference",
                    (
                        reference.filename or "reference.png",
                        reference_content,
                        reference.content_type,
                    ),
                )
            )
        headers = {}
        if settings.inference_api_key:
            headers["Authorization"] = f"Bearer {settings.inference_api_key}"

        async def generate_candidate(edit_description: str) -> httpx.Response:
            data = {
                "description": edit_description,
                "model": settings.yunwu_image_model,
                "api_mode": settings.yunwu_image_api_mode,
                "api_path": settings.yunwu_image_api_path,
                "output_size": settings.yunwu_image_size,
                "response_format": settings.yunwu_image_response_format,
                "strategy": settings.preview_strategy,
            }
            async with httpx.AsyncClient(timeout=settings.yunwu_image_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.inference_base_url.rstrip('/')}/v1/preview-replacement",
                    headers=headers,
                    data=data,
                    files=files,
                )
                response.raise_for_status()
                return response

        def decode_gateway_report(response: httpx.Response) -> dict:
            encoded_report = response.headers.get("X-PersonaWeave-Report")
            if not encoded_report:
                return {}
            try:
                decoded = json.loads(
                    base64.urlsafe_b64decode(
                        encoded_report + "=" * (-len(encoded_report) % 4)
                    )
                )
            except (ValueError, json.JSONDecodeError, binascii.Error):
                return {}
            return decoded if isinstance(decoded, dict) else {}

        async def checked_generation(edit_description: str) -> httpx.Response:
            try:
                return await generate_candidate(edit_description)
            except httpx.HTTPStatusError as error:
                try:
                    body = error.response.json()
                except (ValueError, json.JSONDecodeError):
                    body = None
                detail = body.get("detail") if isinstance(body, dict) else None
                provider_failure = provider_error_from_detail(detail)
                raise HTTPException(
                    provider_error_http_status(provider_failure),
                    detail=provider_failure.model_dump(mode="json", exclude_none=True),
                ) from error
            except httpx.HTTPError as error:
                provider_failure = provider_error("provider_unavailable")
                raise HTTPException(
                    provider_error_http_status(provider_failure),
                    detail=provider_failure.model_dump(mode="json", exclude_none=True),
                ) from error

        def anchored_generation_prompt(*, stage: str, model_prompt: str) -> str:
            return (
                "IMMUTABLE USER REQUIREMENT (must be satisfied in full):\n"
                f"{description}\n\n"
                "EXECUTION CONTRACT:\n"
                "Edit the original source image supplied with this request. Never edit or imitate "
                "a previous generated candidate. Keep the original source as the composition and "
                "preserve every constraint in the immutable user requirement.\n\n"
                f"{stage}:\n{model_prompt}"
            )

        response_headers = {"Cache-Control": "no-store"}
        source_uri = preview_data_uri(source_content, source.content_type)
        reference_uri = (
            preview_data_uri(reference_content, reference.content_type)
            if reference is not None and reference_content is not None
            else None
        )
        upstream: httpx.Response
        final_content: bytes
        final_media_type: str
        if settings.preview_strategy != "model_native":
            upstream = await checked_generation(description)
            final_content = upstream.content
            final_media_type = upstream.headers.get("content-type", "image/png")
            report = decode_gateway_report(upstream)
        else:
            iteration_limit = min(max_iterations, settings.preview_max_iterations)
            iterations = []
            stopped_reason = "max_iterations_reached"
            final_review = None
            try:
                plan = await yunwu.plan_image_edit(
                    source_uri=source_uri,
                    description=description,
                    reference_uri=reference_uri,
                )
            except ProviderNotConfigured as error:
                raise HTTPException(503, str(error)) from error
            except Exception as error:
                raise HTTPException(502, "multimodal edit planning failed") from error

            plan_report = {
                "observed_people": plan.observed_people,
                "intended_face_edits": plan.intended_face_edits,
                "warnings": plan.warnings,
                "edit_prompt_sha256": hashlib.sha256(plan.edit_prompt.encode()).hexdigest(),
            }
            current_prompt = anchored_generation_prompt(
                stage="MULTIMODAL INITIAL EDIT INSTRUCTION",
                model_prompt=plan.edit_prompt,
            )
            for iteration in range(1, iteration_limit + 1):
                prompt_sha256 = hashlib.sha256(current_prompt.encode()).hexdigest()
                upstream = await checked_generation(current_prompt)
                final_content = upstream.content
                final_media_type = upstream.headers.get("content-type", "image/png")
                try:
                    quality = await yunwu.review_image_edit(
                        source_uri=source_uri,
                        result_uri=preview_data_uri(final_content, final_media_type),
                        reference_uri=reference_uri,
                        description=description,
                        current_edit_prompt=current_prompt,
                        iteration=iteration,
                        max_iterations=iteration_limit,
                    )
                    final_review = {
                        "passed": quality.passed,
                        "scores": quality.scores.model_dump(),
                        "issues": [issue.model_dump() for issue in quality.issues],
                        "summary": quality.summary,
                        "revised_edit_prompt_sha256": (
                            hashlib.sha256(quality.revised_edit_prompt.encode()).hexdigest()
                            if quality.revised_edit_prompt
                            else None
                        ),
                    }
                except Exception as error:
                    final_review = {
                        "passed": False,
                        "provider": "yunwu",
                        "error_type": type(error).__name__,
                        "scores": {},
                        "issues": [],
                        "summary": "multimodal result review was unavailable",
                    }
                    stopped_reason = "review_unavailable"

                iterations.append(
                    {
                        "iteration": iteration,
                        "prompt_source": (
                            "initial_multimodal_plan"
                            if iteration == 1
                            else "multimodal_review_revision"
                        ),
                        "edit_prompt_sha256": prompt_sha256,
                        "generation": decode_gateway_report(upstream),
                        "multimodal_review": final_review,
                    }
                )
                if stopped_reason == "review_unavailable":
                    break
                if final_review["passed"]:
                    stopped_reason = "review_passed"
                    break
                if iteration < iteration_limit:
                    if not quality.revised_edit_prompt:
                        stopped_reason = "review_incomplete"
                        break
                    current_prompt = anchored_generation_prompt(
                        stage="MULTIMODAL REVIEW REVISED EDIT INSTRUCTION",
                        model_prompt=quality.revised_edit_prompt,
                    )

            final_iteration = iterations[-1]
            report = {
                "strategy": "model_native",
                "model": settings.yunwu_image_model,
                "max_iterations": iteration_limit,
                "completed_iterations": len(iterations),
                "stopped_reason": stopped_reason,
                "iterations": iterations,
                "multimodal_plan": plan_report,
                "multimodal_review": final_iteration["multimodal_review"],
            }
            if not final_review or not final_review["passed"]:
                response_headers["X-PersonaWeave-Needs-Review"] = "true"
        response_headers["X-PersonaWeave-Report"] = base64.urlsafe_b64encode(
            json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode()
        ).decode()
        return Response(
            content=final_content,
            media_type=final_media_type,
            headers=response_headers,
        )

    @router.post("/previews/face-plan")
    async def preview_face_plan(source: Annotated[UploadFile, File()]):
        if not settings.inference_base_url:
            raise HTTPException(503, "inference gateway is not configured")
        source_content = await source.read(settings.max_upload_bytes + 1)
        if len(source_content) > settings.max_upload_bytes:
            raise HTTPException(413, "preview image exceeds configured size limit")
        if not source_content or not (source.content_type or "").startswith("image/"):
            raise HTTPException(415, "face-plan preview requires a supported image upload")
        headers = {}
        if settings.inference_api_key:
            headers["Authorization"] = f"Bearer {settings.inference_api_key}"
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                upstream = await client.post(
                    f"{settings.inference_base_url.rstrip('/')}/v1/preview-face-plan",
                    headers=headers,
                    files={
                        "source": (
                            source.filename or "source.png",
                            source_content,
                            source.content_type,
                        )
                    },
                )
                upstream.raise_for_status()
        except httpx.HTTPStatusError as error:
            try:
                body = error.response.json()
            except (ValueError, json.JSONDecodeError):
                body = None
            detail = body.get("detail") if isinstance(body, dict) else None
            provider_failure = provider_error_from_detail(detail)
            raise HTTPException(
                provider_error_http_status(provider_failure),
                detail=provider_failure.model_dump(mode="json", exclude_none=True),
            ) from error
        except httpx.HTTPError as error:
            provider_failure = provider_error("provider_unavailable")
            raise HTTPException(
                provider_error_http_status(provider_failure),
                detail=provider_failure.model_dump(mode="json", exclude_none=True),
            ) from error
        response_headers = {"Cache-Control": "no-store"}
        if report := upstream.headers.get("X-PersonaWeave-Report"):
            response_headers["X-PersonaWeave-Report"] = report
        return Response(
            content=upstream.content,
            media_type=upstream.headers.get("content-type", "image/png"),
            headers=response_headers,
        )

    @router.get("/assets/{asset_id}/content")
    def get_asset_content(asset_id: str):
        try:
            asset = repository.get_asset(asset_id)
        except KeyError:
            raise HTTPException(404, "asset not found") from None
        try:
            local_path = asset_resolver.local_path(asset["storage_uri"])
        except ValueError as error:
            raise HTTPException(403, "asset path is outside configured storage") from error
        if local_path is not None:
            if not local_path.is_file():
                raise HTTPException(404, "asset content not found")
            return FileResponse(
                local_path,
                media_type=asset["media_type"],
                filename=asset["filename"],
            )
        if asset["storage_uri"].startswith(("https://", "http://")):
            return RedirectResponse(asset["storage_uri"], status_code=307)
        raise HTTPException(404, "asset content is not available")

    @router.get("/batches/{batch_id}/formal-masks/{sha256}/content")
    def get_formal_mask_content(
        batch_id: str,
        sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
    ):
        try:
            mask = repository.get_current_formal_mask(batch_id, sha256)
            local_path = asset_resolver.local_path(mask["storage_uri"])
        except KeyError:
            raise HTTPException(404, "formal mask not found") from None
        except ValueError as error:
            raise HTTPException(403, "formal mask path is outside configured storage") from error
        if local_path is None or not local_path.is_file():
            raise HTTPException(404, "formal mask content not found")
        if hashlib.sha256(local_path.read_bytes()).hexdigest() != sha256:
            raise HTTPException(409, "formal mask content digest is stale")
        return FileResponse(
            local_path,
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @router.get("/faces/{face_id}/crop")
    def get_face_crop(face_id: str) -> Response:
        try:
            face = repository.get_face(face_id)
            asset = repository.get_asset(face["asset_id"])
        except KeyError:
            raise HTTPException(404, "face crop not found") from None
        try:
            local_path = asset_resolver.local_path(asset["storage_uri"])
        except ValueError as error:
            raise HTTPException(403, "asset path is outside configured storage") from error
        if local_path is None or not local_path.is_file():
            raise HTTPException(404, "face crop source is unavailable")

        bbox = face["bbox"]
        with Image.open(local_path) as image:
            image.seek(0)
            source = ImageOps.exif_transpose(image)
            padding_x = round(bbox["width"] * 0.15)
            padding_y = round(bbox["height"] * 0.15)
            left = max(0, bbox["x"] - padding_x)
            top = max(0, bbox["y"] - padding_y)
            right = min(source.width, bbox["x"] + bbox["width"] + padding_x)
            bottom = min(source.height, bbox["y"] + bbox["height"] + padding_y)
            crop = source.crop((left, top, right, bottom)).convert("RGB")
            crop.thumbnail((512, 512), Image.Resampling.LANCZOS)
            output = BytesIO()
            crop.save(output, format="JPEG", quality=90, optimize=True)
        return Response(
            content=output.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.get("/faces/{face_id}/localization-preview")
    def get_face_localization_preview(face_id: str) -> Response:
        try:
            face = repository.get_face(face_id)
            asset = repository.get_asset(face["asset_id"])
        except KeyError:
            raise HTTPException(404, "face localization preview not found") from None
        attempt = face.get("latest_localization_attempt")
        if not attempt or (
            attempt.get("accepted")
            and not source_face_condition_requires_review(
                attempt.get("source_face_condition"),
                legacy_unrecoverable_occlusion=bool(
                    attempt.get("unrecoverable_occlusion")
                ),
            )
        ):
            raise HTTPException(404, "failed localization candidate not found")
        try:
            local_path = asset_resolver.local_path(asset["storage_uri"])
        except ValueError as error:
            raise HTTPException(403, "asset path is outside configured storage") from error
        if local_path is None or not local_path.is_file():
            raise HTTPException(404, "face crop source is unavailable")
        bbox = attempt["bbox"]
        with Image.open(local_path) as image:
            image.seek(0)
            source = ImageOps.exif_transpose(image)
            padding_x = round(bbox["width"] * 0.15)
            padding_y = round(bbox["height"] * 0.15)
            crop = source.crop((
                max(0, bbox["x"] - padding_x),
                max(0, bbox["y"] - padding_y),
                min(source.width, bbox["x"] + bbox["width"] + padding_x),
                min(source.height, bbox["y"] + bbox["height"] + padding_y),
            )).convert("RGB")
            crop.thumbnail((512, 512), Image.Resampling.LANCZOS)
            output = BytesIO()
            crop.save(output, format="JPEG", quality=90, optimize=True)
        return Response(
            content=output.getvalue(),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/batches", status_code=201)
    def create_batch(payload: BatchCreate) -> dict:
        return public_batch(repository.create_batch(
            payload.name,
            payload.task_instruction,
            payload.max_iterations,
        ))

    @router.put("/batches/{batch_id}/spec")
    def update_batch_spec(batch_id: str, payload: TaskSpecUpdate) -> dict:
        try:
            batch = repository.get_batch(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        if batch["status"] in {"swapping", "validating", "review", "completed"}:
            raise HTTPException(409, "task instruction is locked after generation starts")

        known_codes = {
            *(asset["display_code"] for asset in repository.list_assets(batch_id)),
            *(identity["display_code"] for identity in repository.list_identities(batch_id)),
            *(person["display_code"] for person in repository.list_persons(batch_id)),
        }
        mentioned_codes = set(re.findall(r"@([IPTRO]\d+)\b", payload.task_instruction.upper()))
        unknown = sorted(mentioned_codes - known_codes)
        if unknown:
            raise HTTPException(422, f"unknown task references: {', '.join('@' + code for code in unknown)}")
        return public_batch(repository.update_batch_spec(
            batch_id,
            payload.task_instruction.strip(),
            payload.max_iterations,
        ))

    @router.get("/batches")
    def list_batches() -> list[dict]:
        return [public_batch(batch) for batch in repository.list_batches()]

    @router.delete("/batches/{batch_id}", status_code=204)
    def delete_batch(batch_id: str) -> Response:
        try:
            repository.trash_batch(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        return Response(status_code=204)

    @router.get("/recycle-bin")
    def list_recycle_bin() -> dict:
        deleted_batches = []
        for batch in repository.list_deleted_batches():
            assets = repository.list_assets(batch["id"])
            preview_assets = [
                asset for asset in assets if asset["kind"] in {AssetKind.SOURCE, AssetKind.RESULT}
            ][:6]
            deleted_batches.append({
                "kind": "batch",
                "id": batch["id"],
                "name": batch["name"],
                "status": batch["status"],
                "deleted_at": batch["deleted_at"],
                "restorable": True,
                "source_count": sum(asset["kind"] == AssetKind.SOURCE for asset in assets),
                "result_count": sum(asset["kind"] == AssetKind.RESULT for asset in assets),
                "preview_urls": [public_asset(asset)["content_url"] for asset in preview_assets],
            })
        legacy = []
        trash_root = (settings.asset_root.resolve() / ".trash").resolve()
        if trash_root.exists():
            for directory in sorted(trash_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True):
                if not directory.is_dir():
                    continue
                manifest = load_legacy_manifest(directory)
                source_entries = manifest.get("sources", []) if manifest else []
                result_entries = manifest.get("results", []) if manifest else []
                preview_entries = [*source_entries[:3], *result_entries[:3]]
                legacy.append({
                    "kind": "legacy",
                    "id": directory.name,
                    "name": manifest["name"] if manifest else f"历史删除任务 {directory.name[:8]}",
                    "status": "legacy_files_only",
                    "deleted_at": (
                        manifest.get("deleted_at")
                        if manifest and isinstance(manifest.get("deleted_at"), str)
                        else datetime.fromtimestamp(directory.stat().st_mtime, UTC).isoformat()
                    ),
                    "restorable": manifest is not None,
                    "source_count": len(source_entries),
                    "result_count": len(result_entries),
                    "preview_urls": [
                        f"/api/v1/recycle-bin/legacy/{directory.name}/files/"
                        + quote(str(entry["path"]), safe="/")
                        for entry in preview_entries
                        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    ],
                })
        return {"items": [*deleted_batches, *legacy]}

    @router.post("/recycle-bin/batches/{batch_id}/restore")
    def restore_batch(batch_id: str) -> dict:
        try:
            return public_batch(repository.restore_batch(batch_id))
        except KeyError:
            raise HTTPException(404, "deleted batch not found") from None

    @router.get("/recycle-bin/legacy/{entry_name}/files/{file_path:path}")
    def preview_legacy_trash_file(entry_name: str, file_path: str):
        directory = legacy_trash_directory(entry_name)
        path = safe_legacy_file(directory, file_path)
        return FileResponse(
            path,
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.post("/recycle-bin/legacy/{entry_name}/restore", status_code=201)
    def restore_legacy_trash(entry_name: str) -> dict:
        directory = legacy_trash_directory(entry_name)
        manifest = load_legacy_manifest(directory)
        if manifest is None:
            raise HTTPException(409, "legacy task metadata is unavailable")
        source_entries = manifest.get("sources")
        result_entries = manifest.get("results", [])
        if not isinstance(source_entries, list) or not source_entries:
            raise HTTPException(409, "legacy task has no recoverable source images")
        if not isinstance(result_entries, list):
            raise HTTPException(409, "legacy result metadata is invalid")
        for entry in [*source_entries, *result_entries]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise HTTPException(409, "legacy task manifest is invalid")
            safe_legacy_file(directory, entry["path"])

        batch = repository.create_batch(
            str(manifest["name"]),
            str(manifest.get("task_instruction", "")),
            int(manifest.get("max_iterations", 3)),
        )
        destination = (settings.asset_root.resolve() / batch["id"]).resolve()
        if destination.parent != settings.asset_root.resolve() or destination.exists():
            repository.trash_batch(batch["id"])
            repository.delete_batch(batch["id"])
            raise HTTPException(409, "restore destination already exists")
        directory.replace(destination)
        restored_sources: dict[str, dict] = {}
        try:
            for entry in source_entries:
                path = safe_legacy_file(destination, entry["path"])
                asset = repository.add_asset(
                    batch_id=batch["id"],
                    kind=AssetKind.SOURCE,
                    filename=str(entry.get("filename") or path.name),
                    media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                    storage_uri=path.as_uri(),
                    sha256=sha256_file(path),
                )
                restored_sources[str(entry.get("display_code") or asset["display_code"])] = asset
            for entry in result_entries:
                path = safe_legacy_file(destination, entry["path"])
                parent = restored_sources.get(str(entry.get("parent_source_code", "")))
                repository.add_asset(
                    batch_id=batch["id"],
                    parent_asset_id=parent["id"] if parent else None,
                    kind=AssetKind.RESULT,
                    filename=str(entry.get("filename") or path.name),
                    media_type=mimetypes.guess_type(path.name)[0] or "image/png",
                    storage_uri=path.as_uri(),
                    sha256=sha256_file(path),
                )
            (destination / ".personaweave-trash.json").unlink(missing_ok=True)
        except Exception:
            repository.trash_batch(batch["id"])
            repository.delete_batch(batch["id"])
            if destination.exists():
                destination.replace(directory)
            raise HTTPException(500, "failed to restore legacy task") from None
        return public_batch(repository.get_batch(batch["id"]))

    @router.delete("/recycle-bin/batches/{batch_id}", status_code=204)
    def permanently_delete_batch(batch_id: str) -> Response:
        try:
            batch = repository.get_batch(batch_id, include_deleted=True)
        except KeyError:
            raise HTTPException(404, "deleted batch not found") from None
        if not batch.get("deleted_at"):
            raise HTTPException(409, "batch must be in recycle bin before permanent deletion")
        asset_root = settings.asset_root.resolve()
        batch_directory = (asset_root / batch_id).resolve()
        if batch_directory.parent != asset_root:
            raise HTTPException(400, "invalid batch storage path")
        purge_root = (asset_root / ".purging").resolve()
        purged_directory = None
        if batch_directory.exists():
            purge_root.mkdir(parents=True, exist_ok=True)
            purged_directory = purge_root / f"{batch_id}-{uuid4()}"
            batch_directory.replace(purged_directory)
        try:
            repository.delete_batch(batch_id)
        except Exception:
            if purged_directory is not None and purged_directory.exists():
                purged_directory.replace(batch_directory)
            raise
        if purged_directory is not None:
            shutil.rmtree(purged_directory)
        return Response(status_code=204)

    @router.delete("/recycle-bin/legacy/{entry_name}", status_code=204)
    def permanently_delete_legacy_trash(entry_name: str) -> Response:
        target = legacy_trash_directory(entry_name)
        shutil.rmtree(target)
        return Response(status_code=204)

    @router.get("/material-library")
    def list_material_library() -> list[dict]:
        return [public_library_item(item) for item in repository.list_library_items()]

    @router.post("/material-library", status_code=201)
    def create_material_library_item(
        label: Annotated[str, Form(min_length=1, max_length=120)],
        image: Annotated[UploadFile, File()],
    ) -> dict:
        item = repository.create_library_item(label.strip())
        try:
            uri, digest, media_type = save_upload("_library", image, item["id"])
            repository.add_library_asset(
                item_id=item["id"],
                filename=image.filename or "reference",
                media_type=media_type,
                storage_uri=uri,
                sha256=digest,
            )
            return public_library_item(repository.get_library_item(item["id"]))
        except Exception:
            repository.delete_library_item(item["id"])
            shutil.rmtree(settings.asset_root / "_library" / item["id"], ignore_errors=True)
            raise

    @router.post("/material-library/{item_id}/images", status_code=201)
    def add_material_library_image(
        item_id: str,
        image: Annotated[UploadFile, File()],
    ) -> dict:
        try:
            repository.get_library_item(item_id)
        except KeyError:
            raise HTTPException(404, "material not found") from None
        uri, digest, media_type = save_upload("_library", image, item_id)
        return public_library_asset(repository.add_library_asset(
            item_id=item_id,
            filename=image.filename or "reference",
            media_type=media_type,
            storage_uri=uri,
            sha256=digest,
        ))

    @router.get("/material-library/assets/{asset_id}/content")
    def get_material_library_content(asset_id: str):
        try:
            asset = repository.get_library_asset(asset_id)
            path = asset_resolver.local_path(asset["storage_uri"])
        except KeyError:
            raise HTTPException(404, "material image not found") from None
        except ValueError as error:
            raise HTTPException(403, "material path is outside configured storage") from error
        if path is None or not path.is_file():
            raise HTTPException(404, "material image content not found")
        return FileResponse(path, media_type=asset["media_type"], filename=asset["filename"])

    @router.post("/material-library/{item_id}/use", status_code=201)
    def use_material_library_item(item_id: str, payload: LibraryUseRequest) -> dict:
        try:
            item = repository.get_library_item(item_id)
            batch = repository.get_batch(payload.batch_id)
        except KeyError:
            raise HTTPException(404, "material or batch not found") from None
        if batch["status"] not in {"draft", "awaiting_confirmation"}:
            raise HTTPException(409, "materials can only be added before mappings are confirmed")
        if not item["assets"]:
            raise HTTPException(409, "material has no reference images")
        identity = repository.create_identity(payload.batch_id, item["label"])
        destination_directory = (
            settings.asset_root / payload.batch_id / "references" / identity["id"]
        ).resolve()
        try:
            destination_directory.mkdir(parents=True, exist_ok=True)
            for library_asset in item["assets"]:
                source_path = asset_resolver.local_path(library_asset["storage_uri"])
                if source_path is None or not source_path.is_file():
                    raise FileNotFoundError(library_asset["filename"])
                destination = destination_directory / (
                    library_asset["sha256"] + source_path.suffix.lower()
                )
                if not destination.exists():
                    shutil.copy2(source_path, destination)
                repository.add_asset(
                    batch_id=payload.batch_id,
                    identity_id=identity["id"],
                    kind=AssetKind.REFERENCE,
                    filename=library_asset["filename"],
                    media_type=library_asset["media_type"],
                    storage_uri=destination.resolve().as_uri(),
                    sha256=library_asset["sha256"],
                )
        except Exception:
            repository.delete_identity(identity["id"])
            shutil.rmtree(destination_directory, ignore_errors=True)
            raise HTTPException(500, "failed to copy material into batch") from None
        identity = repository.get_identity(identity["id"])
        identity["references"] = repository.list_assets_for_identity(identity["id"])
        return public_identity(identity)

    @router.delete("/material-library/{item_id}", status_code=204)
    def delete_material_library_item(item_id: str) -> Response:
        try:
            repository.get_library_item(item_id)
        except KeyError:
            raise HTTPException(404, "material not found") from None
        library_root = (settings.asset_root.resolve() / "_library").resolve()
        item_directory = (library_root / item_id).resolve()
        if item_directory.parent != library_root:
            raise HTTPException(400, "invalid material storage path")
        quarantine = None
        if item_directory.exists():
            quarantine_root = (settings.asset_root.resolve() / ".library-purging").resolve()
            quarantine_root.mkdir(parents=True, exist_ok=True)
            quarantine = quarantine_root / f"{item_id}-{uuid4()}"
            item_directory.replace(quarantine)
        try:
            repository.delete_library_item(item_id)
        except Exception:
            if quarantine is not None and quarantine.exists():
                quarantine.replace(item_directory)
            raise
        if quarantine is not None:
            shutil.rmtree(quarantine)
        return Response(status_code=204)

    @router.get("/batches/{batch_id}")
    def get_batch(batch_id: str) -> dict:
        try:
            batch = repository.get_batch(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        result = public_batch(batch)
        result["assets"] = [
            public_asset(asset) for asset in repository.list_assets(batch_id)
        ]
        result["identities"] = [
            public_identity(identity) for identity in repository.list_identities(batch_id)
        ]
        result["persons"] = [
            public_person(person) for person in repository.list_persons(batch_id)
        ]
        result["jobs"] = [public_job(job) for job in repository.list_jobs(batch_id)]
        result["latest_jobs"] = [
            public_job(job) for job in repository.latest_jobs(batch_id)
        ]
        return result

    @router.get("/batches/{batch_id}/progress")
    def get_batch_progress(batch_id: str) -> dict:
        try:
            return repository.batch_progress(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None

    @router.post("/batches/{batch_id}/images", status_code=201)
    def upload_source_image(
        batch_id: str,
        image: Annotated[UploadFile, File()],
    ) -> dict:
        try:
            batch = repository.get_batch(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        if batch["status"] != "draft":
            raise HTTPException(409, "source images can only be changed while the batch is draft")
        uri, digest, media_type = save_upload(batch_id, image, "source")
        return public_asset(repository.add_asset(
            batch_id=batch_id,
            kind=AssetKind.SOURCE,
            filename=image.filename or "image",
            media_type=media_type,
            storage_uri=uri,
            sha256=digest,
        ))

    @router.delete("/batches/{batch_id}/assets/{asset_id}", status_code=204)
    def delete_batch_asset(batch_id: str, asset_id: str) -> Response:
        try:
            batch = repository.get_batch(batch_id)
            asset = repository.get_asset(asset_id)
        except KeyError:
            raise HTTPException(404, "asset not found") from None
        if asset["batch_id"] != batch_id:
            raise HTTPException(404, "asset not found")
        if asset["kind"] not in {AssetKind.SOURCE, AssetKind.REFERENCE}:
            raise HTTPException(409, "only source and reference images can be removed")
        if asset["kind"] == AssetKind.SOURCE and batch["status"] != "draft":
            raise HTTPException(409, "source images can only be removed while the batch is draft")
        if asset["kind"] == AssetKind.REFERENCE and batch["status"] not in {
            "draft",
            "awaiting_confirmation",
        }:
            raise HTTPException(
                409,
                "reference images can only be removed before mappings are confirmed",
            )

        deleted, remaining_references = repository.delete_asset(asset_id)
        if remaining_references == 0:
            try:
                local_path = asset_resolver.local_path(deleted["storage_uri"])
            except ValueError as error:
                raise HTTPException(403, "asset path is outside configured storage") from error
            if local_path is not None:
                local_path.unlink(missing_ok=True)
        return Response(status_code=204)

    @router.post("/batches/{batch_id}/identities", status_code=201)
    def create_identity(batch_id: str, payload: IdentityCreate) -> dict:
        try:
            batch = repository.get_batch(batch_id)
            if batch["status"] not in {"draft", "awaiting_confirmation"}:
                raise HTTPException(
                    409, "target identities can only be changed before mappings are confirmed"
                )
            return repository.create_identity(batch_id, payload.label)
        except KeyError:
            raise HTTPException(404, "batch not found") from None

    @router.delete("/batches/{batch_id}/identities/{identity_id}", status_code=204)
    def delete_batch_identity(batch_id: str, identity_id: str) -> Response:
        try:
            batch = repository.get_batch(batch_id)
            identity = repository.get_identity(identity_id)
        except KeyError:
            raise HTTPException(404, "target identity not found") from None
        if identity["batch_id"] != batch_id:
            raise HTTPException(404, "target identity not found")
        if batch["status"] not in {"draft", "awaiting_confirmation"}:
            raise HTTPException(409, "目标人物只能在任务生成开始前移除")

        references = repository.list_assets_for_identity(identity_id)
        owned_codes = {
            identity["display_code"],
            *(reference["display_code"] for reference in references),
        }
        mentioned_codes = set(
            re.findall(r"@([IPTRO]\d+)\b", batch["task_instruction"].upper())
        )
        referenced_codes = sorted(owned_codes & mentioned_codes)
        if referenced_codes:
            formatted = "、".join(f"@{code}" for code in referenced_codes)
            raise HTTPException(
                409,
                f"任务要求仍引用 {formatted}，请先移除这些引用再删除目标人物",
            )

        reference_root = (settings.asset_root / batch_id / "references").resolve()
        identity_directory = (reference_root / identity_id).resolve()
        if identity_directory.parent != reference_root:
            raise HTTPException(400, "invalid target identity storage path")
        quarantine = None
        if identity_directory.exists():
            quarantine = reference_root / f".deleting-{identity_id}-{uuid4()}"
            identity_directory.rename(quarantine)
        try:
            repository.delete_identity(identity_id)
        except sqlite3.IntegrityError as error:
            if quarantine is not None and quarantine.exists():
                quarantine.rename(identity_directory)
            raise HTTPException(409, "该目标人物仍被人物映射使用，请先取消对应映射") from error
        except KeyError:
            if quarantine is not None and quarantine.exists():
                quarantine.rename(identity_directory)
            raise HTTPException(404, "target identity not found") from None

        if quarantine is not None:
            shutil.rmtree(quarantine, ignore_errors=True)
        return Response(status_code=204)

    @router.post("/batches/{batch_id}/identities/{identity_id}/references", status_code=201)
    def upload_reference_image(
        batch_id: str,
        identity_id: str,
        image: Annotated[UploadFile, File()],
    ) -> dict:
        try:
            identity = repository.get_identity(identity_id)
            if identity["batch_id"] != batch_id:
                raise ValueError("identity does not belong to batch")
            batch = repository.get_batch(batch_id)
            if batch["status"] not in {"draft", "awaiting_confirmation"}:
                raise HTTPException(
                    409, "reference images can only be changed before mappings are confirmed"
                )
            uri, digest, media_type = save_upload(batch_id, image, f"references/{identity_id}")
            return public_asset(repository.add_asset(
                batch_id=batch_id,
                identity_id=identity_id,
                kind=AssetKind.REFERENCE,
                filename=image.filename or "reference",
                media_type=media_type,
                storage_uri=uri,
                sha256=digest,
            ))
        except HTTPException:
            raise
        except (KeyError, ValueError) as error:
            raise HTTPException(400, "identity does not belong to batch") from error

    @router.post("/batches/{batch_id}/analysis/jobs", status_code=202)
    def start_analysis(batch_id: str) -> dict:
        try:
            jobs = workflow.start_analysis(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except InvalidTransition as error:
            raise HTTPException(409, str(error)) from error
        return {"batch_id": batch_id, "jobs": [public_job(job) for job in jobs]}

    @router.post("/batches/{batch_id}/analysis/run")
    async def run_analysis(batch_id: str) -> dict:
        """Create and execute all analysis jobs as one workspace action."""
        try:
            workflow.start_analysis(batch_id)
            return await executor.execute_stage(batch_id, JobStage.ANALYZE)
        except KeyError:
            raise HTTPException(404, "batch or job not found") from None
        except ProviderNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except (InvalidTransition, StageExecutionError) as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/analysis/rerun")
    async def rerun_analysis(batch_id: str) -> dict:
        """Discard derived people and rerun analysis while retaining source assets and history."""
        try:
            workflow.reset_analysis(batch_id)
            return await executor.execute_stage(batch_id, JobStage.ANALYZE)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except ProviderNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except (InvalidTransition, StageExecutionError) as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/task/compile")
    async def compile_task_contract(
        batch_id: str,
        payload: TaskContractCompile | None = None,
    ) -> dict:
        try:
            repository.start_contract_compilation(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        try:
            return public_batch(await workflow.compile_task_contract(
                batch_id,
                payload.decisions if payload else None,
            ))
        except Exception as error:
            status_code, message = contract_compilation_failure(error)
            try:
                repository.fail_contract_compilation(batch_id, message)
            except KeyError:
                raise HTTPException(404, "batch not found") from None
            raise HTTPException(status_code, message) from error

    @router.post("/batches/{batch_id}/task/confirm")
    def confirm_task_contract(
        batch_id: str,
        payload: TaskContractConfirmation,
    ) -> dict:
        try:
            return public_batch(workflow.confirm_task_contract(
                batch_id,
                payload.contract_hash,
                payload.decisions,
                payload.acknowledged_mask_sha256s,
            ))
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except (InvalidTransition, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/task/run")
    async def run_task_loop(batch_id: str) -> dict:
        """Run only a previously hash-confirmed formal task contract."""
        try:
            batch = repository.get_batch(batch_id)
            if batch["status"] == BatchStatus.READY:
                workflow.start_swap(batch_id)
                batch = repository.get_batch(batch_id)
            elif batch["status"] == BatchStatus.AWAITING_CONFIRMATION:
                raise InvalidTransition(
                    "task contract must be compiled and explicitly hash-confirmed before generation"
                )

            swap_result = None
            if batch["status"] == BatchStatus.SWAPPING:
                swap_result = await executor.execute_stage(batch_id, JobStage.SWAP)
                if not swap_result.get("completed"):
                    return {
                        "batch_id": batch_id,
                        "completed": False,
                        "stage": JobStage.SWAP,
                        "swap": swap_result,
                    }
                batch = repository.get_batch(batch_id)

            if batch["status"] != BatchStatus.VALIDATING:
                raise InvalidTransition(f"cannot run task loop from {batch['status']}")
            validation_result = await executor.execute_stage(batch_id, JobStage.VALIDATE)
            return {
                "batch_id": batch_id,
                "completed": bool(validation_result.get("completed")),
                "stage": JobStage.VALIDATE,
                "swap": swap_result,
                "validation": validation_result,
            }
        except KeyError:
            raise HTTPException(404, "batch or job not found") from None
        except ProviderNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except (InvalidTransition, StageExecutionError, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/analysis/complete")
    def complete_analysis(batch_id: str, payload: AnalysisCompletion) -> dict:
        try:
            workflow.complete_analysis(
                batch_id,
                [result.model_dump(mode="json") for result in payload.results],
            )
            return public_batch(repository.get_batch(batch_id))
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except (InvalidTransition, ValueError) as error:
            raise HTTPException(409, str(error)) from error


    @router.post("/batches/{batch_id}/persons/merge")
    def merge_people(batch_id: str, payload: PersonMergeRequest) -> dict:
        try:
            batch = repository.get_batch(batch_id)
            if batch["status"] != "awaiting_confirmation":
                raise HTTPException(409, "people can only be merged during mapping confirmation")
            return public_person(repository.merge_persons(batch_id, payload.person_ids))
        except HTTPException:
            raise
        except (KeyError, ValueError) as error:
            raise HTTPException(400, str(error)) from error

    @router.post("/batches/{batch_id}/persons/fuse")
    async def fuse_people(batch_id: str) -> dict:
        try:
            return await executor.fuse_existing_people(batch_id)
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except ProviderNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except (InvalidTransition, StageExecutionError) as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/persons/{person_id}/relocalize")
    async def relocalize_person(batch_id: str, person_id: str) -> dict:
        try:
            return await executor.relocalize_person(batch_id, person_id)
        except KeyError:
            raise HTTPException(404, "batch or person not found") from None
        except ProviderNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except ValidationError as error:
            raise HTTPException(
                409,
                "云端模型返回的人物定位复核结构不符合要求，请重试。",
            ) from error
        except (InvalidTransition, ValueError) as error:
            raise HTTPException(409, str(error)) from error

    @router.delete("/batches/{batch_id}/persons/{person_id}", status_code=204)
    def delete_person(batch_id: str, person_id: str) -> Response:
        try:
            batch = repository.get_batch(batch_id)
            if batch["status"] != "awaiting_confirmation":
                raise HTTPException(
                    409,
                    "extracted people can only be removed during person analysis confirmation",
                )
            deleted = repository.delete_person(batch_id, person_id)
            cleaned_instruction = re.sub(
                rf"@{re.escape(deleted['display_code'])}\b",
                "",
                batch["task_instruction"],
                flags=re.IGNORECASE,
            )
            cleaned_instruction = re.sub(r"[ \t]{2,}", " ", cleaned_instruction).strip()
            if cleaned_instruction != batch["task_instruction"]:
                repository.update_batch_spec(
                    batch_id,
                    cleaned_instruction,
                    batch["max_iterations"],
                )
            return Response(status_code=204)
        except HTTPException:
            raise
        except KeyError:
            raise HTTPException(404, "person not found") from None

    @router.post("/batches/{batch_id}/persons/{person_id}/split")
    def split_person(batch_id: str, person_id: str, payload: PersonSplitRequest) -> dict:
        try:
            batch = repository.get_batch(batch_id)
            if batch["status"] != "awaiting_confirmation":
                raise HTTPException(409, "people can only be split during mapping confirmation")
            return public_person(
                repository.split_person_faces(batch_id, person_id, payload.face_ids)
            )
        except HTTPException:
            raise
        except (KeyError, ValueError) as error:
            raise HTTPException(400, str(error)) from error

    @router.put("/batches/{batch_id}/faces/{face_id}/resolution")
    def resolve_face_occurrence(
        batch_id: str,
        face_id: str,
        payload: FaceOccurrenceResolution,
    ) -> dict:
        try:
            face = repository.get_face(face_id)
            if face["batch_id"] != batch_id:
                raise KeyError(face_id)
            bbox = payload.bbox
            if payload.action == "manual_bbox":
                if bbox is None:
                    raise ValueError("manual_bbox requires bbox")
                asset = repository.get_asset(face["asset_id"])
                path = asset_resolver.local_path(asset["storage_uri"])
                if path is None or not path.is_file():
                    raise ValueError("source image is not locally available")
                with Image.open(path) as image:
                    width, height = ImageOps.exif_transpose(image).size
                if bbox.x + bbox.width > width or bbox.y + bbox.height > height:
                    raise ValueError("manual face bbox exceeds source image bounds")
                for existing in repository.list_faces_for_asset(face["asset_id"]):
                    if existing["id"] == face_id:
                        continue
                    other = BoundingBox.model_validate(existing["bbox"])
                    if bbox_iou(bbox, other) >= 0.35:
                        raise ValueError("manual face overlaps another detection")
            elif bbox is not None:
                raise ValueError(f"{payload.action} must not include bbox")
            if payload.action == "accept_occlusion":
                attempt = face.get("latest_localization_attempt")
                if not attempt or not source_face_condition_requires_review(
                    attempt.get("source_face_condition"),
                    legacy_unrecoverable_occlusion=bool(
                        attempt.get("unrecoverable_occlusion")
                    ),
                ):
                    raise ValueError(
                        "accept_occlusion requires a confirmed source-image occlusion"
                    )
            resolved = repository.resolve_face_occurrence(
                batch_id,
                face_id,
                payload.action,
                bbox.model_dump(mode="json") if bbox is not None else None,
            )
            person = next(
                item
                for item in repository.list_persons(batch_id)
                if item["id"] == resolved["person_id"]
            )
            return {
                "person": public_person(person),
                "analysis_review_issues": repository.analysis_review_issues(batch_id),
            }
        except KeyError:
            raise HTTPException(404, "batch or face occurrence not found") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/assets/{asset_id}/faces", status_code=201)
    def add_manual_face(
        batch_id: str,
        asset_id: str,
        payload: ManualFaceCreate,
    ) -> dict:
        try:
            batch = repository.get_batch(batch_id)
            if batch["status"] != BatchStatus.AWAITING_CONFIRMATION:
                raise HTTPException(
                    409,
                    "manual faces can only be added during person analysis confirmation",
                )
            asset = repository.get_asset(asset_id)
            if asset["batch_id"] != batch_id or asset["kind"] != AssetKind.SOURCE:
                raise ValueError("source image does not belong to batch")
            path = asset_resolver.local_path(asset["storage_uri"])
            if path is None or not path.is_file():
                raise ValueError("source image is not locally available")
            with Image.open(path) as image:
                width, height = ImageOps.exif_transpose(image).size
            bbox = payload.bbox
            if bbox.x + bbox.width > width or bbox.y + bbox.height > height:
                raise ValueError("manual face bbox exceeds source image bounds")
            for existing in repository.list_faces_for_asset(asset_id):
                other = BoundingBox.model_validate(existing["bbox"])
                if bbox_iou(bbox, other) >= 0.35:
                    raise ValueError("manual face overlaps an existing detection")
            return public_person(repository.add_manual_face(
                batch_id,
                asset_id,
                bbox.model_dump(mode="json"),
                payload.person_id,
            ))
        except HTTPException:
            raise
        except KeyError:
            raise HTTPException(404, "batch, source image or person not found") from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.post("/batches/{batch_id}/swap/jobs", status_code=202)
    def start_swap(batch_id: str) -> dict:
        try:
            jobs = workflow.start_swap(batch_id)
            return {"batch_id": batch_id, "jobs": [public_job(job) for job in jobs]}
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except InvalidTransition as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/swap/complete", status_code=202)
    def complete_swap(batch_id: str, payload: SwapCompletion) -> dict:
        try:
            jobs = workflow.complete_swap(batch_id, payload.results)
            return {
                "batch_id": batch_id,
                "validation_jobs": [public_job(job) for job in jobs],
            }
        except KeyError:
            raise HTTPException(404, "batch or asset not found") from None
        except (InvalidTransition, ValueError) as error:
            raise HTTPException(409, str(error)) from error


    @router.post("/batches/{batch_id}/approve")
    def approve_batch(batch_id: str) -> dict:
        try:
            workflow.approve_batch(batch_id)
            return public_batch(repository.get_batch(batch_id))
        except KeyError:
            raise HTTPException(404, "batch not found") from None
        except InvalidTransition as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/batches/{batch_id}/stages/{stage}/execute")
    async def execute_stage(batch_id: str, stage: JobStage) -> dict:
        try:
            return await executor.execute_stage(batch_id, stage)
        except KeyError:
            raise HTTPException(404, "batch or job not found") from None
        except ProviderNotConfigured as error:
            raise HTTPException(503, str(error)) from error
        except (InvalidTransition, StageExecutionError) as error:
            raise HTTPException(409, str(error)) from error

    @router.post("/jobs/{job_id}/retry", status_code=201)
    def retry_job(job_id: str) -> dict:
        try:
            job = repository.get_job(job_id)
            input_updates = None
            if job["stage"] == JobStage.SWAP:
                input_updates = {
                    "image_model": settings.formal_image_model,
                    "image_api_mode": settings.formal_image_api_mode,
                    "image_api_path": settings.formal_image_api_path,
                    "image_output_size": settings.formal_image_size,
                    "image_response_format": settings.formal_image_response_format,
                }
            retried = repository.retry_job(job_id, input_updates=input_updates)
            retry_status = {
                JobStage.ANALYZE: BatchStatus.ANALYZING,
                JobStage.SWAP: BatchStatus.SWAPPING,
                JobStage.VALIDATE: BatchStatus.VALIDATING,
            }[JobStage(job["stage"])]
            repository.set_batch_status(job["batch_id"], retry_status)
            return public_job(retried)
        except KeyError:
            raise HTTPException(404, "job not found") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router
