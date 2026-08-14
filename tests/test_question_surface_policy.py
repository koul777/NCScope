from __future__ import annotations

import json

import pytest

from app.main import (
    _adjust_generated_questions,
    _attach_ksa_evidence_to_strategy,
    _operating_conditions_separated,
    _parse_question_plan_json,
    _question_for_method,
    _task_conditions_for_method,
)
from app.services.jd_strategy import _build_ncs_code_template_fallback_question
from app.services.question_generation import _attach_candidate_surface_evidence
from app.services.question_surface import (
    build_question_task_frame,
    has_dangling_surface,
    official_ksa_surface_aliases,
    public_task_object,
    stable_ksa_evidence_id,
)


FACTOR = "승인된 변경에 대한 지식"
TRUNCATED_FACTOR = "승인된 변경에 대한"
PUBLIC_FOCUS = "승인된 변경 관련 확인·판단 기준"
NCS_CODE = "0101010205_17v2"


def _evidence_row() -> dict[str, str]:
    return {
        "ncsClCd": NCS_CODE,
        "compeUnitName": "프로젝트 인적자원관리",
        "factorName": FACTOR,
        "ksaTypeName": "지식",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }


def test_timed_assessment_templates_separate_task_from_operating_conditions() -> None:
    methods = (
        "발표면접",
        "토론면접",
        "인바스켓면접",
        "창의적 문제해결력면접",
    )

    for method in methods:
        question = _question_for_method(
            method=method,
            subject="문서작성",
            focus="문서 요구사항 분석 기술",
            detail="사무행정",
            comp_def="요청사항을 분석해 문서를 작성한다.",
            focus_type="기술",
        )
        conditions = _task_conditions_for_method(
            method=method,
            subject="문서작성",
            focus="문서 요구사항 분석 기술",
            detail="사무행정",
            comp_def="요청사항을 분석해 문서를 작성한다.",
            focus_type="기술",
        )

        assert _operating_conditions_separated(method, question) is True
        assert conditions["time_plan"]
        assert not any(
            marker in question
            for marker in ("준비시간", "제한시간", "토론시간", "첫 15분", "5분", "7분", "20분", "30분")
        )


def test_operating_condition_gate_rejects_timing_embedded_in_task_text() -> None:
    assert _operating_conditions_separated(
        "발표면접",
        "[발표과제] 준비시간 20분 후 개선안을 5분 발표하고 질의응답에 답해 주세요.",
    ) is False
    assert _operating_conditions_separated(
        "인바스켓면접",
        "[인바스켓과제] 제한시간 30분 안에 문서를 분류하고 첫 15분 행동을 제시하세요.",
    ) is False
    assert _operating_conditions_separated(
        "경험면접",
        "10분 이내 대응한 실제 경험을 말씀해 주세요.",
    ) is True


def test_dangling_knowledge_label_becomes_grammatical_public_focus() -> None:
    row = _evidence_row()

    frame = build_question_task_frame(
        evidence_row=row,
        factor_name=FACTOR,
        ksa_type="지식",
        competency_name="프로젝트 인적자원관리",
    )

    assert frame["task_object"] == PUBLIC_FOCUS
    assert frame["surface_source"] == "factor_repair"
    assert not has_dangling_surface(frame["task_object"])
    assert frame["evidence_id"] == stable_ksa_evidence_id(row)
    assert official_ksa_surface_aliases(FACTOR) == [FACTOR, TRUNCATED_FACTOR]


@pytest.mark.parametrize(
    ("factor_name", "competency_name", "competency_definition", "task_marker", "behavior_marker"),
    [
        (
            "승인된 변경에 대한 지식",
            "프로젝트 인적자원관리",
            "승인된 변경에 따라 인력과 역할을 조정한다.",
            "반영할 범위와 제외할 업무",
            "직접 바꾼 행동과 관찰 결과",
        ),
        (
            "과거 단계 문서에 대한 지식",
            "프로젝트 전략기획",
            "과거 단계 문서를 검토하여 현재 계획 수립에 반영한다.",
            "이어 써야 할 근거와 더 이상 따르지 않을 내용",
            "적용하거나 배제했는지의 근거",
        ),
        (
            "과거 프로젝트 교훈에 대한 지식",
            "프로젝트 이해관계자관리",
            "과거 프로젝트 교훈을 검토하여 이해관계자 대응에 반영한다.",
            "다시 써야 할 원칙과 그대로 따르지 않을 조건",
            "그 판단 때문에 직접 택한 행동과 관찰 결과",
        ),
    ],
)
def test_knowledge_task_frame_keeps_change_history_semantics_for_experience_prompts(
    factor_name: str,
    competency_name: str,
    competency_definition: str,
    task_marker: str,
    behavior_marker: str,
) -> None:
    frame = build_question_task_frame(
        evidence_row=None,
        factor_name=factor_name,
        ksa_type="지식",
        competency_name=competency_name,
        competency_definition=competency_definition,
    )

    assert frame["task_object"].endswith("확인·판단 기준")
    assert task_marker in frame["task_statement"]
    assert "맡은 역할·목표" in frame["observable_behavior"]
    assert "적용" in frame["observable_behavior"]
    assert behavior_marker in frame["observable_behavior"]
    assert frame["observable_behavior"] != "확인 자료, 판단 기준, 적용 범위, 예외와 오류 위험을 설명한다"


