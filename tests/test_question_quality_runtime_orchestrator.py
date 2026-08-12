from __future__ import annotations

import pytest

from app.services.question_quality_orchestrator import (
    evaluate_ksa_measurement,
    orchestrate_question_set,
)
from app.main import (
    _behavior_anchored_evaluation,
    _followups_for_method,
    _method_evaluation_points,
    _question_for_method,
    _question_variation_constraint,
    _run_runtime_question_quality_orchestration,
    _task_conditions_for_method,
)


def _valid_experience_question(text: str = "") -> dict:
    focus = "문서 오류 점검 능력"
    return {
        "type": "경험면접",
        "question_focus": focus,
        "ksa_refs": [focus],
        "question": text
        or (
            "문서관리 업무에서 문서 오류 점검 능력을 발휘해 자료 불일치를 해결한 경험을 "
            "말씀해 주세요. 당시 상황과 본인 역할, 선택한 행동, 결과와 학습을 설명해 주세요."
        ),
    }


def test_rejects_ksa_name_restatement_without_observable_behavior() -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": "문서 오류 점검 능력",
            "question": "문서 오류 점검 능력과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
        }
    )

    assert result["passed"] is False
    assert result["checks"]["not_ksa_restatement"] is False
    assert result["checks"]["observable_task"] is False


def test_accepts_experience_question_that_elicits_actual_ksa_evidence() -> None:
    result = evaluate_ksa_measurement(_valid_experience_question())

    assert result["passed"] is True
    assert result["observation_group_hits"] == [True, True, True]


@pytest.mark.parametrize(
    ("method", "focus_type", "focus", "question"),
    [
        ("경험면접", "기술", "자료 검증 능력", "자료 검증 능력 관련 상황 본인 행동 결과"),
        ("상황면접", "태도", "민원 대응 태도", "민원 대응 태도 상황 판단 기준 순서 행동 위험"),
        ("발표면접", "지식", "자료 분석 지식", "자료 분석 지식 발표 준비시간 자료 진단 대안 실행 성과지표"),
        ("토론면접", "태도", "협업 태도", "협업 태도 토론 입장 반대 근거 합의 실행"),
        ("인바스켓면접", "기술", "문서 분류 능력", "문서 분류 능력 인바스켓 제한시간 문서 우선순위 보고 위임"),
        ("직무지식면접", "지식", "회계 기준 지식", "회계 기준 지식 절차 기준 적용 예외 산출물 품질"),
        (
            "창의적 문제해결력면접",
            "기술",
            "문제 분석 능력",
            "문제 분석 능력 문제 정의 대안 검증 실현가능성 의사결정 실행",
        ),
    ],
)
def test_rejects_keyword_stuffing_without_an_interview_task(
    method: str,
    focus_type: str,
    focus: str,
    question: str,
) -> None:
    result = evaluate_ksa_measurement(
        {
            "type": method,
            "question_focus": focus,
            "question_focus_type": focus_type,
            "question": question,
        }
    )

    assert result["passed"] is False
    assert result["checks"]["elicits_response"] is False


@pytest.mark.parametrize(
    ("method", "focus_type", "focus", "question"),
    [
        ("경험면접", "기술", "자료 검증 능력", "자료 검증 능력 관련 상황과 본인 행동, 결과를 설명해 주세요."),
        ("상황면접", "태도", "민원 대응 태도", "민원 대응 태도 상황에서 판단 기준, 행동 순서와 위험을 제시해 주세요."),
        ("발표면접", "지식", "자료 분석 지식", "자료 분석 지식 발표에서 자료 진단, 대안, 실행과 성과지표를 발표해 주세요."),
        ("토론면접", "태도", "협업 태도", "협업 태도 토론에서 입장, 반대 근거, 합의와 실행을 제시해 주세요."),
        ("인바스켓면접", "기술", "문서 분류 능력", "문서 분류 능력 인바스켓에서 제한시간, 우선순위, 보고와 위임을 제시해 주세요."),
        ("직무지식면접", "지식", "회계 기준 지식", "회계 기준 지식의 절차, 적용, 예외, 산출물과 품질을 설명해 주세요."),
        (
            "창의적 문제해결력면접",
            "기술",
            "문제 분석 능력",
            "문제 분석 능력의 문제 정의, 대안, 검증, 실현가능성과 실행을 제시해 주세요.",
        ),
    ],
)
def test_rejects_polite_keyword_lists_without_enough_task_detail(
    method: str,
    focus_type: str,
    focus: str,
    question: str,
) -> None:
    result = evaluate_ksa_measurement(
        {
            "type": method,
            "question_focus": focus,
            "question_focus_type": focus_type,
            "question": question,
        }
    )

    assert result["passed"] is False
    assert result["checks"]["elicits_response"] is True
    assert result["checks"]["sufficient_task_detail"] is False


