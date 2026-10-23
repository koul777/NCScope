from __future__ import annotations

import json
from typing import Any

import pytest

from app import main
from app.services import ai_question_quality_review as review_service


def _field_ready_question() -> dict[str, Any]:
    points = [
        "업무 상황과 본인 역할",
        "판단 기준과 선택 근거",
        "직접 수행한 조치와 실행 과정",
        "결과 확인과 개선 학습",
    ]
    follow_ups = [
        "당시 어떤 상황이었고 본인이 맡은 역할은 무엇이었습니까?",
        "어떤 판단 기준과 선택 근거로 직접 조치했습니까?",
        "그 조치의 결과를 무엇으로 확인했고 이후 무엇을 개선했습니까?",
    ]
    rubrics = []
    for point in points:
        observable = (
            f"'{point}' 과제에서 확인한 문서와 본인의 판단·행동·결과를 "
            "면접 기록에 구분해 남기는 관찰행동"
        )
        rubrics.append(
            {
                "dimension": point,
                "observable_evidence": observable,
                "weight": 25,
                "critical_floor": 1,
                "no_answer_rule": (
                    "유사경험과 표준화 대체상황 뒤에도 관찰행동이 없을 때만 1점으로 기록한다."
                ),
                "anchors": {
                    "5": f"{point} 5점: {observable}을 모두 충족하고 두 개 이상의 검증 근거를 연결한다.",
                    "3": f"{point} 3점: 관련 판단과 행동 및 검증 근거는 있으나 순서나 기여가 일부 불명확하다.",
                    "1": f"{point} 1점: 유사경험과 대체상황 뒤에도 관찰행동과 검증 근거를 제시하지 못한다.",
                },
            }
        )
    return {
        "type": "경험면접",
        "ncsClCd": "0202030103_22v2",
        "question_evidence_id": "ksa:test",
        "question": (
            "여러 업무 요청의 우선순위를 조정한 경험을 말씀해 주세요. "
            "직접 직무경험이 없다면 학교·프로젝트·봉사 등 가장 가까운 유사경험도 좋습니다."
        ),
        "follow_ups": follow_ups,
        "evaluation_points": points,
        "structured_follow_ups": [
            {
                "trigger": "always",
                "answer_slot": "situation_and_role",
                "evidence_target": points[0],
                "prompt": follow_ups[0],
                "ask_limit": 1,
            },
            {
                "trigger": "if_decision_or_action_basis_missing",
                "answer_slot": "decision_and_action",
                "evidence_target": points[1],
                "prompt": follow_ups[1],
                "ask_limit": 1,
            },
            {
                "trigger": "if_result_or_verification_missing",
                "answer_slot": "result_and_learning",
                "evidence_target": points[3],
                "prompt": follow_ups[2],
                "ask_limit": 1,
            },
        ],
        "assessment_guide": {
            "dimensions": points,
            "dimension_rubrics": rubrics,
            "dimension_weight_total": 100,
            "evaluation_point_mapping": [
                {
                    "evaluation_point_index": index,
                    "evaluation_point": point,
                    "dimension_index": index,
                    "dimension": point,
                    "weight": 25,
                }
                for index, point in enumerate(points, start=1)
            ],
            "weighted_score_formula": {
                "formula": "Σ(차원점수 × 차원가중치) ÷ 100",
                "dimension_score_scale": [1, 2, 3, 4, 5],
                "result_scale_min": 1.0,
                "result_scale_max": 5.0,
                "percentage_formula": "가중합 점수 ÷ 5 × 100",
                "rounding_rule": "소수점 둘째 자리에서 반올림한다.",
                "critical_floor_precedence": True,
            },
            "candidate_evidence_policy": {
                "direct_job_experience_required": False,
                "accepted_evidence_types": [
                    "동일 직무 실제 경험",
                    "다른 직무의 유사경험",
                    "학교·교육·프로젝트·봉사·동아리·인턴 경험",
                ],
                "evidence_state_rules": {
                    "no_direct_job_experience": {
                        "classification": "경험 경로 없음"
                    },
                    "analogous_evidence_provided": {
                        "classification": "평가 가능한 행동근거"
                    },
                    "no_observable_evidence_after_alternative": {
                        "classification": "근거 없음"
                    },
                },
                "standardized_alternative_scenario": {
                    "enabled": True,
                    "prompt": (
                        "관련 경험이 없는 모든 지원자에게 같은 자료 불일치와 일정 변경 상황을 "
                        "제시하고 판단 기준, 행동 순서와 결과 확인 방법을 묻는다."
                    ),
                    "delivery_rule": "모든 지원자에게 같은 문구와 준비시간을 제공한다.",
                    "scoring_rule": "경력 유무가 아니라 동일한 네 평가차원의 관찰행동만 평정한다.",
                },
            },
        },
        "task_conditions": {
            "candidate_instruction": "실제 또는 유사 경험 한 가지를 선택해 답하십시오.",
            "provided_materials": ["경험이 없을 때 제시할 동일 대체상황"],
            "required_outputs": ["판단 근거", "직접 행동", "결과 확인"],
            "standardization": "모든 지원자에게 같은 조건을 적용한다.",
        },
        "ncs_traceability": {
            "ncs_code": "0202030103_22v2",
            "ability_unit_name": "경영진 일정관리",
            "element_id": "651",
            "element_name": "경영진 일정표 작성하기",
            "ksa_factor": "조직에 대한 정보",
            "performance_criteria": [
                "일정표의 종류에 따른 작성 방법을 준수하여 일정표를 작성할 수 있다."
            ],
            "performance_criteria_linked": True,
        },
    }


