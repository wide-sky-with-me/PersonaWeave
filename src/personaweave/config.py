from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PERSONAWEAVE_",
        extra="ignore",
    )

    database_path: Path = Path("./data/personaweave.sqlite3")
    asset_root: Path = Path("./data/assets")
    asset_legacy_root: Path | None = None

    yunwu_api_key: str | None = Field(default=None, repr=False)
    yunwu_base_url: str = "https://yunwu.ai/v1"
    yunwu_vision_model: str | None = None
    yunwu_vision_api_mode: Literal["chat_completions"] = "chat_completions"
    yunwu_vision_api_path: str = "/chat/completions"
    yunwu_reviewer_model_a: str | None = None
    yunwu_reviewer_model_b: str | None = None
    yunwu_image_model: str = "gpt-image-1"
    yunwu_image_api_mode: Literal["json_generations", "multipart_edits"] = "multipart_edits"
    yunwu_image_api_path: str = "/images/edits"
    yunwu_image_size: str = "1024x1024"
    yunwu_image_response_format: Literal["url", "b64_json"] = "url"
    yunwu_image_timeout_seconds: float = Field(default=600.0, ge=10.0, le=1800.0)
    formal_image_model: str = "gpt-image-2"
    formal_image_api_mode: Literal["multipart_edits"] = "multipart_edits"
    formal_image_api_path: str = "/images/edits"
    formal_image_size: str = "1024x1024"
    formal_image_response_format: Literal["url", "b64_json"] | None = None
    formal_image_timeout_seconds: float = Field(default=600.0, ge=10.0, le=1800.0)
    multimodal_timeout_seconds: float = Field(default=600.0, ge=10.0, le=600.0)
    advisory_preview_max_edge: int = Field(default=1600, ge=512, le=2048)
    preview_strategy: str = "model_native"
    preview_max_iterations: int = Field(default=3, ge=1, le=10)

    inference_base_url: str | None = None
    inference_api_key: str | None = Field(default=None, repr=False)
    request_timeout_seconds: float = 600.0
    worker_concurrency: int = Field(default=2, ge=1, le=16)
    contract_compilation_concurrency: int = Field(default=4, ge=1, le=16)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)

    @field_validator("formal_image_response_format", mode="before")
    @classmethod
    def blank_formal_response_format_means_omitted(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def require_distinct_reviewer_models(self) -> Settings:
        reviewer_models = (
            self.yunwu_reviewer_model_a,
            self.yunwu_reviewer_model_b,
        )
        if any(reviewer_models) and not all(reviewer_models):
            raise ValueError("Yunwu reviewer models A and B must both be configured")
        if reviewer_models[0] and reviewer_models[0] == reviewer_models[1]:
            raise ValueError("Yunwu reviewer models A and B must be distinct")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
