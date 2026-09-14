"""Both diagnostic CLIs exercise the real new service with an entirely offline gateway."""

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.ai.configuration import ImageEditConfig
from core.ai.types import ImageEditResult
from core.refinement.contracts import Diagnosis
from core.refinement.conversation import ConversationRefinementService
from core.refinement.image_ops import decode_image
from scripts import evaluate_refinement_models, refinement_e2e


@pytest.fixture
def offline(tmp_path, monkeypatch):
    image = np.full((64, 96, 3), 127, dtype=np.uint8)
    for name, value in [("input", 127), ("original", 70), ("reference", 40)]:
        assert cv2.imwrite(str(tmp_path / f"{name}.png"), np.full_like(image, value))
    manifest = tmp_path / "people.json"
    manifest.write_text(
        json.dumps(
            [{"person_id": 7, "person_locator": "画面左侧穿蓝色上衣的人物", "reference_paths": ["reference.png"]}]
        ),
        encoding="utf-8",
    )

    class Gateway:
        default_image_alias = "test-image"
        image_edits = {"test-image": ImageEditConfig(model_name="test-image", generation={"size": "auto"})}
        needs_edit = True
        mapping_uncertain = False

        def __init__(self):
            self.edits, self.visions = [], []

        def vision_alias(self, role):
            return "test-vision"

        async def structured_vision_completion(self, messages, contract, **kwargs):
            self.visions.append((messages, contract, kwargs))
            if contract is not Diagnosis:
                raise ValueError("Simulated advisory outage with PRIVATE upstream body")
            available = "REFERENCE_BOARD_PERSON_7" in json.dumps(messages)
            report = {
                "assistant_message": "检查完成，修复可见边缘问题。",
                "needs_edit": self.needs_edit,
                "mapping_uncertain": self.mapping_uncertain,
                "people": [
                    {
                        "person_id": 7,
                        "person_locator": "画面左侧穿蓝色上衣的人物",
                        "mapping_confident": not self.mapping_uncertain,
                        "identity_status": "uncertain"
                        if self.mapping_uncertain
                        else ("match" if available else "unavailable"),
                        "evidence": "当前参考板与人物特征可见对应",
                        "uncertainty": "参考证据或人物对应尚不充分" if self.mapping_uncertain or not available else "",
                    }
                ],
                "issues": [],
                "edit_plan": {
                    "changes": ["修复人物右侧面部边缘"] if self.needs_edit else [],
                    "preserve": ["保留人物与场景关系"],
                    "instructions": "修复边缘并协调自然光影" if self.needs_edit else "",
                },
            }
            return None, contract.model_validate(report)

        async def edit_image(self, images, prompt, **kwargs):
            self.edits.append((images, prompt, kwargs))
            _, encoded = cv2.imencode(".png", np.full_like(image, 91))
            return ImageEditResult(image_bytes=encoded.tobytes(), model="test-model", provider="test")

    gateway = Gateway()
    monkeypatch.setattr(evaluate_refinement_models, "AIGateway", lambda: gateway)
    monkeypatch.setattr(refinement_e2e, "ConversationRefinementService", lambda: ConversationRefinementService(gateway))
    args = SimpleNamespace(
        image=tmp_path / "input.png",
        original=tmp_path / "original.png",
        people=manifest,
        output_dir=tmp_path / "output",
        instructions="修复右侧边缘，协调遮挡和光影",
        model_alias=None,
        models=["test-image"],
        allow_paid=True,
        mode="auto",
    )
    return gateway, args, image