def test_rejects_user_reported_shallow_wording_variant() -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": "자료 검증 능력",
            "question_focus_type": "기술",
            "question": "'자료 검증 능력'과 관련하여 실제 경험이 있으십니까? 말씀해주세요.",
        }
    )

    assert result["passed"] is False
    assert result["checks"]["not_ksa_restatement"] is False


def test_rejects_shallow_ksa_restatement_even_with_star_boilerplate() -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": "자료 검증 능력",
            "question_focus_type": "기술",
            "question": (
                "자료 검증 능력과 관련된 경험을 말씀해 주세요. "
                "당시 상황과 본인 역할, 행동, 결과와 학습을 설명해 주세요."
            ),
        }
    )

    assert result["checks"]["observable_task"] is True
    assert result["checks"]["not_ksa_restatement"] is False
    assert result["passed"] is False


@pytest.mark.parametrize(
    "intro",
    [
        "자료 검증 능력에 대한 경험에 대해 말씀해 주세요.",
        "자료 검증 능력과 관련된 사례가 있다면 구체적으로 이야기해 주세요.",
        "자료 검증 능력에 관한 사례를 들어 설명해 주세요.",
        "자료 검증 능력과 관련해 본인의 실제 경험을 소개해 주세요.",
        "자료 검증 능력과 관련된 경험을 공유해 주세요.",
        "자료 검증 능력과 관련된 사례를 들려주세요.",
        "자료 검증 능력과 관련된 경험을 서술해 주세요.",
    ],
)
def test_rejects_paraphrased_self_report_request_even_with_star_boilerplate(intro: str) -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": "자료 검증 능력",
            "question_focus_type": "기술",
            "question": (
                f"{intro} 당시 상황과 본인 역할, 선택한 행동, 결과와 학습을 설명해 주세요."
            ),
        }
    )

    assert result["checks"]["observable_task"] is True
    assert result["checks"]["not_ksa_restatement"] is False
    assert result["passed"] is False


@pytest.mark.parametrize(
    ("focus", "intro"),
    [
        ("자료 검증 능력", "자료 검증 능력을 발휘한 사례를 말씀해 주세요."),
        ("자료 검증 능력", "자료 검증 능력을 활용했던 경험을 설명해 주세요."),
        ("자료 검증 능력", "자료 검증 능력을 보여 준 사례를 소개해 주세요."),
        ("자료 검증 기술", "자료 검증 기술을 사용한 경험을 말씀해 주세요."),
        ("자료 검증 기술", "자료 검증 기술을 수행했던 경험을 공유해 주세요."),
        ("정확성 유지 태도", "정확성 유지 태도를 보여준 사례를 말씀해 주세요."),
        ("정확성 유지 태도", "정확성 유지 태도를 실천한 사례를 들려주세요."),
        ("보안 법규 지식", "보안 법규 지식을 적용했던 경험을 말씀해 주세요."),
        ("자료 검증 능력", "자료 검증 능력에 대해 발휘한 사례를 말씀해 주세요."),
        ("자료 검증 능력", "자료 검증 능력에 대한 경험을 말씀해 주세요."),
    ],
)
def test_rejects_label_adjacent_self_report_synonyms_with_star_boilerplate(
    focus: str,
    intro: str,
) -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": focus,
            "question": (
                f"{intro} 당시 상황과 본인 역할, 선택한 행동, 결과와 학습을 설명해 주세요."
            ),
        }
    )

    assert result["checks"]["not_ksa_restatement"] is False
    assert result["passed"] is False


