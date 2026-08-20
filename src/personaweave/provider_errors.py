import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PROVIDER_ERROR_MARKER = "PERSONAWEAVE_PROVIDER_ERROR:"
ProviderErrorCode = Literal[
    "provider_policy_rejected",
    "provider_timeout",
    "provider_rate_limited",
    "provider_request_rejected",
    "provider_response_invalid",
    "provider_unavailable",
    "workflow_execution_failed",
]


_ERROR_POLICY: dict[ProviderErrorCode, tuple[str, bool, int]] = {
    "provider_policy_rejected": (
        "图片内容触发服务商安全策略，无法完成生成。",
        False,
        422,
    ),
    "provider_timeout": ("图片生成服务响应超时，请稍后重试。", True, 504),
    "provider_rate_limited": ("图片生成服务请求过于频繁，请稍后重试。", True, 429),
    "provider_request_rejected": (
        "图片生成服务拒绝了请求，请检查输入后重试。",
        False,
        502,
    ),
    "provider_response_invalid": (
        "云端模型返回的数据结构不符合要求，请重试。",
        True,
        502,
    ),
    "provider_unavailable": ("图片生成服务暂时不可用，请稍后重试。", True, 502),
    "workflow_execution_failed": ("工作流执行失败，请检查工作流配置后重试。", False, 502),
}

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_POLICY_TOKENS = {
    "contentpolicyviolation",
    "content_policy_violation",
    "outputimagesensitivecontentdetected.policyviolation",
    "policyviolation",
    "safetyviolation",
    "sensitivecontent",
    "sensitivecontentdetected",
}


class ProviderErrorDTO(BaseModel):
    """Safe, serializable provider failure exposed across process boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ProviderErrorCode
    message: str = Field(min_length=1, max_length=160)
    retryable: bool
    upstream_status: int | None = Field(default=None, ge=100, le=599)
    provider_code: str | None = Field(default=None, min_length=1, max_length=128)
    provider_type: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86400)

    @model_validator(mode="after")
    def require_canonical_policy(self) -> "ProviderErrorDTO":
        message, retryable, _ = _ERROR_POLICY[self.code]
        if self.message != message or self.retryable is not retryable:
            raise ValueError("provider error message and retryability must match canonical policy")
        for value in (self.provider_code, self.provider_type, self.request_id):
            if value is not None and _TOKEN_PATTERN.fullmatch(value) is None:
                raise ValueError("provider diagnostic facts contain unsupported characters")
        return self


class ProviderExecutionError(RuntimeError):
    def __init__(self, error: ProviderErrorDTO):
        self.error = error
        super().__init__(error.message)


def provider_error(
    code: ProviderErrorCode,
    *,
    upstream_status: int | None = None,
    provider_code: str | None = None,
    provider_type: str | None = None,
    request_id: str | None = None,
    retry_after_seconds: int | None = None,
) -> ProviderErrorDTO:
    message, retryable, _ = _ERROR_POLICY[code]
    return ProviderErrorDTO(
        code=code,
        message=message,
        retryable=retryable,
        upstream_status=upstream_status,
        provider_code=provider_code,
        provider_type=provider_type,
        request_id=request_id,
        retry_after_seconds=retry_after_seconds,
    )


def provider_error_http_status(error: ProviderErrorDTO | ProviderErrorCode) -> int:
    code = error.code if isinstance(error, ProviderErrorDTO) else error
    return _ERROR_POLICY[code][2]


def provider_error_retryable(code: str | None) -> bool:
    policy = _ERROR_POLICY.get(code)  # type: ignore[arg-type]
    return policy[1] if policy is not None else False


def provider_error_from_detail(detail: object) -> ProviderErrorDTO:
    """Validate a gateway object detail or return a non-leaking safe fallback."""

    if isinstance(detail, dict):
        try:
            return ProviderErrorDTO.model_validate(detail)
        except (TypeError, ValueError):
            pass
    return provider_error("provider_unavailable")


def provider_error_from_history(status: object) -> ProviderErrorDTO:
    """Classify the most recent Comfy execution error without exposing node prose."""

    if not isinstance(status, dict):
        return provider_error("workflow_execution_failed")
    messages = status.get("messages")
    if not isinstance(messages, list):
        return provider_error("workflow_execution_failed")
    for message in reversed(messages):
        if not (
            isinstance(message, list)
            and len(message) == 2
            and message[0] == "execution_error"
            and isinstance(message[1], dict)
        ):
            continue
        exception_message = message[1].get("exception_message")
        if not isinstance(exception_message, str):
            return provider_error("workflow_execution_failed")
        facts = _marked_facts(exception_message)
        if facts is None:
            facts = _legacy_facts(exception_message)
        return _classify_facts(facts) if facts is not None else provider_error(
            "workflow_execution_failed"
        )
    return provider_error("workflow_execution_failed")


def _marked_facts(message: str) -> dict[str, Any] | None:
    marker_index = message.find(PROVIDER_ERROR_MARKER)
    if marker_index < 0:
        return None
    encoded = message[marker_index + len(PROVIDER_ERROR_MARKER) : marker_index + 2048]
    try:
        value, _ = json.JSONDecoder().raw_decode(encoded.lstrip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return _validated_facts(value)


def _legacy_facts(message: str) -> dict[str, Any] | None:
    """Parse only the old Yunwu wrapper's structured tokens, never its message prose."""

    if message.startswith("Yunwu image API timed out after "):
        return {"failure_kind": "timeout"}
    status_match = re.search(r"Yunwu image API returned HTTP (\d{3})(?:\D|$)", message)
    if status_match is None:
        return None
    facts: dict[str, Any] = {
        "failure_kind": "http",
        "upstream_status": int(status_match.group(1)),
    }
    for fact, label in (("provider_code", "code"), ("provider_type", "type")):
        match = re.search(rf"(?:^|\s){label}=([A-Za-z0-9][A-Za-z0-9._:/-]{{0,127}})", message)
        if match is not None:
            facts[fact] = match.group(1)
    return _validated_facts(facts)