def _passing_ai_response() -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "reviews": [
                                {
                                    "index": 1,
                                    "scores": {
                                        dimension: 5
                                        for dimension in review_service.AI_QUALITY_DIMENSIONS
                                    },
                                    "reason_codes": [],
                                    "regeneration_guidance_codes": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }


def test_field_ready_operating_contract_passes_deterministic_gate() -> None:
    assert review_service._deterministic_contract_issues(_field_ready_question()) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda row: row["assessment_guide"].pop("candidate_evidence_policy"),
            "candidate_experience_unfair",
        ),
        (
            lambda row: row["assessment_guide"]["dimensions"].pop(),
            "evaluation_dimension_mismatch",
        ),
        (
            lambda row: row["assessment_guide"].pop("weighted_score_formula"),
            "scoring_rule_incomplete",
        ),
        (
            lambda row: row["assessment_guide"]["dimension_rubrics"][0].update(
                {
                    "observable_evidence": row["assessment_guide"]["dimension_rubrics"][0][
                        "dimension"
                    ]
                }
            ),
            "scoring_rule_incomplete",
        ),
        (
            lambda row: row["structured_follow_ups"][1].update(
                {
                    "prompt": row["follow_ups"][1],
                    "evidence_target": row["evaluation_points"][3],
                }
            ),
            "probe_contract_mismatch",
        ),
    ],
)
def test_deterministic_gate_blocks_run118_issue_classes(mutate, expected: str) -> None:
    question = _field_ready_question()
    mutate(question)

    assert expected in review_service._deterministic_contract_issues(question)


def test_high_model_scores_cannot_override_deterministic_release_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _field_ready_question()
    question["assessment_guide"].pop("weighted_score_formula")
    monkeypatch.setattr(
        review_service,
        "post_chat_completions_with_retries",
        lambda **_kwargs: _passing_ai_response(),
    )

    result = review_service.review_interview_questions_with_ai(
        questions=[question],
        ncs_matches=[],
        ncs_ksa=[],
        interview_methods=["경험면접"],
        job_context={"notice": "신입 채용"},
        provider="openai_api",
        api_key_override="sk-test",
    )

    assert result["status"] == "failed"
    assert result["items"][0]["passed"] is False
    assert result["items"][0]["deterministic_release_issues"] == [
        "scoring_rule_incomplete"
    ]
    assert "scoring_rule_incomplete" in result["reason_codes"]
    assert "make_scoring_rule_executable" in result["items"][0][
        "regeneration_guidance_codes"
    ]