def test_allows_focus_when_concrete_action_and_output_precede_experience_marker() -> None:
    result = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": "자료 검증 능력",
            "question_focus_type": "기술",
            "question": (
                "자료 검증 능력을 발휘해 원자료와 문서 초안을 대조하고 오류 검증표를 완성한 경험을 말씀해 주세요. "
                "당시 상황과 본인 역할, 선택한 행동, 결과와 학습을 설명해 주세요."
            ),
        }
    )

    assert result["passed"] is True


def test_rejects_generic_ksa_case_intro_but_allows_specific_action_before_experience() -> None:
    generic = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": "자료 검증 능력",
            "question_focus_type": "기술",
            "question": (
                "자료 검증 능력에 관한 사례를 소개해 주세요. "
                "당시 상황과 본인 역할, 행동, 결과와 학습을 설명해 주세요."
            ),
        }
    )
    specific = evaluate_ksa_measurement(
        {
            "type": "경험면접",
            "question_focus": "자료 검증 능력",
            "question_focus_type": "기술",
            "question": (
                "자료 검증 능력을 발휘해 원자료 불일치를 찾아 수정하고 검증 결과표를 완성한 경험을 말씀해 주세요. "
                "당시 상황과 본인 역할, 선택한 행동, 결과와 학습을 설명해 주세요."
            ),
        }
    )

    assert generic["checks"]["not_ksa_restatement"] is False
    assert generic["passed"] is False
    assert specific["passed"] is True


def test_ksa_type_requires_evidence_chain_not_one_generic_marker() -> None:
    rows = [
        {
            "type": "경험면접",
            "question_focus": "보안 법규 지식",
            "question_focus_type": "지식",
            "question": (
                "보안 법규 지식이 필요했던 경험을 말씀해 주세요. "
                "당시 상황과 본인 역할, 행동, 결과와 학습을 설명해 주세요."
            ),
        },
        {
            "type": "경험면접",
            "question_focus": "문서 작성 기술",
            "question_focus_type": "기술",
            "question": (
                "문서 작성 기술을 수행한 경험을 말씀해 주세요. "
                "당시 상황과 본인 역할, 행동, 선택 이유와 학습을 설명해 주세요."
            ),
        },
        {
            "type": "상황면접",
            "question_focus": "정확성을 지키려는 태도",
            "question_focus_type": "태도",
            "question": (
                "정확성을 지키려는 태도가 필요한 상황입니다. "
                "어떤 판단 기준과 행동 순서로 보고하고 실행하시겠습니까?"
            ),
        },
    ]

    results = [evaluate_ksa_measurement(row) for row in rows]
    assert all(result["checks"]["observable_task"] is True for result in results)
    assert all(result["checks"]["ksa_type_operationalized"] is False for result in results)
    assert all(result["passed"] is False for result in results)


def test_all_method_templates_operationalize_each_ksa_type() -> None:
    methods = [
        "경험면접",
        "상황면접",
        "발표면접",
        "토론면접",
        "인바스켓면접",
        "직무지식면접",
        "창의적 문제해결력면접",
    ]
    focuses = {
        "지식": "문서 보안 법규 지식",
        "기술": "문서 오류 검증 기술",
        "태도": "정확성을 유지하려는 태도",
    }

    failures: list[tuple[str, str, dict]] = []
    for method in methods:
        for focus_type, focus in focuses.items():
            question = _question_for_method(
                method,
                "문서관리",
                focus,
                "사무행정",
                "문서 오류를 확인하고 품질을 관리한다",
                focus_type,
                variation_index=23,
            )
            result = evaluate_ksa_measurement(
                {
                    "type": method,
                    "question_focus": focus,
                    "question_focus_type": focus_type,
                    "question": question,
                }
            )
            assert "  " not in question
            if not result["passed"]:
                failures.append((method, focus_type, result))

    assert failures == []


