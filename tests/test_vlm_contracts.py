"""Shared structured-output repair and identity arbitration survive the refinement redesign."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from core.ai.contracts import IdentityArbitration
from core.ai.gateway import AIGateway
from core.refinement.contracts import CandidateReview, Diagnosis, Issue


def response(payload):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])


def diagnosis(**changes):
    payload = {
        "assistant_message": "未发现明确缺陷，不需要强制修图。",
        "needs_edit": False,
        "mapping_uncertain": False,
        "people": [
            {
                "person_id": 3,
                "person_locator": "下排左侧人物",
                "mapping_confident": True,
                "identity_status": "unavailable",
                "evidence": "当前没有绑定身份参考素材",
                "uncertainty": "无法验证目标身份一致性",
            }
        ],
        "issues": [],
        "edit_plan": {"changes": [], "preserve": [], "instructions": ""},
    }
    payload.update(changes)
    return payload


def issue(**changes):
    payload = {
        "category": "anatomy",
        "person_id": 3,
        "region": "双眼瞳孔与视线方向",
        "description": "双眼瞳孔的视线方向存在可见偏差，与头部朝向不一致",
        "evidence": "当前图片中两侧瞳孔相对眼睑的位置不一致",
        "suggestion": "协调双眼瞳孔位置和视线方向",
        "severity": "medium",
        "confidence": "high",
    }
    payload.update(changes)
    return payload


def test_score_free_diagnosis_can_genuinely_recommend_no_edit():
    parsed = AIGateway.parse_structured_response(AIGateway.__new__(AIGateway), response(diagnosis()), Diagnosis)
    assert parsed.needs_edit is False and parsed.issues == []
    assert "overall_score" not in parsed.model_dump()
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(diagnosis(should_refine=True))


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "面部问题"},
        {"region": "脸"},
        {"description": "有点怪"},
        {"evidence": "不自然"},
        {"suggestion": ""},
        {"confidence": "low"},
    ],
)
def test_review_issue_specificity_category_and_uncertainty_are_strict(changes):
    with pytest.raises(ValidationError):
        Issue.model_validate(issue(**changes))


def test_identity_arbitration_contract_remains_bounded_for_swap_workflow():
    with pytest.raises(ValidationError):
        IdentityArbitration.model_validate(
            {"matched_person_index": 1, "confidence": 1.2, "reasoning": "match", "attributes_matched": []}
        )
    result = IdentityArbitration.model_validate(
        {"matched_person_index": 1, "confidence": 0.8, "reasoning": "visible match", "attributes_matched": []}
    )
    assert result.matched_person_index == 1


def test_candidate_review_is_advisory_without_acceptance_or_score_fields():
    review = CandidateReview.model_validate({"summary": "复评证据仅供人工裁决，不自动接受"})
    assert "accepted" not in review.model_dump()
    for changes in ({"accepted": True}, {"recommendation": "accept"}, {"overall_score": 99}):
        with pytest.raises(ValidationError):
            CandidateReview.model_validate({"summary": review.summary, **changes})


class RepairingGateway(AIGateway):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def vision_completion(self, messages, alias="personaweave-vision-default", **kwargs):
        self.calls.append({"messages": messages, "alias": alias, **kwargs})
        return next(self.responses)


def test_shared_structured_completion_repairs_invalid_candidate_review_once():
    invalid = response({"summary": "短"})
    repaired = response({"summary": "候选边缘已改善，最终结果仍需人工确认"})
    gateway = RepairingGateway([invalid, repaired])
    final_response, report = asyncio.run(
        gateway.structured_vision_completion(
            [{"role": "user", "content": "review"}],
            CandidateReview,
            alias="personaweave-vision-default",
            response_format={"type": "json_object"},
        )
    )
    assert final_response is repaired and report.summary
    assert len(gateway.calls) == 2


def test_shared_structured_completion_retries_remaining_validation_path():
    invalid_issue = issue(region="双眼")
    del invalid_issue["severity"]
    invalid = response(diagnosis(issues=[invalid_issue]))
    partial = response(diagnosis(issues=[{**invalid_issue, "severity": "medium"}]))
    repaired = response(diagnosis(issues=[issue()]))
    gateway = RepairingGateway([invalid, partial, repaired])
    _, report = asyncio.run(gateway.structured_vision_completion([], Diagnosis))
    assert report.issues[0].region == "双眼瞳孔与视线方向"
    assert len(gateway.calls) == 3
    prompt = gateway.calls[2]["messages"][0]["content"]
    assert "MUST change every field path" in prompt
    assert "region" in prompt


def test_shared_structured_completion_fails_after_two_unsuccessful_repairs():
    invalid = response({"summary": ""})
    gateway = RepairingGateway([invalid, invalid, invalid])
    with pytest.raises(ValueError, match="structured repair failed after 2 attempts"):
        asyncio.run(gateway.structured_vision_completion([{"role": "user", "content": "review"}], CandidateReview))
    assert len(gateway.calls) == 3


def test_explicit_zero_text_repairs_never_reissues_model_request():
    invalid = response({"summary": ""})
    gateway = RepairingGateway([invalid])
    with pytest.raises(ValueError):
        asyncio.run(gateway.structured_vision_completion([], CandidateReview, max_text_repairs=0))
    assert len(gateway.calls) == 1