def _validated_facts(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or len(value) > 8:
        return None
    kind = value.get("failure_kind")
    if kind not in {"http", "timeout"}:
        return None
    facts: dict[str, Any] = {"failure_kind": kind}
    status = value.get("upstream_status")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        facts["upstream_status"] = status
    retry_after = value.get("retry_after_seconds")
    if (
        isinstance(retry_after, int)
        and not isinstance(retry_after, bool)
        and 0 <= retry_after <= 86400
    ):
        facts["retry_after_seconds"] = retry_after
    for key in ("provider_code", "provider_type", "request_id"):
        item = value.get(key)
        if isinstance(item, str) and _TOKEN_PATTERN.fullmatch(item):
            facts[key] = item
    return facts


def _classify_facts(facts: dict[str, Any]) -> ProviderErrorDTO:
    if facts["failure_kind"] == "timeout":
        code: ProviderErrorCode = "provider_timeout"
    elif any(
        _is_policy_token(facts.get(key))
        for key in ("provider_code", "provider_type")
    ):
        code = "provider_policy_rejected"
    elif facts.get("upstream_status") == 429:
        code = "provider_rate_limited"
    elif isinstance(facts.get("upstream_status"), int) and 400 <= facts["upstream_status"] < 500:
        code = "provider_request_rejected"
    else:
        code = "provider_unavailable"
    return provider_error(
        code,
        upstream_status=facts.get("upstream_status"),
        provider_code=facts.get("provider_code"),
        provider_type=facts.get("provider_type"),
        request_id=facts.get("request_id"),
        retry_after_seconds=facts.get("retry_after_seconds"),
    )


def _is_policy_token(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.casefold()
    return normalized in _POLICY_TOKENS or any(
        part in _POLICY_TOKENS for part in re.split(r"[.:/]", normalized)
    )