def test_generation_offset_selects_a_new_variation_without_unbounded_history() -> None:
    focus = "문서 오류 검증 기술"
    base_question = {
        "type": "경험면접",
        "method": "경험면접",
        "ncsClCd": "U1",
        "competency": "문서관리",
        "ncs_detail": "사무행정",
        "ncsSubdCdnm": "사무행정",
        "question_focus": focus,
        "question_focus_type": "기술",
        "ksa_refs": [focus],
        "question": f"{focus}과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
        "follow_ups": ["어떤 경험입니까?", "무엇을 했습니까?", "결과는 어땠습니까?"],
        "evaluation_points": ["성실성", "태도", "열정", "표현력"],
    }
    ksa = [
        {
            "ncsClCd": "U1",
            "compeUnitName": "문서관리",
            "factorName": focus,
            "ksaTypeName": "기술",
            "factorSource": "ncs-mcp",
            "ksaStatus": "official",
        }
    ]
    outputs: list[str] = []
    for generation_offset in (0, 1):
        result = _run_runtime_question_quality_orchestration(
            {
                "interview_questions": [dict(base_question)],
                "question_plan_used": {"total_main_count": 1, "follow_up_count": 3},
            },
            question_plan={"total_main_count": 1, "follow_up_count": 3},
            ncs_ksa=ksa,
            avoid_questions=[],
            generation_offset=generation_offset,
        )
        metadata = result["question_quality_orchestration"]
        assert metadata["status"] == "passed"
        assert metadata["generation_offset"] == generation_offset
        outputs.append(result["interview_questions"][0]["question"])

    assert outputs[0] != outputs[1]


def test_situation_template_deduplicates_overlapping_focus_and_domain_context() -> None:
    question = _question_for_method(
        "상황면접",
        "문서관리",
        "문서 오류 검증 기술",
        "사무행정",
        "문서 기준 불일치를 확인하고 오류를 검증한다.",
        "기술",
        variation_index=1,
    )

    assert question.count("문서 기준 불일치") == 1
    assert "  " not in question


def test_long_running_variation_pool_has_1152_unique_combinations() -> None:
    initial_count = 19
    variations = [
        _question_variation_constraint(initial_count + offset)
        for offset in range(1152)
    ]

    assert len(set(variations)) == 1152


def test_variation_pool_uses_cross_domain_operational_roles() -> None:
    variations = [
        _question_variation_constraint(index)
        for index in range(19, 19 + 180)
    ]
    merged = "\n".join(variations)

    assert "안전 담당은 작업 중지를 요구" not in merged
    assert "고객 담당은 지연 안내를 우선" not in merged
    assert "품질 담당자는 추가 검증" in merged
    assert "서비스 담당자는 지연 안내와 대안 제시" in merged


def test_each_interview_method_requires_its_observable_task_shape() -> None:
    rows = [
        {
            "type": "상황면접",
            "question_focus": "민원 응대 기준",
            "question": (
                "고객상담 업무에서 민원 응대 기준이 다른 요청과 충돌한 상황입니다. "
                "어떤 근거와 기준으로 판단하고 위험을 통제하며 보고와 실행 순서를 정하시겠습니까?"
            ),
        },
        {
            "type": "발표면접",
            "question_focus": "운영 자료 분석",
            "question": (
                "[발표과제] 운영 자료 분석 자료를 받아 준비시간 20분 후 현황과 원인을 진단하고 "
                "대안, 실행 우선순위, 성과지표를 발표한 뒤 질의응답에 답해 주세요."
            ),
        },
        {
            "type": "토론면접",
            "question_focus": "품질 기준 준수",
            "question": (
                "[토론과제] 품질 기준 준수를 강화할 입장과 처리 속도 입장이 충돌합니다. "
                "입장발표와 근거 제시 후 반대 의견을 경청·조정하여 최종 합의안을 도출해 주세요."
            ),
        },
        {
            "type": "인바스켓면접",
            "question_focus": "문서 우선순위 판단",
            "question": (
                "[인바스켓과제] 제한시간 안에 문서 우선순위 판단이 필요한 여러 문서와 요청이 "
                "동시에 들어왔습니다. 분류와 우선순위를 정하고 보고·위임·직접처리 첫 조치를 제시해 주세요."
            ),
        },
        {
            "type": "직무지식면접",
            "question_focus": "문서 보안 규정 지식",
            "question": (
                "문서 보안 규정 지식을 근거로 적용 절차와 기준을 설명하고, 예외상황 판단, "
                "산출물 품질 점검과 오류 예방 방법을 제시해 주세요."
            ),
        },
        {
            "type": "창의적 문제해결력면접",
            "question_focus": "대안 타당성 검증 능력",
            "question": (
                "[창의적 문제해결력과제] 대안 타당성 검증 능력이 필요한 문제를 미래예측 관점에서 "
                "정의하고 원인 가설, 창의적 대안, 검증 방법과 실현가능성, 의사결정·실행계획·성과지표를 제시해 주세요."
            ),
        },
    ]

    assert all(evaluate_ksa_measurement(row)["passed"] is True for row in rows)


