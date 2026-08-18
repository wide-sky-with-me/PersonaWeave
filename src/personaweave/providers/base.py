from typing import Protocol

from personaweave.domain import (
    AnalysisRequest,
    AnalysisResult,
    SwapRequest,
    SwapResult,
    ValidationRequest,
    ValidationResult,
)


class FaceAnalysisProvider(Protocol):
    async def analyze(self, request: AnalysisRequest) -> AnalysisResult: ...


class FaceSwapProvider(Protocol):
    async def swap(self, request: SwapRequest) -> SwapResult: ...


class ValidationProvider(Protocol):
    async def validate(self, request: ValidationRequest) -> ValidationResult: ...

