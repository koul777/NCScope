from __future__ import annotations

from copy import deepcopy

import pytest

from app.main import (
    _attach_question_quality_report,
    _behavior_anchored_evaluation,
    _behavior_anchors_ok,
    _ensure_experience_candidate_evidence_notice,
    _public_assessment_guide,
    _probe_answer_slot_semantically_matches_prompt,
    _probe_contract_semantics_ok,
    _question_for_method,
    _structured_follow_ups_for_question,
    _structured_follow_ups_ok,
)


EVALUATION_POINTS = [
    "상황과 제약조건을 구체적으로 구분한다",
    "판단 기준과 본인 행동을 연결한다",
    "산출물 또는 결과를 확인할 근거를 제시한다",
    "위험과 후속조치를 설명한다",
]
FOLLOW_UPS = [
    "먼저 확인한 상황과 제약조건은 무엇입니까?",
    "그 판단 기준에 따라 본인이 직접 한 행동은 무엇입니까?",
    "결과를 무엇으로 확인했고 이후 어떤 조치를 했습니까?",
]


@pytest.mark.parametrize(
    "method",
    [
        "경험면접",
        "상황면접",
        "발표면접",
        "토론면접",
        "인바스켓면접",
        "직무지식면접",
        "창의적 문제해결력면접",
    ],
)
def test_structured_follow_ups_wrap_authored_prompts_without_rewriting(method: str) -> None:
    rows = _structured_follow_ups_for_question(method, FOLLOW_UPS, EVALUATION_POINTS)

    assert [row["prompt"] for row in rows] == FOLLOW_UPS
    assert rows[0]["trigger"] == "always"
    assert sum(row["trigger"] != "always" for row in rows) >= 2
    assert all(row["answer_slot"] for row in rows)
    assert all(
        _probe_answer_slot_semantically_matches_prompt(row["answer_slot"], row["prompt"])
        for row in rows
    )
    assert all(row["evidence_target"] in EVALUATION_POINTS for row in rows)
    assert all(
        _probe_contract_semantics_ok(
            row["answer_slot"], row["evidence_target"], row["prompt"]
        )
        for row in rows
    )
    assert all(row["ask_limit"] == 1 for row in rows)
    assert _structured_follow_ups_ok(rows, FOLLOW_UPS) is True


def test_experience_probe_targets_follow_meaning_instead_of_array_position() -> None:
    points = [
        "당시 상황과 본인 역할",
        "자료·도구를 사용한 수행 순서",
        "선택 근거와 직접 행동",
        "결과를 입증하는 기록",
    ]
    follow_ups = [
        "방금 말씀하신 사례에서 당시 본인이 맡은 역할과 구체적인 목표는 무엇이었나요?",
        "앞서 언급한 일정표 작성 수행에서 사용한 자료·도구와 실제 순서, 그 순서를 선택한 이유와 본인이 직접 취한 조치는 무엇인가요?",
        "답변에 결과 근거가 없다면, 결과를 확인한 문서·수치·기록·피드백과 이후 보완한 점을 설명해 주세요.",
    ]

    rows = _structured_follow_ups_for_question("경험면접", follow_ups, points)

    assert [row["evidence_target"] for row in rows] == [points[0], points[1], points[3]]
    assert _structured_follow_ups_ok(rows, follow_ups) is True


def test_v12_result_probe_uses_result_slot_under_strict_alignment() -> None:
    points = [
        "당시 상황과 본인 역할",
        "선택 근거와 직접 행동",
        "결과를 입증하는 기록과 이후 개선",
    ]
    follow_ups = [
        "당시 본인이 맡은 역할과 구체적인 목표는 무엇이었나요?",
        "그 판단 기준에 따라 본인이 직접 취한 행동은 무엇인가요?",
        "그 행동의 결과를 어떤 기록으로 확인했고 이후 무엇을 개선했습니까?",
    ]

    rows = _structured_follow_ups_for_question(
        "경험면접",
        follow_ups,
        points,
        require_evaluation_alignment=True,
    )

    assert rows[2]["trigger"] == "if_result_or_verification_missing"
    assert rows[2]["answer_slot"] == "result_and_learning"
    assert (
        _structured_follow_ups_ok(
            rows,
            follow_ups,
            points,
            require_evaluation_alignment=True,
        )
        is True
    )