def test_distinct_planning_skills_keep_distinct_observable_surfaces() -> None:
    schedule, schedule_source = public_task_object(
        factor_name="일정 계획 수립",
        ksa_type="기술",
        competency_name="문서작성",
    )
    budget, budget_source = public_task_object(
        factor_name="예산 계획 수립",
        ksa_type="기술",
        competency_name="문서작성",
    )
    collection, collection_source = public_task_object(
        factor_name="정보수집 기술",
        ksa_type="기술",
        competency_name="사례관리 실행계획 수립",
    )

    assert schedule == "일정 계획 작성·검토 절차"
    assert budget == "예산 계획 작성·검토 절차"
    assert collection == "정보 수집·확인 절차"
    assert {schedule_source, budget_source, collection_source} == {"factor_repair"}
    assert len({schedule, budget, collection}) == 3


def test_common_model_path_repairs_only_candidate_visible_ksa_copy() -> None:
    row = _evidence_row()
    item = {
        "type": "토론면접",
        "competency": "프로젝트 인적자원관리",
        "ncsClCd": NCS_CODE,
        "question_focus": FACTOR,
        "ksa_refs": [FACTOR],
        "question_evidence_id": stable_ksa_evidence_id(row),
        "question": (
            "[토론과제] 프로젝트 인적자원관리에서 '승인된 변경에 대한'을 어떤 범위에 적용할지 두 입장이 충돌합니다. "
            "각 입장의 근거와 위험을 검토하고 공동 합의안을 도출해 주세요."
        ),
        "follow_ups": [f"'{FACTOR}' 쟁점에서 먼저 확인할 문서는 무엇입니까?"],
        "evaluation_points": [f"'{FACTOR}'을 활용한 판단 근거"],
    }

    repaired = _attach_candidate_surface_evidence(
        item,
        ncs_ksa=[row],
        ncs_matches=[
            {
                "ncsClCd": NCS_CODE,
                "compeUnitName": "프로젝트 인적자원관리",
            }
        ],
    )

    visible = "\n".join(
        [
            repaired["question"],
            *repaired["follow_ups"],
            *repaired["evaluation_points"],
        ]
    )
    assert FACTOR not in visible
    assert TRUNCATED_FACTOR not in visible
    assert PUBLIC_FOCUS in visible
    assert repaired["question_focus"] == FACTOR
    assert repaired["ksa_refs"] == [FACTOR]
    assert repaired["question_evidence_id"] == stable_ksa_evidence_id(row)
    assert repaired["provider_question_evidence_id"] == stable_ksa_evidence_id(row)
    assert repaired["question_evidence_assignment_valid"] is True
    assert repaired["question_evidence_assignment_reason"] == "exact_provider_evidence_id"
    assert repaired["candidate_surface_repairs"] == [
        "evaluation_points",
        "follow_ups",
        "question",
    ]


@pytest.mark.parametrize(
    ("provider_evidence_id", "reason"),
    [
        ("", "missing_provider_evidence_id"),
        ("ksa_111111111111111111111111", "provider_evidence_id_mismatch"),
    ],
)
def test_common_model_path_does_not_launder_missing_or_forged_evidence_id(
    provider_evidence_id: str,
    reason: str,
) -> None:
    row = _evidence_row()
    item = {
        "type": "상황면접",
        "competency": "프로젝트 인적자원관리",
        "ncsClCd": NCS_CODE,
        "question_focus": FACTOR,
        "ksa_refs": [FACTOR],
        "question_evidence_id": provider_evidence_id,
        "question": f"마감 직전 {FACTOR}이 필요한 상황에서 무엇을 판단하시겠습니까?",
        "follow_ups": ["방금 선택한 근거는 무엇입니까?"],
        "evaluation_points": ["판단 근거"],
    }

    repaired = _attach_candidate_surface_evidence(
        item,
        ncs_ksa=[row],
        ncs_matches=[
            {
                "ncsClCd": NCS_CODE,
                "compeUnitName": "프로젝트 인적자원관리",
            }
        ],
    )

    assert FACTOR not in repaired["question"]
    assert repaired["provider_question_evidence_id"] == provider_evidence_id
    assert repaired["question_evidence_id"] == provider_evidence_id
    assert repaired["question_task_frame"]["evidence_id"] == stable_ksa_evidence_id(row)
    assert repaired["question_evidence_assignment_valid"] is False
    assert repaired["question_evidence_assignment_reason"] == reason