def test_orchestrator_repairs_history_duplicate_and_rechecks() -> None:
    original = _valid_experience_question()

    def repair(item: dict, index: int, reasons: list[str], attempt: int) -> dict:
        assert index == 0
        assert "history_duplicate" in reasons
        repaired = dict(item)
        repaired["question"] = (
            "문서관리 업무에서 문서 오류 점검 능력을 발휘해 예외 규정으로 생긴 기록 누락을 "
            "바로잡은 경험을 말씀해 주세요. 당시 상황과 본인 역할, 선택한 행동, 결과와 학습을 설명해 주세요."
        )
        return repaired

    questions, metadata = orchestrate_question_set(
        [original],
        avoid_questions=[original["question"]],
        repair_question=repair,
    )

    assert questions[0]["question"] != original["question"]
    assert metadata["status"] == "passed"
    assert metadata["repaired_count"] == 1
    assert metadata["unresolved_count"] == 0


def test_orchestrator_can_require_a_logged_repair_without_faking_history() -> None:
    original = _valid_experience_question()

    def repair(item: dict, _index: int, reasons: list[str], _attempt: int) -> dict:
        assert "generation_offset_variation" in reasons
        repaired = dict(item)
        repaired["question"] = (
            "문서관리 업무에서 문서 오류 점검 능력을 발휘해 승인 지연으로 생긴 기록 누락을 "
            "바로잡은 경험을 말씀해 주세요. 당시 상황과 본인 역할, 선택한 행동, 결과와 학습을 설명해 주세요."
        )
        return repaired

    questions, metadata = orchestrate_question_set(
        [original],
        repair_question=repair,
        required_repair_reasons={0: ["generation_offset_variation"]},
    )

    assert questions[0]["question"] != original["question"]
    assert metadata["status"] == "passed"
    assert metadata["items"][0]["initial_issues"] == ["generation_offset_variation"]
    assert metadata["items"][0]["final_issues"] == []


def test_orchestrator_isolates_repair_errors_per_question() -> None:
    weak = {
        "type": "경험면접",
        "question_focus": "문서 오류 점검 능력",
        "question": "문서 오류 점검 능력과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
    }

    def broken_repair(_item: dict, _index: int, _reasons: list[str], _attempt: int) -> dict:
        raise RuntimeError("repair failed")

    questions, metadata = orchestrate_question_set(
        [weak, _valid_experience_question()],
        repair_question=broken_repair,
        max_repair_attempts=2,
    )

    assert len(questions) == 2
    assert metadata["status"] == "needs_review"
    assert metadata["repair_error_count"] == 2
    assert metadata["items"][0]["errors"]
    assert metadata["items"][1]["errors"] == []
    assert "not_ksa_restatement" in metadata["items"][0]["final_issues"]
    assert "repair_exhausted" in metadata["items"][0]["final_issues"]


def test_exhausted_repair_preserves_original_issues_and_separates_last_candidate_diagnostics() -> None:
    weak = {
        "type": "경험면접",
        "question_focus": "문서 오류 점검 능력",
        "question_focus_type": "기술",
        "question": "문서 오류 점검 능력과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
    }

    def still_invalid(item: dict, _index: int, _reasons: list[str], _attempt: int) -> dict:
        candidate = dict(item)
        candidate["question"] = (
            "문서 오류 점검을 실제로 수행할 단계와 산출물을 설명해 주세요."
        )
        return candidate

    questions, metadata = orchestrate_question_set(
        [weak],
        avoid_questions=["문서 오류 점검을 실제로 수행할 단계와 산출물을 설명해 주세요."],
        repair_question=still_invalid,
        max_repair_attempts=2,
    )

    event = metadata["items"][0]
    assert questions[0] == weak
    assert "not_ksa_restatement" in event["final_issues"]
    assert "repair_exhausted" in event["final_issues"]
    assert "history_duplicate" in event["last_candidate_issues"]
    assert "not_ksa_restatement" not in event["last_candidate_issues"]