def test_reviewer_prompt_includes_field_contract_and_explicit_failure_rules() -> None:
    prompt = review_service._review_prompt(
        questions=[_field_ready_question()],
        ncs_matches=[],
        ncs_ksa=[],
        interview_methods=["경험면접"],
        job_context={"notice": "신입 채용"},
    )

    assert '"field_use_contract"' in prompt
    assert '"candidate_evidence_policy"' in prompt
    assert '"weighted_score_formula"' in prompt
    assert '"structured_follow_ups"' in prompt
    assert "일정표의 종류에 따른 작성 방법" in prompt
    assert "candidate_experience_unfair" in prompt
    assert "evaluation_dimension_mismatch" in prompt
    assert "scoring_rule_incomplete" in prompt
    assert "probe_contract_mismatch" in prompt


def test_present_contract_missing_all_operating_fields_fails_closed() -> None:
    question = _field_ready_question()
    for key in ("assessment_guide", "structured_follow_ups"):
        question.pop(key)
    # task_conditions still marks this as a field-use question; the gate must
    # not silently downgrade it to the legacy bare-question contract.
    assert set(review_service._deterministic_contract_issues(question)) == {
        "candidate_experience_unfair",
        "evaluation_dimension_mismatch",
        "scoring_rule_incomplete",
        "probe_contract_mismatch",
    }


def test_model_reported_contract_issue_is_blocking_even_with_high_scores() -> None:
    response = _passing_ai_response()
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["reviews"][0]["reason_codes"] = ["probe_contract_mismatch"]
    payload["reviews"][0]["regeneration_guidance_codes"] = [
        "align_probe_contract"
    ]
    response["choices"][0]["message"]["content"] = json.dumps(
        payload, ensure_ascii=False
    )

    parsed = review_service._parse_review(response, 1)

    assert parsed[0]["passed"] is False
    assert parsed[0]["blocking_reason_codes"] == ["probe_contract_mismatch"]


def test_follow_up_coherence_two_is_now_a_critical_failure() -> None:
    response = _passing_ai_response()
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["reviews"][0]["scores"]["follow_up_coherence"] = 2
    payload["reviews"][0]["reason_codes"] = ["follow_up_disconnected"]
    payload["reviews"][0]["regeneration_guidance_codes"] = [
        "deepen_answer_linked_followups"
    ]
    response["choices"][0]["message"]["content"] = json.dumps(
        payload, ensure_ascii=False
    )

    parsed = review_service._parse_review(response, 1)

    assert parsed[0]["passed"] is False
    assert parsed[0]["blocking_reason_codes"] == ["follow_up_disconnected"]


def test_main_operating_metadata_and_reviewer_release_gate_share_contract() -> None:
    method = "경험면접"
    points = main._method_evaluation_points(
        method,
        ["일정표 작성법"],
        "지식",
    )
    follow_ups = [
        "당시 어떤 상황이었고 본인이 맡은 역할은 무엇이었습니까?",
        "어떤 규정과 판단 기준을 확인하고 직접 어떤 행동을 선택했습니까?",
        "그 행동의 결과를 어떤 기록으로 확인했고 이후 무엇을 개선했습니까?",
    ]
    question = {
        "type": method,
        "evaluation_points": points,
        "follow_ups": follow_ups,
        "structured_follow_ups": main._structured_follow_ups_for_question(
            method,
            follow_ups,
            points,
        ),
        "assessment_guide": main._behavior_anchored_evaluation(
            method,
            "일정표 작성법",
            points,
            "지식",
        ),
        "task_conditions": {
            "candidate_instruction": "실제 또는 유사 경험 한 가지를 선택해 답하십시오."
        },
    }

    assert [
        row["evidence_target"] for row in question["structured_follow_ups"]
    ] == [points[0], points[2], points[3]]
    assert review_service._deterministic_contract_issues(question) == []