def test_strict_result_trigger_rejects_record_only_slot_misplacement() -> None:
    points = [
        "당시 상황과 본인 역할",
        "선택 근거와 직접 행동",
        "결과를 입증하는 기록과 이후 개선",
        "판단 기준을 남긴 기록",
    ]
    follow_ups = [
        "당시 본인이 맡은 역할과 구체적인 목표는 무엇이었나요?",
        "그 판단 기준에 따라 본인이 직접 취한 행동은 무엇인가요?",
        "그 행동의 결과를 어떤 기록으로 확인했고 이후 무엇을 개선했습니까?",
    ]
    rows = _structured_follow_ups_for_question(
        "경험면접",
        follow_ups,
        points,
        require_evaluation_alignment=True,
    )
    record_only_prompt = "그 판단 기준을 어떤 기록에 남겼습니까?"
    follow_ups[2] = record_only_prompt
    rows[2].update(
        {
            "answer_slot": "document_and_urgency",
            "evidence_target": points[3],
            "prompt": record_only_prompt,
        }
    )

    assert _probe_contract_semantics_ok(
        rows[2]["answer_slot"], rows[2]["evidence_target"], rows[2]["prompt"]
    )
    assert (
        _structured_follow_ups_ok(
            rows,
            follow_ups,
            points,
            require_evaluation_alignment=True,
        )
        is False
    )


def test_strict_decision_trigger_rejects_result_only_slot_misplacement() -> None:
    points = [
        "당시 상황과 본인 역할",
        "선택 근거와 직접 행동",
        "결과를 입증하는 기록과 이후 개선",
    ]
    follow_ups = [
        "당시 본인이 맡은 역할과 구체적인 목표는 무엇이었나요?",
        "그 판단 기준에 따라 본인이 직접 취한 행동은 무엇인가요?",
        "그 행동의 결과를 어떤 기록으로 확인했고 이후 무엇을 개선했습니까?",
    ]
    rows = _structured_follow_ups_for_question(
        "경험면접",
        follow_ups,
        points,
        require_evaluation_alignment=True,
    )
    result_prompt = "그 행동의 결과를 어떤 수치로 확인했고 무엇을 개선했습니까?"
    follow_ups[1] = result_prompt
    rows[1].update(
        {
            "answer_slot": "result_and_learning",
            "evidence_target": points[2],
            "prompt": result_prompt,
        }
    )

    assert _probe_contract_semantics_ok(
        rows[1]["answer_slot"], rows[1]["evidence_target"], rows[1]["prompt"]
    )
    assert (
        _structured_follow_ups_ok(
            rows,
            follow_ups,
            points,
            require_evaluation_alignment=True,
        )
        is False
    )


def test_planned_ai_structured_follow_up_rejects_unscored_probe() -> None:
    follow_ups = [
        "방금 말씀하신 선택의 근거가 다른 자료와 충돌한다면 무엇을 바꾸시겠습니까?",
        "앞서 설명한 행동에서 본인이 직접 맡은 범위는 무엇입니까?",
        "그 조치가 완료된 뒤 실제 결과를 어떤 기록으로 확인했습니까?",
    ]
    unrelated_points = ["가", "나", "다", "라"]

    rows = _structured_follow_ups_for_question(
        "경험면접",
        follow_ups,
        unrelated_points,
        require_evaluation_alignment=True,
    )

    assert all(row["evidence_target"] in unrelated_points for row in rows)
    assert (
        _structured_follow_ups_ok(
            rows,
            follow_ups,
            unrelated_points,
            require_evaluation_alignment=True,
        )
        is False
    )


def test_structured_follow_up_gate_rejects_prompt_drift_and_unbounded_probe() -> None:
    rows = _structured_follow_ups_for_question("경험면접", FOLLOW_UPS, EVALUATION_POINTS)
    rows[1]["prompt"] = "새로 만든 질문"
    assert _structured_follow_ups_ok(rows, FOLLOW_UPS) is False

    rows = _structured_follow_ups_for_question("경험면접", FOLLOW_UPS, EVALUATION_POINTS)
    rows[2]["ask_limit"] = 2
    assert _structured_follow_ups_ok(rows, FOLLOW_UPS) is False

    rows = _structured_follow_ups_for_question("경험면접", FOLLOW_UPS, EVALUATION_POINTS)
    rows[0]["answer_slot"] = "agreement_and_followup"
    assert _structured_follow_ups_ok(rows, FOLLOW_UPS) is False

    rows = _structured_follow_ups_for_question("경험면접", FOLLOW_UPS, EVALUATION_POINTS)
    rows[2]["evidence_target"] = EVALUATION_POINTS[0]
    assert _structured_follow_ups_ok(rows, FOLLOW_UPS) is False