@pytest.mark.parametrize("entry", ["evaluation", "e2e"])
def test_diagnostics_generate_single_image_with_new_evidence_contract(offline, entry):
    gateway, args, image = offline
    result = asyncio.run(evaluate_refinement_models.run(args) if entry == "evaluation" else refinement_e2e.run(args))
    generated = result["results"][0] if entry == "evaluation" else result
    assert generated["status"] == ("ok" if entry == "evaluation" else "review_required")
    assert generated["review_error"] == "Advisory review unavailable (ValueError)"
    assert "PRIVATE" not in json.dumps(generated)
    assert Path(generated["candidate_path"]).exists()
    assert len(gateway.edits) == 1 and len(gateway.edits[0][0]) == 1
    actual_input = decode_image(base64.b64decode(gateway.edits[0][0][0].split(",", 1)[1]))
    np.testing.assert_array_equal(actual_input, image)
    first_messages = json.dumps(gateway.visions[0][0])
    assert "ORIGINAL" in first_messages and "CURRENT" in first_messages and "REFERENCE_BOARD_PERSON_7" in first_messages
    assert len(gateway.visions) == 2
    assert all(call[2]["max_text_repairs"] == 0 for call in gateway.visions)


@pytest.mark.parametrize("condition", ["discuss", "no_edit", "mapping_uncertain"])
def test_diagnostic_does_not_force_generation(offline, condition):
    gateway, args, _ = offline
    if condition == "discuss":
        args.mode = "discuss"
    elif condition == "no_edit":
        gateway.needs_edit = False
    else:
        gateway.mapping_uncertain = True
    result = asyncio.run(refinement_e2e.run(args))
    assert (
        result["status"]
        == {"discuss": "discussed", "no_edit": "no_edit", "mapping_uncertain": "clarification_required"}[condition]
    )
    assert gateway.edits == [] and len(gateway.visions) == 1
    assert not (args.output_dir / "candidate.png").exists()


def test_model_comparison_uses_same_baseline_and_only_explicit_aliases(offline):
    gateway, args, image = offline
    gateway.image_edits = {
        **gateway.image_edits,
        "provider/model": ImageEditConfig(model_name="provider/model", generation={"size": "auto"}),
        "unused": ImageEditConfig(model_name="unused", generation={"size": "auto"}),
    }
    args.models = ["test-image", "provider/model"]
    result = asyncio.run(evaluate_refinement_models.run(args))
    assert [call[2]["alias"] for call in gateway.edits] == args.models
    assert len(result["results"]) == 2
    for call in gateway.edits:
        np.testing.assert_array_equal(decode_image(base64.b64decode(call[0][0].split(",", 1)[1])), image)
    assert (args.output_dir / "model-02.png").is_file()
    assert result["results"][0]["metrics"]["mean_absolute_channel_difference"] == 36


@pytest.mark.parametrize("entry", ["evaluation", "e2e"])
def test_diagnostics_require_authorization_before_any_model_call(offline, entry):
    gateway, args, _ = offline
    args.allow_paid = False
    with pytest.raises(ValueError, match="allow-paid"):
        asyncio.run(evaluate_refinement_models.run(args) if entry == "evaluation" else refinement_e2e.run(args))
    assert not gateway.edits and not gateway.visions
    assert not args.output_dir.exists()


def test_missing_references_are_not_invented(offline):
    gateway, args, _ = offline
    args.original = None
    args.people.write_text(
        json.dumps([{"person_id": 7, "person_locator": "画面左侧穿蓝色上衣的人物"}]), encoding="utf-8"
    )
    args.mode = "discuss"
    result = asyncio.run(refinement_e2e.run(args))
    assert result["diagnosis"]["people"][0]["identity_status"] == "unavailable"
    assert not gateway.edits


def test_comparison_unknown_alias_rejected_before_any_paid_request(offline):
    gateway, args, _ = offline
    args.models = ["unknown"]
    with pytest.raises(ValueError, match="Unknown image alias"):
        asyncio.run(evaluate_refinement_models.run(args))
    assert not gateway.visions and not gateway.edits


def test_diagnostic_output_cannot_publish_or_overwrite_existing_bundle(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="production"):
        refinement_e2e.create_bundle_dir(root / "outputs/api_results/diagnostic")
    with pytest.raises(FileExistsError):
        refinement_e2e.create_bundle_dir(tmp_path)