def _runtime_strategy(
    method: str = "경험면접",
    focus: str = "문서 오류 점검 능력",
    focus_type: str = "기술",
) -> tuple[dict, dict, list[dict]]:
    code = "0202030201_25v3"
    subject = "문서작성"
    detail = "사무행정"
    evaluation_points = _method_evaluation_points(method, [focus], focus_type)
    question = {
        "type": method,
        "method": method,
        "ncsClCd": code,
        "competency": subject,
        "ncs_detail": detail,
        "ncsSubdCdnm": detail,
        "question_focus": focus,
        "question_focus_type": focus_type,
        "question_focus_source": "official_ksa",
        "ksa_refs": [focus],
        "question": _question_for_method(method, subject, focus, detail, "문서 오류를 점검한다", focus_type),
        "follow_ups": _followups_for_method(method, subject, focus, 3, focus_type=focus_type),
        "evaluation_points": evaluation_points,
        "question_source": "template_fallback",
        "model_question_preserved": False,
        "task_conditions": _task_conditions_for_method(method, subject, focus, detail, "문서 오류를 점검한다"),
        "assessment_guide": _behavior_anchored_evaluation(method, focus, evaluation_points),
    }
    strategy = {
        "interview_questions": [question],
        "question_plan_used": {"total_main_count": 1, "follow_up_count": 3},
    }
    plan = {"total_main_count": 1, "follow_up_count": 3}
    ksa = [
        {
            "ncsClCd": code,
            "compeUnitName": subject,
            "factorName": focus,
            "factorSource": "ncs-mcp",
            "ksaStatus": "official",
            "ksaTypeName": focus_type,
        }
    ]
    return strategy, plan, ksa


def test_runtime_orchestration_rebuilds_history_duplicate_and_keeps_count() -> None:
    strategy, plan, ksa = _runtime_strategy()
    previous = strategy["interview_questions"][0]["question"]

    result = _run_runtime_question_quality_orchestration(
        strategy,
        question_plan=plan,
        ncs_ksa=ksa,
        avoid_questions=[previous],
    )

    questions = result["interview_questions"]
    orchestration = result["question_quality_orchestration"]
    assert len(questions) == 1
    assert questions[0]["question"] != previous
    assert questions[0]["question_source"] == "quality_orchestrator_repair"
    assert orchestration["repaired_count"] == 1
    assert orchestration["unresolved_count"] == 0
    assert orchestration["status"] == "passed"
    assert result["question_quality_report"]["passed"] is True


def test_runtime_repairs_invalid_task_conditions_without_replacing_question() -> None:
    strategy, plan, ksa = _runtime_strategy()
    original_question = strategy["interview_questions"][0]["question"]
    strategy["interview_questions"][0]["task_conditions"] = {
        "candidate_instruction": "",
        "time_plan": [],
        "provided_materials": [],
        "required_outputs": [],
    }

    result = _run_runtime_question_quality_orchestration(
        strategy,
        question_plan=plan,
        ncs_ksa=ksa,
        avoid_questions=[],
    )

    metadata = result["question_quality_orchestration"]
    repaired = result["interview_questions"][0]
    assert metadata["status"] == "passed"
    assert metadata["full_quality_unresolved_count"] == 0
    assert metadata["unresolved_count"] == 0
    assert repaired["question"] == original_question
    assert repaired["quality_repaired_fields"] == ["task_conditions"]
    assert repaired["task_conditions"]["candidate_instruction"]
    assert repaired["task_conditions"]["provided_materials"]
    assert repaired["task_conditions"]["required_outputs"]


def test_runtime_empty_generation_reports_one_unresolved_candidate_failure() -> None:
    result = _run_runtime_question_quality_orchestration(
        {"interview_questions": [], "question_plan_used": {"total_main_count": 1}},
        question_plan={"total_main_count": 1, "follow_up_count": 3},
        ncs_ksa=[],
        avoid_questions=[],
    )

    metadata = result["question_quality_orchestration"]
    assert metadata["status"] == "needs_review"
    assert metadata["question_count"] == 0
    assert metadata["initial_failure_count"] == 1
    assert metadata["unresolved_count"] == 1