def test_behavior_guide_has_four_weighted_dimension_scorecards() -> None:
    guide = _behavior_anchored_evaluation(
        "경험면접",
        "문서 요구사항 확인",
        EVALUATION_POINTS,
        "기술",
    )

    assert guide["dimensions"] == EVALUATION_POINTS
    assert guide["dimension_weight_total"] == 100
    assert len(guide["dimension_rubrics"]) == 4
    assert sum(row["weight"] for row in guide["dimension_rubrics"]) == 100
    for point, row in zip(EVALUATION_POINTS, guide["dimension_rubrics"], strict=True):
        assert row["dimension"] == point
        assert point in row["observable_evidence"]
        assert "문서 요구사항 확인" in row["observable_evidence"]
        assert row["critical_floor"] == 1
        assert row["no_answer_rule"]
        assert set(row["anchors"]) == {"5", "3", "1"}
        assert all("문서 요구사항 확인" in row["anchors"][score] for score in ("5", "3", "1"))
        assert "2개 이상" in row["anchors"]["5"]
        assert "1개" in row["anchors"]["3"]
        assert "0개" in row["anchors"]["1"]
    assert [row["evaluation_point"] for row in guide["evaluation_point_mapping"]] == EVALUATION_POINTS
    assert [row["dimension"] for row in guide["evaluation_point_mapping"]] == EVALUATION_POINTS
    assert guide["weighted_score_formula"]["formula"] == "Σ(차원점수 × 차원가중치) ÷ 100"
    assert guide["weighted_score_formula"]["dimension_score_scale"] == [1, 2, 3, 4, 5]
    evidence_policy = guide["candidate_evidence_policy"]
    assert evidence_policy["direct_job_experience_required"] is False
    assert len(evidence_policy["accepted_evidence_types"]) >= 4
    assert evidence_policy["evidence_state_rules"]["no_direct_job_experience"]["score_effect"] == "감점 없음"
    assert evidence_policy["evidence_state_rules"]["no_observable_evidence_after_alternative"]["classification"] == "근거 없음"
    assert evidence_policy["standardized_alternative_scenario"]["enabled"] is True
    assert "문서 요구사항 확인" in evidence_policy["standardized_alternative_scenario"]["prompt"]
    assert guide["critical_floor"]["score"] == 1
    assert guide["no_answer_rule"]["score"] == 1
    assert _behavior_anchors_ok(guide) is True


def test_newcomer_can_use_analogous_evidence_or_the_same_alternative_scenario() -> None:
    guide = _behavior_anchored_evaluation(
        "경험면접",
        "문서 요구사항 확인",
        EVALUATION_POINTS,
        "기술",
        surface_focus="보고 자료 요구사항을 확인하기",
    )
    policy = guide["candidate_evidence_policy"]
    states = policy["evidence_state_rules"]

    assert policy["direct_job_experience_required"] is False
    assert any("학교" in row and "프로젝트" in row for row in policy["accepted_evidence_types"])
    assert states["no_direct_job_experience"]["score_effect"] == "감점 없음"
    assert states["analogous_evidence_provided"]["classification"] == "평가 가능한 행동근거"
    assert states["no_observable_evidence_after_alternative"]["classification"] == "근거 없음"
    alternative = policy["standardized_alternative_scenario"]
    assert alternative["enabled"] is True
    assert "모든 지원자에게 동일하게" in alternative["prompt"]
    assert "추가 사실을 임의로 만들지 않는다" in alternative["delivery_rule"]
    assert "직접경력 보유 여부가 아니라" in alternative["scoring_rule"]

    question = _question_for_method(
        "경험면접",
        "보고업무",
        "문서 요구사항 확인",
        "비서",
        "지시사항을 정확하게 파악하여 보고한다.",
        "기술",
    )
    assert "직무 경험이 없다면" in question
    assert "학교·직업교육 실습" in question
    assert "봉사·동아리" in question
    assert "동일한 대체상황" in question


def test_existing_experience_stem_gets_idempotent_candidate_evidence_notice() -> None:
    question = "임원 지원 순서를 조정한 실제 경험을 말씀해 주세요."

    repaired, changed = _ensure_experience_candidate_evidence_notice(question, "경험면접")
    repeated, changed_again = _ensure_experience_candidate_evidence_notice(repaired, "경험면접")

    assert changed is True
    assert "유사경험" in repaired
    assert "모두에게 같은 표준 대체상황" in repaired
    assert repeated == repaired
    assert changed_again is False


def test_non_experience_task_does_not_add_a_second_alternative_scenario() -> None:
    guide = _behavior_anchored_evaluation(
        "상황면접",
        "우선순위 판단",
        EVALUATION_POINTS,
        "지식",
    )

    alternative = guide["candidate_evidence_policy"]["standardized_alternative_scenario"]
    assert alternative["enabled"] is False
    assert alternative["prompt"] == ""
    assert "주질문 자체가" in alternative["delivery_rule"]
    assert _behavior_anchors_ok(guide) is True


