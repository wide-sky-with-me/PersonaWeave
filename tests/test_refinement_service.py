"""Tests for whole-image AI refinement and advisory review."""

import asyncio
import base64
from types import SimpleNamespace

import numpy as np
import pytest

from core import ai_gateway
from core.ai_gateway import _resolve_env
from core.image_security import MAX_IMAGE_BYTES
from core.refinement_service import RefinementService
from core.vlm_contracts import WholeImageQualityReview


def image(height=32, width=32, value=127):
    return np.full((height, width, 3), value, dtype=np.uint8)


class DiagnosticGateway:
    def __init__(self):
        self.messages = None
        self.kwargs = None

    @staticmethod
    def vision_alias(role):
        return {"refinement": "personaweave-vision-refinement"}[role]

    async def structured_vision_completion(self, messages, contract, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        report = {
            "reports": [
                {
                    "person_id": 7,
                    "overall_score": 80,
                    "anatomy_score": 78,
                    "coherence_score": 79,
                    "naturalness_score": 75,
                    "lighting_score": 81,
                    "person_locator": "画面左侧、黑色上衣、坐在笔记本电脑后的人物",
                    "issues": [],
                    "strengths": [],
                    "recommended_prompt": "修复面部边缘和皮肤纹理",
                    "should_refine": True,
                },
                {
                    "person_id": 9,
                    "overall_score": 82,
                    "anatomy_score": 81,
                    "coherence_score": 80,
                    "naturalness_score": 79,
                    "lighting_score": 83,
                    "person_locator": "画面右侧、蓝色上衣、站立的人物",
                    "issues": [],
                    "strengths": [],
                    "recommended_prompt": "修复眼睛和下颌协调性",
                    "should_refine": True,
                },
            ]
        }
        return SimpleNamespace(model="fake-vlm"), contract.model_validate(report)


def people():
    return [
        SimpleNamespace(person_id=7, face_index=2, person_locator="下排·从左到右第 1 位", user_focus="检查边缘"),
        SimpleNamespace(person_id=9, face_index=1, person_locator="上排·从左到右第 1 位", user_focus=""),
    ]


def task_people():
    return [
        SimpleNamespace(
            person_id=7,
            face_index=2,
            person_locator="画面右侧后排、蓝色外套、站在窗边的人物",
            review_prompt="修复右眼睑重影与下颌接缝",
            user_instructions="保留雀斑，不要磨皮",
            issues=[
                SimpleNamespace(
                    category="boundary_blending",
                    region="右耳前发际线与太阳穴交界处",
                    description="约一厘米长的浅灰直线色带与周围发丝不连续",
                    suggestion="恢复自然发丝边缘",
                )
            ],
        )
    ]


def test_environment_reference_is_strict(monkeypatch):
    monkeypatch.setenv("TEST_AI_KEY", "secret")
    assert _resolve_env("os.environ/TEST_AI_KEY") == "secret"
    monkeypatch.delenv("TEST_AI_KEY")
    with pytest.raises(ValueError, match="TEST_AI_KEY"):
        _resolve_env("os.environ/TEST_AI_KEY")


def test_image_edit_rejects_invalid_outputs(monkeypatch):
    async def non_image(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"not-image").decode(), url=None)])

    gateway = ai_gateway.AIGateway.__new__(ai_gateway.AIGateway)
    gateway.image_edits = {
        "alias": ai_gateway.ImageEditConfig(model_name="alias", params={"model": "openai/edit-model"})
    }
    gateway.default_image_alias = "alias"
    monkeypatch.setattr(ai_gateway.litellm, "aimage_edit", non_image)
    with pytest.raises(ValueError, match="could not be decoded"):
        asyncio.run(gateway.edit_image([b"input"], "whole image correction"))

    async def oversized(**kwargs):
        payload = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()
        return SimpleNamespace(data=[SimpleNamespace(b64_json=payload, url=None)])

    monkeypatch.setattr(ai_gateway.litellm, "aimage_edit", oversized)
    with pytest.raises(ValueError, match="encoded byte limit"):
        asyncio.run(gateway.edit_image([b"input"], "whole image correction"))