def test_runtime_metadata_exposes_question_count_gap() -> None:
    strategy, _plan, ksa = _runtime_strategy()
    plan = {"total_main_count": 2, "follow_up_count": 3}
    strategy["question_plan_used"] = plan

    result = _run_runtime_question_quality_orchestration(
        strategy,
        question_plan=plan,
        ncs_ksa=ksa,
        avoid_questions=[],
    )

    metadata = result["question_quality_orchestration"]
    assert metadata["status"] == "needs_review"
    assert metadata["question_count_gap"] == 1
    assert metadata["unresolved_count"] == 1
    assert metadata["stages"][0]["status"] == "partial"


def test_runtime_thirty_generation_simulation_has_no_repeat_or_empty_result() -> None:
    history: list[str] = []
    generated: list[str] = []

    for _cycle in range(30):
        strategy, plan, ksa = _runtime_strategy()
        result = _run_runtime_question_quality_orchestration(
            strategy,
            question_plan=plan,
            ncs_ksa=ksa,
            avoid_questions=history,
        )
        questions = result["interview_questions"]
        assert len(questions) == 1
        assert result["question_quality_orchestration"]["unresolved_count"] == 0
        generated.append(questions[0]["question"])
        history.extend(generated[-1:])

    assert len(set(generated)) == 30


def test_runtime_thirty_generations_across_methods_and_ksa_types_pass_full_gate() -> None:
    methods = [
        "경험면접",
        "상황면접",
        "발표면접",
        "토론면접",
        "인바스켓면접",
        "직무지식면접",
        "창의적 문제해결력면접",
    ]
    focuses = {
        "지식": "문서 보안 법규 지식",
        "기술": "문서 오류 검증 기술",
        "태도": "정확성을 유지하려는 태도",
    }

    for method in methods:
        for focus_type, focus in focuses.items():
            history: list[str] = []
            for cycle in range(30):
                strategy, plan, ksa = _runtime_strategy(method, focus, focus_type)
                result = _run_runtime_question_quality_orchestration(
                    strategy,
                    question_plan=plan,
                    ncs_ksa=ksa,
                    avoid_questions=history,
                )
                question = result["interview_questions"][0]["question"]
                metadata = result["question_quality_orchestration"]
                report = result["question_quality_report"]
                assert metadata["status"] == "passed", (
                    method,
                    focus_type,
                    cycle + 1,
                    report["items"][0]["issues"],
                    result["interview_questions"][0].get("question_focus_surface"),
                    result["interview_questions"][0].get("follow_ups"),
                    result["interview_questions"][0].get("evaluation_points"),
                    metadata,
                )
                assert metadata["unresolved_count"] == 0
                assert report["passed"] is True, (method, focus_type, cycle + 1, report)
                assert question not in history
                history.append(question)

            assert len(set(history)) == 30


def test_generation_offset_rotates_deterministic_fallback_but_preserves_valid_model_candidate() -> None:
    fallback_strategy, plan, ksa = _runtime_strategy()
    fallback_original = fallback_strategy["interview_questions"][0]["question"]
    fallback_result = _run_runtime_question_quality_orchestration(
        fallback_strategy,
        question_plan=plan,
        ncs_ksa=ksa,
        avoid_questions=[],
        generation_offset=1,
    )

    model_strategy, model_plan, model_ksa = _runtime_strategy()
    model_strategy["interview_questions"][0]["question_source"] = "model"
    model_original = model_strategy["interview_questions"][0]["question"]
    model_result = _run_runtime_question_quality_orchestration(
        model_strategy,
        question_plan=model_plan,
        ncs_ksa=model_ksa,
        avoid_questions=[],
        generation_offset=1,
    )

    fallback_metadata = fallback_result["question_quality_orchestration"]
    model_metadata = model_result["question_quality_orchestration"]
    assert fallback_result["interview_questions"][0]["question"] != fallback_original
    assert "generation_offset_variation" in fallback_metadata["items"][0]["initial_issues"]
    assert fallback_metadata["repaired_count"] == 1
    assert model_result["interview_questions"][0]["question"] == model_original
    assert model_metadata["repaired_count"] == 0
