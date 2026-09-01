"""Deterministic face-swap model routing and fallback policy."""

from dataclasses import dataclass
from typing import Literal

SwapperModel = Literal["auto", "inswapper_128", "hyperswap_1c_256", "ghost_1_256"]


@dataclass(frozen=True)
class RoutingDecision:
    """Auditable model choice for one target face."""

    requested: str
    selected: str
    reason: str
    fallback: bool = False


class SwapperModelRouter:
    """Choose a backend from face geometry, match confidence, and license policy."""

    def __init__(self, available_models: set[str]):
        self.available_models = set(available_models)

    def _first_available(self, candidates: list[str], requested: str, reason: str) -> RoutingDecision:
        for candidate in candidates:
            if candidate in self.available_models:
                return RoutingDecision(
                    requested=requested,
                    selected=candidate,
                    reason=reason if candidate == candidates[0] else f"{reason}; fallback: {candidate}",
                    fallback=candidate != candidates[0],
                )
        raise FileNotFoundError("No supported face-swap model assets are available")

    def select(
        self,
        requested: SwapperModel,
        *,
        face_size: float,
        yaw_ratio: float,
        identity_confidence: float,
        allow_research_models: bool = False,
    ) -> RoutingDecision:
        """Return a model decision without loading model weights."""
        if requested != "auto":
            if requested == "hyperswap_1c_256" and not allow_research_models:
                return self._first_available(
                    ["inswapper_128", "ghost_1_256"],
                    requested,
                    "HyperSwap requires explicit ResearchRAIL opt-in",
                )
            fallback_order = [requested, "inswapper_128", "ghost_1_256"]
            if allow_research_models:
                fallback_order.append("hyperswap_1c_256")
            return self._first_available(fallback_order, requested, "explicit model request")

        absolute_yaw = abs(yaw_ratio)
        if allow_research_models and face_size >= 160 and absolute_yaw <= 0.45 and identity_confidence >= 0.55:
            return self._first_available(
                ["hyperswap_1c_256", "inswapper_128"],
                requested,
                "large high-confidence face uses opted-in HyperSwap quality routing",
            )

        # Ghost remains available for explicit experiments, but real-image regression
        # tests show unstable facial structure on some cross-identity transfers.
        # Automatic production routing therefore uses the robust InSwapper baseline.
        return self._first_available(
            ["inswapper_128"],
            requested,
            "stable automatic routing uses the InSwapper production baseline",
        )