def test_debate_fallback_separates_task_from_time_and_submission_conditions() -> None:
    row = _evidence_row()
    question = _build_ncs_code_template_fallback_question(
        unit={
            "ncsClCd": NCS_CODE,
            "compeUnitName": "프로젝트 인적자원관리",
            "ncsSubdCdnm": "프로젝트관리",
        },
        comp_name="프로젝트 인적자원관리",
        ncs_code=NCS_CODE,
        ksa_terms=[FACTOR],
        evidence_terms=[FACTOR],
        evidence_rows=[row],
        index=5,
    )

    visible = "\n".join(
        [question["question"], *question["follow_ups"], *question["evaluation_points"]]
    )
    assert FACTOR not in visible
    assert TRUNCATED_FACTOR not in visible
    assert PUBLIC_FOCUS in visible
    assert "토론시간 20분" not in question["question"]
    assert "1분 입장발표" not in question["question"]
    assert question["task_conditions"]["time_plan"] == [
        {"phase": "개별 입장발표", "minutes": 1},
        {"phase": "전체 토론", "minutes": 20},
    ]
    assert any(
        "공통안의 적용 범위·예외·검증·실행 책임 또는 미합의 이송 기준" in output
        for output in question["task_conditions"]["required_outputs"]
    )


def test_runtime_adjustment_keeps_evidence_link_and_removes_dangling_copy() -> None:
    row = _evidence_row()
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {
                        "detail": "프로젝트관리",
                        "enabled": True,
                        "main_count": 1,
                        "follow_up_count": 3,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        ["프로젝트관리"],
    )
    strategy = {
        "interview_questions": [
            {
                "question": (
                    "[토론과제] 프로젝트 인적자원관리 업무에서 '승인된 변경에 대한'을 어떤 범위와 예외 기준으로 "
                    "적용할지를 두고 문서·절차 기준을 강화하는 입장과 처리 속도·협업 효율을 우선하는 입장이 충돌합니다. "
                    "각 입장의 근거와 위험을 검토하고 실행 가능한 공동 합의안을 도출해 주세요."
                ),
                "follow_ups": [
                    f"'{FACTOR}' 쟁점에서 먼저 확인할 문서와 사실은 무엇입니까?",
                    "상대 입장에서 수용할 부분과 수용하기 어려운 부분을 어떤 기준으로 구분하겠습니까?",
                    "합의안의 적용 범위·예외·검증 기준과 실행 책임을 어떻게 정하겠습니까?",
                ],
                "evaluation_points": [
                    "확인 근거의 적절성",
                    "상대 근거의 타당성 검토",
                    "쟁점 조정",
                    "실행 가능한 공동 합의",
                ],
            }
        ]
    }

    out = _adjust_generated_questions(
        strategy,
        plan,
        ["토론면접"],
        ncs_matches=[
            {
                "ncsClCd": NCS_CODE,
                "compeUnitName": "프로젝트 인적자원관리",
                "ncsSubdCdnm": "프로젝트관리",
                "matchedDetailName": "프로젝트관리",
            }
        ],
        ncs_ksa=[row],
    )
    out = _attach_ksa_evidence_to_strategy(out, [row])
    question = out["interview_questions"][0]
    visible = "\n".join(
        [question["question"], *question["follow_ups"], *question["evaluation_points"]]
    )

    assert FACTOR not in visible
    assert TRUNCATED_FACTOR not in visible
    assert PUBLIC_FOCUS in visible
    assert question["question_evidence_id"] == stable_ksa_evidence_id(row)
    assert question["question_source"] == "template_fallback"
    assert question["model_question_preserved"] is False
    assert "debate_outcome_flexibility" in question["model_replacement_reasons"]
    report = out["question_quality_report"]
    assert report["passed"] is False
    assert report["summary"]["ready_count"] == 0
    assert report["summary"]["needs_review_count"] == 1
    assert report["items"][0]["ready"] is False
    assert report["items"][0]["issues"] == ["field_realism"]
    assert report["items"][0]["check_statuses"]["field_realism"] == "fail"


def test_task_conditions_use_official_type_for_an_opaque_factor_name() -> None:
    conditions = _task_conditions_for_method(
        method="토론면접",
        subject="메뉴 구성",
        focus="곁들임 메뉴 구성",
        detail="한식조리",
        focus_type="지식",
    )

    assert conditions["required_outputs"] == [
        "초기 입장과 확인 근거",
        "반대 입장의 수용·불수용 기준",
        "공통안의 적용 범위·예외·검증·실행 책임 또는 미합의 이송 기준",
    ]


def test_debate_main_task_operationalizes_the_final_agreement_by_ksa_type() -> None:
    row = _evidence_row()
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "프로젝트관리", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["프로젝트관리"],
    )
    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["토론면접"],
        ncs_matches=[
            {
                "ncsClCd": NCS_CODE,
                "compeUnitName": "프로젝트 인적자원관리",
                "ncsSubdCdnm": "프로젝트관리",
                "matchedDetailName": "프로젝트관리",
            }
        ],
        ncs_ksa=[row],
    )

    question = out["interview_questions"][0]["question"]
    assert "적용 범위·예외·검증·실행 책임" in question
    assert "합의가 어렵다면 미합의 쟁점과 결정권자 이송 기준" in question
    assert "토론시간 20분" not in question