def test_diagnosis_uses_one_complete_image_and_structured_people():
    gateway = DiagnosticGateway()
    result = asyncio.run(RefinementService(gateway).review(image(), people()))
    assert set(result) == {7, 9}
    assert gateway.kwargs["alias"] == "personaweave-vision-refinement"
    assert result[7]["person_locator"] == "下排·从左到右第 1 位"
    content = gateway.messages[1]["content"]
    assert len([part for part in content if part["type"] == "image_url"]) == 1
    assert "PERSON #7: 视觉位置线索：下排·从左到右第 1 位" in content[0]["text"]
    assert (
        "Do not reinterpret a within-row position as a global left-to-right face index"
        in gateway.messages[0]["content"]
    )
    assert "defect-first hypothesis testing" in content[0]["text"]


def test_diagnosis_compares_complete_original_and_swapped_images_when_available():
    gateway = DiagnosticGateway()
    asyncio.run(RefinementService(gateway).review(image(value=140), people(), original=image(value=90)))

    content = gateway.messages[1]["content"]
    assert len([part for part in content if part["type"] == "image_url"]) == 2
    assert "Image 1 is the complete target photo before face swapping" in content[0]["text"]


def test_generation_prompt_uses_text_locator_and_every_diagnosed_issue():
    prompt = RefinementService.build_whole_image_prompt("保持暖色光线", task_people())
    assert "人物文字定位：画面右侧后排、蓝色外套、站在窗边的人物" in prompt
    assert "检测几何定位" not in prompt
    assert "x=" not in prompt
    assert "身份参考" not in prompt
    assert "右耳前发际线与太阳穴交界处" in prompt
    assert "约一厘米长的浅灰直线色带" in prompt
    assert "人工修复要求：保留雀斑，不要磨皮" in prompt
    assert "保持暖色光线" in prompt
    assert "本任务不执行重新换脸" in prompt


def test_provider_canvas_is_normalized_to_source_shape():
    provider = np.zeros((1024, 1024, 3), dtype=np.uint8)
    provider[256:768] = 120
    normalized, audit = RefinementService.normalize_to_canvas(provider, (720, 1280, 3))
    assert normalized.shape == (720, 1280, 3)
    assert audit["provider_output_size"] == [1024, 1024]
    assert audit["target_canvas_size"] == [1280, 720]
    assert audit["canvas_normalization"] == "center-crop-height"


def test_generation_parameters_follow_provider_limits():
    gateway = SimpleNamespace(
        default_image_alias="personaweave-refine-gemini",
        image_edits={
            "personaweave-refine-gemini": SimpleNamespace(provider="dmx-gemini", generation={"image_size": "4K"}),
            "personaweave-refine-gemini-flash": SimpleNamespace(provider="dmx-gemini", generation={"image_size": "2K"}),
            "personaweave-refine-seedream": SimpleNamespace(
                provider="dmx-seedream",
                generation={
                    "max_side": 2848,
                    "max_pixels": 2048 * 2048,
                    "min_side": 512,
                    "grid": 8,
                    "delimiter": "x",
                },
            ),
            "personaweave-refine-plus": SimpleNamespace(
                provider="dmx-responses",
                generation={
                    "max_side": 2048,
                    "max_pixels": 2048 * 2048,
                    "min_side": 512,
                    "grid": 8,
                    "delimiter": "*",
                },
            ),
        },
    )
    service = RefinementService(gateway)
    assert service._generation_parameters(None, (3616, 5424, 3)) == (
        {"aspect_ratio": "3:2", "image_size": "4K"},
        None,
    )
    assert service._generation_parameters("personaweave-refine-gemini-flash", (3616, 5424, 3)) == (
        {"aspect_ratio": "3:2", "image_size": "2K"},
        None,
    )
    assert service._generation_parameters("personaweave-refine-plus", (3616, 5424, 3)) == (
        {"size": "2048*1360"},
        [2048, 1360],
    )
    assert service._generation_parameters("personaweave-refine-seedream", (3616, 5424, 3)) == (
        {"size": "2504x1672"},
        [2504, 1672],
    )


