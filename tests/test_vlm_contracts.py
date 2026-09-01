"""Contract tests for structured VLM diagnosis and whole-image review."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.ai_gateway import AIGateway
from core.vlm_contracts import BatchFaceReview, IdentityArbitration, ReviewIssue, WholeImageQualityReview


def response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


def test_batch_review_normalizes_provider_shapes_and_forces_conservative_decision():
    parsed = AIGateway.parse_structured_response(
        AIGateway.__new__(AIGateway),
        response(
            [
                {
                    "person_id": 1,
                    "overall_score": 93,
                    "anatomy_score": 93,
                    "coherence_score": 93,
                    "naturalness_score": 91,
                    "lighting_score": 94,
                    "person_locator": "画面左侧人物",
                    "issues": [],
                    "strengths": "光照方向一致",
                    "recommended_prompt": "修复局部纹理连续性",
                    "should_refine": False,
                }
            ]
        ),
        BatchFaceReview,
    )
    assert parsed.reports[0].strengths == ["光照方向一致"]
    assert parsed.reports[0].should_refine is True


def test_batch_review_accepts_explicit_person_label_and_rejects_ambiguous_one():
    payload = {
        "reports": [
            {
                "person_id": "PERSON #2",
                "overall_score": 90,
                "anatomy_score": 90,
                "coherence_score": 90,
                "naturalness_score": 90,
                "lighting_score": 90,
                "person_locator": "画面右侧人物",
                "issues": [],
                "strengths": [],
                "recommended_prompt": "检查边缘连续性",
                "should_refine": True,
            }
        ]
    }
    assert BatchFaceReview.model_validate(payload).reports[0].person_id == 2
    payload["reports"][0]["person_id"] = "第二个人"
    with pytest.raises(ValidationError):
        BatchFaceReview.model_validate(payload)


def test_review_issue_specificity_and_category_are_strict():
    with pytest.raises(ValidationError):
        ReviewIssue.model_validate(
            {
                "category": "面部问题",
                "severity": "medium",
                "region": "右眼角",
                "description": "该处有约2毫米的模糊色斑，与周围皮肤纹理不连续",
                "suggestion": "重新羽化该区域边缘",
            }
        )
    with pytest.raises(ValidationError):
        ReviewIssue.model_validate(
            {
                "category": "bilateral_symmetry_gaze",
                "severity": "medium",
                "region": "双眼",
                "description": "双眼瞳孔的视线方向存在可见偏差，与头部朝向不一致",
                "suggestion": "协调双眼瞳孔位置和视线方向",
            }
        )
    with pytest.raises(ValidationError):
        ReviewIssue.model_validate(
            {
                "category": "boundary_blending",
                "severity": "low",
                "region": "脸",
                "description": "有点怪",
                "suggestion": "",
            }
        )
    issue = ReviewIssue.model_validate(
        {
            "category": "boundary_blending",
            "severity": "medium",
            "region": "右耳前发际线与太阳穴交界处",
            "description": "该处有一条约1厘米长的浅灰直线色带，与周围发丝自然边缘不连续",
            "suggestion": "沿右耳前发际线重新恢复自然发丝边缘",
        }
    )
    assert issue.category == "boundary_blending"


def whole_review(**overrides):
    payload = {
        "overall_score": 85,
        "coherence_score": 84,
        "naturalness_score": 86,
        "people": [{"person_id": 1, "status": "improved", "evidence": "面部与场景协调性得到可见改善"}],
        "unrequested_changes_detected": False,
        "new_high_severity_issue": False,
        "recommendation": "accept",
        "summary": "整图质量自然，建议人工对比后接受。",
        "issues": [],
        "strengths": ["整体光影协调"],
    }
    payload.update(overrides)
    return payload


def test_whole_image_review_is_typed_advisory_without_identity_gate():
    review = WholeImageQualityReview.model_validate(whole_review(recommendation="regenerate"))
    assert review.recommendation == "regenerate"
    assert "identity_score" not in review.model_dump()
    assert not hasattr(review, "passed")


def test_whole_image_review_cannot_accept_low_scores_or_unresolved_people():
    low_score = WholeImageQualityReview.model_validate(whole_review())
    assert low_score.recommendation == "review"

    unresolved = WholeImageQualityReview.model_validate(
        whole_review(
            overall_score=95,
            coherence_score=95,
            naturalness_score=95,
            people=[{"person_id": 1, "status": "unchanged", "evidence": "原有侧脸结构问题仍清晰可见"}],
        )
    )
    assert unresolved.recommendation == "review"


def test_whole_image_review_rejects_duplicate_people_and_extra_fields():
    duplicate = whole_review(
        people=[
            {"person_id": 1, "status": "improved", "evidence": "第一个重复人物评估"},
            {"person_id": 1, "status": "resolved", "evidence": "第二个重复人物评估"},
        ]
    )
    with pytest.raises(ValidationError, match="duplicate person_id"):
        WholeImageQualityReview.model_validate(duplicate)
    with pytest.raises(ValidationError):
        WholeImageQualityReview.model_validate(whole_review(unexpected=True))


def test_identity_arbitration_contract_remains_bounded_for_swap_workflow():
    with pytest.raises(ValidationError):
        IdentityArbitration.model_validate(
            {"matched_person_index": 1, "confidence": 1.2, "reasoning": "match", "attributes_matched": []}
        )


class RepairingGateway(AIGateway):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def vision_completion(self, messages, alias="personaweave-vision-review", **kwargs):
        self.calls.append({"messages": messages, "alias": alias, **kwargs})
        return next(self.responses)


def test_structured_completion_repairs_invalid_whole_image_review_once():
    invalid = response(whole_review(new_high_severity_issue="uncertain"))
    repaired = response(whole_review(new_high_severity_issue=False, recommendation="review"))
    gateway = RepairingGateway([invalid, repaired])
    final_response, report = asyncio.run(
        gateway.structured_vision_completion(
            [{"role": "user", "content": "review"}],
            WholeImageQualityReview,
            alias="personaweave-vision-precision",
            response_format={"type": "json_object"},
        )
    )
    assert final_response is repaired
    assert report.recommendation == "review"
    assert len(gateway.calls) == 2


def test_structured_completion_retries_remaining_validation_path():
    base_report = {
        "person_id": 3,
        "overall_score": 80,
        "anatomy_score": 78,
        "coherence_score": 76,
        "naturalness_score": 79,
        "lighting_score": 81,
        "person_locator": "下排从左到右第三位人物",
        "strengths": [],
        "recommended_prompt": "协调双眼瞳孔位置和视线方向",
        "should_refine": True,
    }
    issue = {
        "category": "bilateral_symmetry_gaze",
        "region": "双眼",
        "description": "双眼瞳孔的视线方向存在可见偏差，与头部朝向不一致",
        "suggestion": "协调双眼瞳孔位置和视线方向",
    }
    invalid = response({"reports": [{**base_report, "issues": [issue]}]})
    partially_repaired = response({"reports": [{**base_report, "issues": [{**issue, "severity": "medium"}]}]})
    repaired = response(
        {
            "reports": [
                {
                    **base_report,
                    "issues": [
                        {
                            **issue,
                            "severity": "medium",
                            "region": "双眼瞳孔与视线方向",
                        }
                    ],
                }
            ]
        }
    )
    gateway = RepairingGateway([invalid, partially_repaired, repaired])

    _, report = asyncio.run(gateway.structured_vision_completion([], BatchFaceReview))

    assert report.reports[0].issues[0].region == "双眼瞳孔与视线方向"
    assert len(gateway.calls) == 3
    second_repair_prompt = gateway.calls[2]["messages"][0]["content"]
    assert "MUST change every field path" in second_repair_prompt
    assert "双眼瞳孔与视线方向" in second_repair_prompt


def test_structured_completion_fails_after_two_unsuccessful_repairs():
    invalid = response({"recommendation": "unknown"})
    gateway = RepairingGateway([invalid, invalid, invalid])
    with pytest.raises(ValueError, match="structured repair failed after 2 attempts"):
        asyncio.run(
            gateway.structured_vision_completion(
                [{"role": "user", "content": "review"}],
                WholeImageQualityReview,
            )
        )
    assert len(gateway.calls) == 3
