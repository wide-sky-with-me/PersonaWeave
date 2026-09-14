"""Provider-neutral AI results and safe request failures; independent of wire protocols."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ImageEditResult:
    """Normalized image-edit result returned by every gateway provider."""

    image_bytes: bytes
    model: str
    provider: str
    usage: dict = field(default_factory=dict)
    request_id: str | None = None


class ModelConfigurationError(RuntimeError):
    """Safe local routing failure, before a model request can be dispatched."""

    def __init__(self):
        super().__init__("Local AI routing configuration invalid; check model provider prefixes and deployments")


class ModelRequestError(RuntimeError):
    """Safe provider failure classification; never retain an upstream body or connection."""

    def __init__(self, kind: str, status_code=None):
        self.kind = kind if kind in {"Vision", "Image"} else "AI"
        self.status_code = status_code if type(status_code) is int and 100 <= status_code <= 599 else None
        suffix = f" (HTTP {self.status_code})" if self.status_code is not None else ""
        super().__init__(
            f"{self.kind} model request failed{suffix}; check service configuration and endpoint availability"
        )