def test_weight_formula_mapping_and_newcomer_policy_survive_public_export() -> None:
    guide = _behavior_anchored_evaluation(
        "경험면접",
        "문서 요구사항 확인",
        EVALUATION_POINTS,
        "기술",
    )

    public = _public_assessment_guide(guide)

    assert public["method"] == "경험면접"
    assert public["evaluation_point_mapping"] == guide["evaluation_point_mapping"]
    assert public["weighted_score_formula"] == guide["weighted_score_formula"]
    assert public["candidate_evidence_policy"] == guide["candidate_evidence_policy"]


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("dimension_rubrics", 0, "weight"), 20),
        (("dimension_rubrics", 0, "observable_evidence"), ""),
        (("dimension_rubrics", 0, "critical_floor"), 2),
        (("dimension_rubrics", 0, "no_answer_rule"), ""),
        (("dimension_rubrics", 0, "anchors"), {"5": "충분히 긴 문장"}),
        (("focus",), ""),
        (("critical_floor", "score"), 2),
        (("no_answer_rule", "rule"), ""),
        (("evaluation_point_mapping", 0, "dimension_index"), 2),
        (("weighted_score_formula", "formula"), "단순 평균"),
        (("candidate_evidence_policy", "direct_job_experience_required"), True),
        (("candidate_evidence_policy", "evidence_state_rules", "no_direct_job_experience"), {}),
        (("candidate_evidence_policy", "standardized_alternative_scenario", "enabled"), True),
    ],
)
def test_behavior_guide_gate_rejects_incomplete_operating_rules(
    mutation: tuple[object, ...],
    value: object,
) -> None:
    guide = _behavior_anchored_evaluation(
        "상황면접",
        "우선순위 판단",
        EVALUATION_POINTS,
        "지식",
    )
    target: object = guide
    for key in mutation[:-1]:
        target = target[key]  # type: ignore[index]
    target[mutation[-1]] = value  # type: ignore[index]

    assert _behavior_anchors_ok(guide) is False


def test_quality_report_backfills_operating_metadata_deterministically() -> None:
    question = {
        "type": "경험면접",
        "question": "보고 자료의 불일치를 발견하고 바로잡았던 실제 경험을 설명해 주세요.",
        "follow_ups": list(FOLLOW_UPS),
        "evaluation_points": list(EVALUATION_POINTS),
        "question_focus": "문서 요구사항 확인",
        "question_focus_type": "기술",
        "question_focus_surface": "보고 자료의 요구사항 확인",
        "competency": "보고업무",
        "ncsClCd": "0202030208_22v2",
        "ncs_detail": "비서",
        "schedule_role": "core",
        "planned_minutes": 7,
    }
    strategy = {
        "interview_questions": [question],
        "question_plan_used": {"total_main_count": 1},
    }

    _attach_question_quality_report(strategy)

    assert "유사경험" in question["question"]
    assert "모두에게 같은 표준 대체상황" in question["question"]
    assert question["candidate_evidence_notice_added"] is True
    assert question["follow_ups"] == FOLLOW_UPS
    assert [row["prompt"] for row in question["structured_follow_ups"]] == FOLLOW_UPS
    assert _structured_follow_ups_ok(question["structured_follow_ups"], FOLLOW_UPS)
    assert _behavior_anchors_ok(question["assessment_guide"])
    report_checks = strategy["question_quality_report"]["items"][0]["checks"]
    assert report_checks["structured_follow_ups"] is True

    first = deepcopy(question["structured_follow_ups"])
    _attach_question_quality_report(strategy)
    assert question["structured_follow_ups"] == first


def test_quality_report_keeps_planned_ai_probe_scoring_mismatch_blocking() -> None:
    question = {
        "type": "경험면접",
        "question": "보고 자료의 불일치를 발견하고 바로잡았던 실제 경험을 설명해 주세요.",
        "follow_ups": list(FOLLOW_UPS),
        "evaluation_points": ["가", "나", "다", "라"],
        "question_focus": "문서 요구사항 확인",
        "question_focus_type": "기술",
        "question_focus_surface": "보고 자료의 요구사항 확인",
        "competency": "보고업무",
        "ncsClCd": "0202030208_22v2",
        "ncs_detail": "비서",
        "model_question_preserved": True,
        "strict_probe_evaluation_alignment": True,
    }
    strategy = {
        "interview_questions": [question],
        "question_plan_used": {"total_main_count": 1},
    }

    _attach_question_quality_report(strategy)

    structured = question["structured_follow_ups"]
    assert all(row["evidence_target"] in question["evaluation_points"] for row in structured)
    assert (
        _structured_follow_ups_ok(
            structured,
            question["follow_ups"],
            question["evaluation_points"],
            require_evaluation_alignment=True,
        )
        is False
    )
    assert (
        strategy["question_quality_report"]["items"][0]["checks"][
            "structured_follow_ups"
        ]
        is False
    )