def test_whole_image_generation_sends_exactly_one_image_and_selected_alias():
    class Gateway:
        default_image_alias = "personaweave-refine-gemini"
        image_edits = {
            "personaweave-refine-gemini": SimpleNamespace(provider="dmx-gemini", generation={"image_size": "4K"})
        }

        def __init__(self):
            self.call = None

        async def edit_image(self, images, prompt, alias=None, parameters=None):
            self.call = {"images": images, "prompt": prompt, "alias": alias, "parameters": parameters}
            encoded = RefinementService.encode_image(image(), lossless=True).split(",", 1)[1]
            return SimpleNamespace(
                image_bytes=base64.b64decode(encoded),
                model="gemini-3-pro-image",
                provider="dmx-gemini",
                usage={},
                request_id="request-1",
            )

    gateway = Gateway()
    result = asyncio.run(
        RefinementService(gateway).refine_whole_image(
            image(64, 96), task_people(), "保持整体自然", "personaweave-refine-gemini"
        )
    )
    assert len(gateway.call["images"]) == 1
    assert gateway.call["alias"] == "personaweave-refine-gemini"
    assert "身份参考图映射" not in gateway.call["prompt"]
    assert result["candidate"].shape == (64, 96, 3)
    assert result["audit"]["input_image_count"] == 1
    assert result["audit"]["request_id"] == "request-1"


def test_final_review_uses_two_complete_images_and_is_advisory():
    class Gateway:
        def __init__(self):
            self.messages = None
            self.kwargs = None

        async def structured_vision_completion(self, messages, contract, **kwargs):
            self.messages = messages
            self.kwargs = kwargs
            return SimpleNamespace(model="review-vlm"), contract.model_validate(
                {
                    "overall_score": 72,
                    "coherence_score": 74,
                    "naturalness_score": 70,
                    "people": [{"person_id": 7, "status": "worsened", "evidence": "眼睛结构比生成前更不自然"}],
                    "unrequested_changes_detected": True,
                    "new_high_severity_issue": True,
                    "recommendation": "regenerate",
                    "summary": "候选存在明显问题，建议重新生成。",
                    "issues": ["背景文字发生变化"],
                    "strengths": [],
                }
            )

    gateway = Gateway()
    review = asyncio.run(
        RefinementService(gateway).review_whole_candidate(
            image(), image(value=140), task_people(), "保持文字", alias="personaweave-vision-eval"
        )
    )
    assert review["recommendation"] == "regenerate"
    assert review["model_used"] == "review-vlm"
    assert gateway.kwargs["alias"] == "personaweave-vision-eval"
    content = gateway.messages[0]["content"]
    assert len([part for part in content if part["type"] == "image_url"]) == 2
    assert "advisory evidence for a human" in content[0]["text"]
    assert "PERSON #7" in content[0]["text"]


def test_final_review_uses_original_baseline_and_candidate_when_available():
    class Gateway:
        def __init__(self):
            self.messages = None

        @staticmethod
        def vision_alias(role):
            return {"refinement": "personaweave-vision-refinement"}[role]

        async def structured_vision_completion(self, messages, contract, **kwargs):
            self.messages = messages
            return SimpleNamespace(model="review-vlm"), contract.model_validate(
                {
                    "overall_score": 93,
                    "coherence_score": 94,
                    "naturalness_score": 93,
                    "people": [{"person_id": 7, "status": "improved", "evidence": "目标瑕疵已有可见改善"}],
                    "unrequested_changes_detected": False,
                    "new_high_severity_issue": False,
                    "recommendation": "accept",
                    "summary": "三张完整图片对照后未见额外明显变化。",
                    "issues": [],
                    "strengths": ["目标瑕疵改善"],
                }
            )

    gateway = Gateway()
    asyncio.run(
        RefinementService(gateway).review_whole_candidate(
            image(value=110),
            image(value=120),
            task_people(),
            original_target=image(value=100),
        )
    )
    content = gateway.messages[0]["content"]
    assert len([part for part in content if part["type"] == "image_url"]) == 3
    assert "Image 1 is the complete pre-swap target" in content[0]["text"]


def test_final_review_requires_exact_person_coverage():
    class Gateway:
        @staticmethod
        def vision_alias(role):
            return {"refinement": "personaweave-vision-refinement"}[role]

        async def structured_vision_completion(self, messages, contract, **kwargs):
            review = WholeImageQualityReview.model_validate(
                {
                    "overall_score": 90,
                    "coherence_score": 90,
                    "naturalness_score": 90,
                    "people": [{"person_id": 999, "status": "improved", "evidence": "错误的人物编号"}],
                    "unrequested_changes_detected": False,
                    "new_high_severity_issue": False,
                    "recommendation": "review",
                    "summary": "人物覆盖不匹配，应由调用端拒绝。",
                }
            )
            return SimpleNamespace(model="review-vlm"), review

    with pytest.raises(ValueError, match="coverage mismatch"):
        asyncio.run(RefinementService(Gateway()).review_whole_candidate(image(), image(), task_people()))
