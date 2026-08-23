import json

import pytest
from fastapi import HTTPException

from app.services.jd_strategy import _build_ncs_code_template_fallback_question
from app.services.question_evaluation_alignment import (
    evaluate_evaluation_elicitation_alignment,
)
from app.services.question_surface import (
    normalize_ksa_type,
    public_task_object,
    replace_official_ksa_surface,
    stable_ksa_evidence_id,
)
from app.main import (
    _adjust_generated_questions,
    _attach_ksa_evidence_to_strategy,
    _attach_question_quality_report,
    _behavior_anchored_evaluation,
    _behavior_anchors_ok,
    _clamp_runtime_knobs,
    _complete_experience_question_star,
    _debate_option_defensibility_ok,
    _debate_outcome_flexibility_ok,
    _decision_dilemma_quality_ok,
    _domain_context_pack,
    _ensure_question_plan_unit_coverage,
    _experience_star_followups,
    _group_interview_questions_for_response,
    _question_intent_key,
    _method_evaluation_points,
    _method_shape_ok,
    _normalize_ksa_type,
    _natural_question_wording_ok,
    _focus_scenario_coherence_ok,
    _official_sample_format_ok,
    _operational_focus_label,
    _followups_for_method,
    _parse_interview_methods,
    _parse_question_plan_json,
    _question_for_method,
    _question_variation_constraint,
    _select_ksa_focus_for_method,
    _task_conditions_for_method,
)


def test_server_completed_experience_question_keeps_star_rubric_aligned() -> None:
    surface = "예산관리규정 적용·판단 기준"
    points = _method_evaluation_points(
        "경험면접",
        ["예산관리규정에 대한 지식"],
        "지식",
        surface_focus=surface,
    )
    item = {
        "type": "경험면접",
        "question": (
            "예산 편성 중 과거 실적의 오류를 바로잡은 경험을 말씀해 주세요. "
            "당시 맡은 역할과 목표, 예산관리규정 적용·판단 기준에 따라 확인한 규정·문서·자료와 "
            "적용 범위의 판단 근거, 그에 따라 직접 취한 행동, 문서·수치·기록·피드백으로 "
            "확인한 결과를 구체적으로 설명해 주세요."
        ),
        "follow_ups": _experience_star_followups(
            focus_type="지식",
            surface_focus=surface,
            count=3,
        ),
        "evaluation_points": points,
        "assessment_guide": _behavior_anchored_evaluation(
            "경험면접",
            "예산관리규정에 대한 지식",
            points,
            "지식",
            surface_focus=surface,
        ),
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert result["decision"] == "pass", json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("focus_type", "surface", "raw_question"),
    [
        (
            "기술",
            "이해관계자 요구 사항 확인 절차",
            "이해관계자 요구 사항을 확인해 누락을 바로잡은 경험을 말씀해 주세요.",
        ),
        (
            "태도",
            "계량화된 자료에 대한 정확성 준수 행동 기준",
            "마감 압박 속에서도 자료 오류를 바로잡은 경험을 말씀해 주세요.",
        ),
    ],
)
def test_server_completed_experience_skill_and_attitude_rubrics_align(
    focus_type: str,
    surface: str,
    raw_question: str,
) -> None:
    question, changed = _complete_experience_question_star(
        raw_question,
        focus_type=focus_type,
        focus_surface=surface,
    )
    points = _method_evaluation_points(
        "경험면접",
        [surface],
        focus_type,
        surface_focus=surface,
    )
    item = {
        "type": "경험면접",
        "question": question,
        "follow_ups": _experience_star_followups(
            focus_type=focus_type,
            surface_focus=surface,
            count=3,
        ),
        "evaluation_points": points,
        "assessment_guide": _behavior_anchored_evaluation(
            "경험면접",
            surface,
            points,
            focus_type,
            surface_focus=surface,
        ),
    }

    result = evaluate_evaluation_elicitation_alignment(item)

    assert changed is True
    assert result["decision"] == "pass", json.dumps(result, ensure_ascii=False)


def test_generic_department_assignment_does_not_satisfy_candidate_task_role() -> None:
    completed, changed = _complete_experience_question_star(
        "담당 부서의 요청이 충돌했던 경험을 말씀해 주세요.",
        focus_type="태도",
        focus_surface="이해관계자 의견 조정 행동 기준",
    )

    assert changed is True
    assert "당시 맡은 역할과 목표" in completed


def test_question_plan_unit_coverage_keeps_one_unit_per_detail() -> None:
    plan = {
        "selected_terms": ["프로젝트관리", "산학협력관리", "경영기획", "예산"],
    }
    ranked = [
        {
            "ncsClCd": "P1",
            "compeUnitName": "프로젝트 전략기획",
            "matchedDetailName": "프로젝트관리",
        }
    ]
    candidates = [
        *ranked,
        {"ncsClCd": "A1", "compeUnitName": "산학협력 사업기획", "matchedDetailName": "산학협력관리"},
        {"ncsClCd": "B1", "compeUnitName": "경영계획 수립", "matchedDetailName": "경영기획"},
        {"ncsClCd": "C1", "compeUnitName": "예산 편성", "matchedDetailName": "예산"},
    ]

    result = _ensure_question_plan_unit_coverage(plan, ranked, candidates)

    assert [row["ncsClCd"] for row in result] == ["P1", "A1", "B1", "C1"]


def test_question_plan_splits_multiple_detail_labels_from_one_value() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {
                        "detail": "인사, 프로젝트관리",
                        "enabled": True,
                        "main_count": 1,
                        "follow_up_count": 3,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        ["인사, 프로젝트관리"],
    )

    assert plan["selected_terms"] == ["인사", "프로젝트관리"]
    assert plan["total_main_count"] == 2
    assert [item["detail"] for item in plan["question_sequence"]] == ["인사", "프로젝트관리"]


def test_question_plan_preserves_up_to_fifty_main_questions() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {
                        "detail": f"세분류-{index}",
                        "enabled": True,
                        "main_count": 10,
                        "follow_up_count": 3,
                    }
                    for index in range(1, 7)
                ]
            },
            ensure_ascii=False,
        ),
        [],
    )

    assert plan["total_main_count"] == 50
    assert len(plan["question_sequence"]) == 50
    assert plan["question_sequence"][-1]["detail"] == "세분류-5"


@pytest.mark.parametrize("provider_source", ["openai_api", "codex_cli"])
def test_adjust_questions_preserves_exact_provider_evidence_when_factor_names_repeat(
    provider_source: str,
) -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사업기획", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사업기획"],
    )
    first_evidence = {
        "ncsClCd": "0201010101_24v1",
        "compeUnitName": "사업환경 분석",
        "elementName": "외부환경 분석",
        "factorName": "관련 법규 및 규정에 대한 지식",
        "factorNo": "K-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    selected_evidence = {
        "ncsClCd": "0201010101_24v1",
        "compeUnitName": "사업환경 분석",
        "elementName": "사업범위 확정",
        "factorName": "관련 법규 및 규정에 대한 지식",
        "factorNo": "K-02",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    selected_id = stable_ksa_evidence_id(selected_evidence)

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "type": "경험면접",
                    "question_source": provider_source,
                    "competency": "사업환경 분석",
                    "ncsClCd": "0201010101_24v1",
                    "question_evidence_id": selected_id,
                    "question": (
                        "사업 기획 과정에서 법령 해석이 엇갈려 승인 일정과 범위를 함께 지키기 "
                        "어려웠던 경험을 말씀해 주십시오. 당시 확인한 자료, 본인의 판단과 행동, "
                        "작성한 문서와 결과를 구체적으로 설명해 주십시오."
                    ),
                    "follow_ups": [
                        "방금 말씀하신 판단에서 직접 대조한 규정과 문서의 조항은 무엇이었습니까?",
                        "그 선택과 반대로 처리했다면 일정과 책임에는 어떤 위험이 생겼겠습니까?",
                        "말씀하신 조치 후 승인 결과와 문서 변경 내역을 어떻게 확인했습니까?",
                    ],
                    "evaluation_points": [
                        "확인 자료의 구체성",
                        "규정 적용 판단의 타당성",
                        "본인 행동과 역할",
                        "산출물 및 결과 검증",
                    ],
                }
            ]
        },
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": "0201010101_24v1",
                "compeUnitName": "사업환경 분석",
                "ncsSclasCdnm": "경영기획",
                "ncsSubdCdnm": "기획사무",
                "matchedDetailName": "사업기획",
            }
        ],
        ncs_ksa=[first_evidence, selected_evidence],
    )

    adjusted = out["interview_questions"][0]
    assert adjusted["question_evidence_id"] == selected_id
    assert adjusted["question_task_frame"]["evidence_id"] == selected_id
    assert adjusted["question_evidence_id"] != stable_ksa_evidence_id(first_evidence)

    enriched = _attach_ksa_evidence_to_strategy(out, [first_evidence, selected_evidence])
    enriched_question = enriched["interview_questions"][0]
    assert enriched_question["question_evidence_id"] == selected_id
    assert enriched_question["ksa_evidence"][0]["evidence_id"] == selected_id


def test_oda_development_strategy_does_not_select_power_plant_context() -> None:
    context = _domain_context_pack(
        detail="공적개발원조사업관리",
        subject="공적개발원조사업 개발전략수립",
        focus="자국의 대외정책, 공적개발원조정책, 국별협력전략",
        comp_def="개발협력 정책에 따라 국별 전략을 수립한다.",
    )

    assert "국가협력전략" in context["evidence"]
    assert "협력국" in context["situation"]
    serialized = json.dumps(context, ensure_ascii=False)
    assert "설비" not in serialized
    assert "작업허가서" not in serialized


@pytest.mark.parametrize(
    "method",
    ["상황면접", "발표면접", "토론면접", "인바스켓면접", "창의적 문제해결력면접"],
)
def test_case_based_methods_publish_the_same_variation_as_a_source_card(method: str) -> None:
    variation_index = 5
    variation = _question_variation_constraint(variation_index)
    question = _question_for_method(
        method=method,
        subject="프로젝트 인적자원관리",
        focus="승인된 변경에 대한 지식",
        detail="프로젝트관리",
        comp_def="승인된 변경에 따라 인력과 역할을 조정한다.",
        focus_type="지식",
        variation_index=variation_index,
    )
    conditions = _task_conditions_for_method(
        method=method,
        subject="프로젝트 인적자원관리",
        focus="승인된 변경에 대한 지식",
        detail="프로젝트관리",
        comp_def="승인된 변경에 따라 인력과 역할을 조정한다.",
        focus_type="지식",
        variation_index=variation_index,
    )

    assert variation
    assert variation in "\n".join([question, *conditions["case_facts"]])
    assert f"추가 제약: {variation}" in conditions["case_facts"]
    assert {
        "source": "추가 제약 카드",
        "field": "운영 제약",
        "value": variation,
    } in conditions["case_materials"]
    assert "추가 제약 카드" in conditions["provided_materials"]


@pytest.mark.parametrize("variation_index", [2, 37, 143, 999])
def test_experience_variation_uses_one_natural_scenario_without_prompt_bloat(
    variation_index: int,
) -> None:
    focus = "기계형식 인증과 관련된 업무에 관한 탐색적 의지"
    focus_surface, _ = public_task_object(factor_name=focus, ksa_type="태도")
    question = _question_for_method(
        method="경험면접",
        subject="기계형식인증검토",
        focus=focus,
        detail="기계형식인증",
        comp_def="기계형식 인증 요건을 검토한다.",
        focus_type="태도",
        variation_index=variation_index,
    )
    follow_ups = _followups_for_method(
        method="경험면접",
        subject="기계형식인증검토",
        focus=focus,
        count=3,
        focus_type="태도",
    )

    assert len(question) <= 360
    assert "조건이고" not in question
    assert "조건이며" not in question
    assert _natural_question_wording_ok(
        {
            "type": "경험면접",
            "question_focus": focus,
            "question_focus_surface": focus_surface,
            "question_focus_type": "태도",
        },
        question,
        follow_ups,
    )


def test_debate_quality_requires_event_choice_and_multiple_consequence_axes() -> None:
    vague = (
        "[토론과제] 문서관리 업무에서 기준 강화 입장과 효율 우선 입장이 충돌합니다. "
        "각 입장의 근거를 검토하고 합의안을 제시해 주세요."
    )
    concrete = (
        "[토론과제] 발주부서의 범위 확대 요청은 승인 대기 중입니다. 승인 전 착수 시 비용 정산과 "
        "책임 근거가 부족하고 승인 후 착수 시 검수 일정이 지연됩니다. 미승인 범위에는 착수하지 말자는 "
        "입장과 저위험 범위에 한해 조건부 착수하자는 입장이 충돌합니다. 공동 합의안을 도출해 주세요."
    )

    assert _decision_dilemma_quality_ok("토론면접", vague) is False
    assert _decision_dilemma_quality_ok("토론면접", concrete) is True
    assert _decision_dilemma_quality_ok("경험면접", vague) is True


@pytest.mark.parametrize(
    "question",
    [
        (
            "[토론과제] 공동 연구사업 착수일이 임박했지만 관계기관은 일정 준수를 위해 "
            "핵심 자료만 먼저 확정하자는 입장이고, 연구원은 검증이 끝난 자료만 접수하자는 "
            "입장입니다. 선제출 범위와 보완 조건을 합의해 주세요."
        ),
        (
            "[토론과제] 신규 교육사업 평가에서 참여 인원은 목표를 달성했지만 만족도는 "
            "낮고, 사업부서는 예산 확보를 위해 등급을 유지하자는 입장이고 평가부서는 "
            "확인된 수치에 따라 낮추자는 입장입니다. 공동 평가 기준을 결정해 주세요."
        ),
    ],
)
def test_debate_dilemma_accepts_natural_decision_and_consequence_language(
    question: str,
) -> None:
    assert _decision_dilemma_quality_ok("토론면접", question) is True


def test_project_hr_debate_uses_approval_accountability_and_schedule_tradeoff() -> None:
    question = _question_for_method(
        method="토론면접",
        subject="프로젝트 인적자원관리",
        focus="승인된 변경에 대한 지식",
        detail="프로젝트관리",
        comp_def="승인된 변경에 따라 인력과 역할을 조정한다.",
        focus_type="지식",
    )

    assert "승인 결정은 D+3" in question
    assert "검수 준비 마감은 D+5" in question
    assert "하루 2시간을 사전 분석에 배정" in question
    assert "변경 실행·확정·비용 집행은 보류" in question
    assert "사전 분석의 착수 해당 여부는 불명확" in question
    assert "합의가 어렵다면 미합의 쟁점과 결정권자 이송 기준" in question
    assert "승인된 변경에 대한" not in question
    assert _decision_dilemma_quality_ok("토론면접", question) is True
    assert _debate_option_defensibility_ok("토론면접", question) is True
    assert _debate_outcome_flexibility_ok("토론면접", question) is True


def test_debate_gate_rejects_unauthorized_shortcut_and_forced_consensus() -> None:
    question = (
        "[토론과제] 변경 승인이 대기 중입니다. 승인 전 착수를 금지하자는 입장과 "
        "저위험 업무는 조건부 선착수한 뒤 사후 승인받자는 입장이 충돌합니다. "
        "각 입장을 검토하고 반드시 공동 합의안을 도출해 주세요."
    )

    assert _debate_option_defensibility_ok("토론면접", question) is False
    assert _debate_outcome_flexibility_ok("토론면접", question) is False


def test_focus_overlay_keeps_one_opposing_policy_pair() -> None:
    context = _domain_context_pack(
        detail="사무행정",
        subject="문서작성",
        focus="문서 요구사항 파악",
        comp_def="요청사항을 확인해 문서를 작성한다.",
    )

    assert context["debate"].count("입장") == 2
    assert "문서 정확성" in context["debate"]
    assert "문서·절차 기준" not in context["debate"]


@pytest.mark.parametrize(
    "bad_question",
    [
        "승인이 대기 중이고가 발생한 직무 경험을 말씀해 주세요.",
        "자료 오류 상황이 동시에 발생한 상황입니다.",
        "해당 직무에서 입사예정자의 조직적응을 적극적으로 도와주고자 관련 행동 기준에 따라 어떤 판단과 행동을 했는지 말씀해 주세요.",
    ],
)
def test_natural_wording_gate_rejects_scenario_assembly_artifacts(bad_question: str) -> None:
    assert _natural_question_wording_ok({}, bad_question, []) is False


def test_specific_definition_domain_beats_generic_title_domain() -> None:
    context = _domain_context_pack(
        detail="사무행정",
        subject="사업관리",
        focus="정책 검토 지식",
        comp_def="국별협력 전략에 따라 개발원조 사업을 기획하고 조정한다.",
    )

    assert "국가협력전략" in context["evidence"]
    assert "결재 문서" not in context["evidence"]


@pytest.mark.parametrize(
    ("value", "factor", "expected"),
    [
        ("", "분류 종류 구분", "지식"),
        ("", "시스템 활용 능력", "기술"),
        ("", "업무 적극성", "태도"),
        ("S", "종류 구분", "기술"),
    ],
)
def test_main_and_surface_use_one_ksa_type_classifier(
    value: str,
    factor: str,
    expected: str,
) -> None:
    assert normalize_ksa_type(value, factor) == expected
    assert _normalize_ksa_type(value, factor) == expected


@pytest.mark.parametrize("index", range(7))
def test_ncs_code_fallback_templates_require_human_review(index: int) -> None:
    code = "0202030201_25v3"
    unit = {
        "ncsClCd": code,
        "compeUnitName": "문서작성",
        "ncsSubdCdnm": "사무행정",
    }
    factors = ["문서 요구사항 파악", "자료 오류 점검"]
    question = _build_ncs_code_template_fallback_question(
        unit=unit,
        comp_name="문서작성",
        ncs_code=code,
        ksa_terms=factors,
        evidence_terms=factors,
        index=index,
    )
    strategy = {
        "interview_questions": [question],
        "question_plan_used": {"total_main_count": 1},
    }
    evidence = [
        {
            "ncsClCd": code,
            "compeUnitName": "문서작성",
            "factorName": factor,
            "factorSource": "ncs-mcp",
            "ksaStatus": "official",
        }
        for factor in factors
    ]

    out = _attach_ksa_evidence_to_strategy(strategy, evidence)
    report_item = out["question_quality_report"]["items"][0]

    assert report_item["ready"] is False
    assert "field_realism" in report_item["issues"]
    # Provider-free fallback questions intentionally stay generic and may
    # trigger additional review gates (method shape, context, or KSA task
    # measurement) after taxonomy labels are hidden from the candidate view.
    assert report_item["issues"]
    assert "raw_deterministic_provenance" in report_item["realism_issue_codes"]


def test_fallback_template_does_not_expose_malformed_attitude_surface() -> None:
    code = "0202030201_25v3"
    factor = "입사예정자의 조직적응을 적극적으로 도와주고자 하는 태도"
    question = _build_ncs_code_template_fallback_question(
        unit={
            "ncsClCd": code,
            "compeUnitName": "조직문화 운영",
            "ncsSubdCdnm": "인사",
        },
        comp_name="조직문화 운영",
        ncs_code=code,
        ksa_terms=[factor],
        evidence_terms=[factor],
        evidence_rows=[
            {
                "ncsClCd": code,
                "factorName": factor,
                "ksaTypeName": "태도",
                "elementName": "조직문화 지원하기",
            }
        ],
        index=0,
        method_override="경험면접",
    )

    visible = "\n".join([question["question"], *question["follow_ups"]])
    assert "조직문화 운영에서" in question["question"]
    assert "도와주고자 관련 행동 기준" not in visible
    assert "입사예정자의 조직적응 지원 행동 기준" in visible


def test_runtime_knobs_allow_default_seven_ksa_units() -> None:
    _, ksa_units, _ = _clamp_runtime_knobs(None, None, None)

    assert ksa_units == 3


def test_adjust_questions_enforces_selected_method_and_exact_counts() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {"detail": "총무", "enabled": True, "main_count": 1, "follow_up_count": 5},
                    {"detail": "인사", "enabled": True, "main_count": 1, "follow_up_count": 2},
                    {"detail": "경영기획", "enabled": False, "main_count": 3, "follow_up_count": 3},
                ]
            },
            ensure_ascii=False,
        ),
        ["총무", "인사", "경영기획"],
    )
    methods = _parse_interview_methods(json.dumps(["발표면접"], ensure_ascii=False))
    strategy = {
        "interview_questions": [
            {
                "type": "경험면접",
                "competency": "원본 능력단위",
                "question": "기존 경험을 말씀해 주세요.",
                "follow_ups": ["기존 꼬리"],
            }
        ]
    }
    ncs_matches = [
        {
            "ncsClCd": "0202010102_25v3",
            "compeUnitName": "행사 운영기획",
            "ncsSclasCdnm": "총무",
            "ncsSubdCdnm": "총무·인사",
            "compeUnitDef": "행사 운영을 기획하고 실행하는 능력이다.",
        },
        {
            "ncsClCd": "0202020101_25v3",
            "compeUnitName": "인사기획",
            "ncsSclasCdnm": "인사",
            "ncsSubdCdnm": "총무·인사",
            "compeUnitDef": "인사제도를 기획하는 능력이다.",
        },
    ]
    ncs_ksa = [
        {"ncsClCd": "0202010102_25v3", "factorName": "행사 운영계획 수립"},
        {"ncsClCd": "0202020101_25v3", "factorName": "인력운영 계획 수립"},
    ]

    out = _adjust_generated_questions(
        strategy,
        plan,
        methods,
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )

    questions = out["interview_questions"]
    assert len(questions) == 2
    assert [q["ncs_detail"] for q in questions] == ["총무", "인사"]
    assert [q["competency"] for q in questions] == ["행사 운영기획", "인사기획"]
    assert all(q["type"] == "발표면접" for q in questions)
    assert all("[발표과제]" in q["question"] for q in questions)
    assert "기존 경험" not in questions[0]["question"]
    assert len(questions[0]["follow_ups"]) == 5
    assert len(questions[1]["follow_ups"]) == 2
    assert out["question_customization_policy"] == "model_preserve_with_guidebook_template_fallback_followup_gate"


def test_adjust_questions_prioritizes_exact_sub_detail_over_shared_small_category() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {"detail": "하수처리시설운영관리", "enabled": True, "main_count": 1, "follow_up_count": 3},
                    {"detail": "폐수처리시설운영관리", "enabled": True, "main_count": 1, "follow_up_count": 3},
                ]
            },
            ensure_ascii=False,
        ),
        ["하수처리시설운영관리", "폐수처리시설운영관리"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["직무지식면접"],
        ncs_matches=[
            {
                "ncsClCd": "1401030101_25v3",
                "compeUnitName": "하수처리 운영",
                "ncsSclasCdnm": "수질관리",
                "ncsSubdCdnm": "하수처리시설운영관리",
                "matched_keywords": ["처리시설"],
            },
            {
                "ncsClCd": "1401030201_25v3",
                "compeUnitName": "폐수처리 운영",
                "ncsSclasCdnm": "수질관리",
                "ncsSubdCdnm": "폐수처리시설운영관리",
                "matched_keywords": ["처리시설"],
            },
        ],
        ncs_ksa=[
            {"ncsClCd": "1401030101_25v3", "factorName": "방류수 수질 기준 확인"},
            {"ncsClCd": "1401030201_25v3", "factorName": "폐수 유입 부하량 산정"},
        ],
    )

    questions = out["interview_questions"]

    assert [q["ncsClCd"] for q in questions] == ["1401030101_25v3", "1401030201_25v3"]
    assert [q["ncs_detail"] for q in questions] == ["하수처리시설운영관리", "폐수처리시설운영관리"]
    assert [q["competency"] for q in questions] == ["하수처리 운영", "폐수처리 운영"]


def test_adjust_questions_replaces_legacy_debate_main_and_moves_timing_to_conditions() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    model_question = (
        "[토론과제] 사무행정 업무에서 문서 보안 기준 준수와 신속한 자료 공유 입장이 충돌합니다. "
        "토론시간 20분 동안 1분 입장발표 후 반대 의견을 고려해 본인의 초기 입장과 최종 합의 기준을 제시해 주세요."
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": model_question,
                    "follow_ups": [
                        "문서 보안 기준에서 본인의 초기 입장을 뒷받침하는 핵심 근거는 무엇입니까?",
                        "반대 의견 중 수용할 수 있는 부분은 무엇입니까?",
                        "최종 합의안에 반드시 포함되어야 할 기준은 무엇입니까?",
                    ],
                    "evaluation_points": ["입장발표 근거", "경청과 상호작용", "갈등 조정", "최종 합의안 도출"],
                }
            ]
        },
        plan,
        ["토론면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "일반사무",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 보안 기준 확인"}],
    )

    question = out["interview_questions"][0]

    assert question["question"] != model_question
    assert question["question_source"] == "template_fallback"
    assert question["model_question_preserved"] is False
    assert "main_question_method_shape" in question["model_replacement_reasons"]
    assert question["model_question_raw"] == model_question
    assert "토론시간 20분" not in question["question"]
    assert question["task_conditions"]["time_plan"] == [
        {"phase": "개별 입장발표", "minutes": 1},
        {"phase": "전체 토론", "minutes": 20},
    ]
    assert question["type"] == "토론면접"
    assert question["ncs_detail"] == "사무행정"


@pytest.mark.parametrize(
    ("method", "question", "follow_ups", "evaluation_points"),
    [
        (
            "경험면접",
            "사무행정 문서작성 업무에서 문서 요구사항 파악을 적용해 문제를 해결한 경험을 말씀해 주세요. 당시 상황, 본인 역할, 선택한 행동, 결과와 학습을 포함해 설명해 주세요.",
            [
                "그 상황에서 문서 요구사항 중 먼저 확인한 기준은 무엇입니까?",
                "본인 역할에서 어떤 행동을 선택했고 그 이유는 무엇입니까?",
                "결과와 성과를 어떻게 확인했고 다음에는 무엇을 개선하겠습니까?",
            ],
            ["구체적 상황 설명", "본인 역할과 행동", "성과와 학습", "판단 근거"],
        ),
        (
            "상황면접",
            "사무행정 문서작성 업무 중 문서 요구사항 파악과 관련해 자료 오류와 마감 지연이 동시에 발생한 상황입니다. 어떤 판단 기준으로 위험을 통제하고 어떤 순서로 행동하시겠습니까?",
            [
                "문서작성 자료에서 먼저 확인해야 할 사실은 무엇입니까?",
                "문서 요구사항 파악을 기준으로 관련 부서에는 어떤 이유로 처리 순서를 설명하겠습니까?",
                "후속 위험을 어떻게 점검하고 예방하겠습니까?",
            ],
            ["사실 확인", "판단 기준", "행동 순서", "위험요인 인식", "이해관계자 대응"],
        ),
        (
            "발표면접",
            "[발표과제] 사무행정 문서작성 업무에서 문서 요구사항 파악 오류가 반복되는 자료가 주어졌습니다. 자료를 바탕으로 현황을 진단하고 개선 대안 2가지와 실행 계획, 성과지표를 발표한 뒤 질의응답에 답해 주세요.",
            [
                "문서 요구사항 파악 오류 진단에 활용한 핵심 근거 자료는 무엇입니까?",
                "문서작성 개선 대안 중 우선순위를 가장 높게 둔 방안과 그 이유는 무엇입니까?",
                "반대 의견이 제기되면 어떻게 답변하고 성과지표를 보완하겠습니까?",
            ],
            ["자료 분석력", "논리적 구조화", "대안의 실행가능성", "실행계획과 성과지표", "질의응답 대응"],
        ),
        (
            "토론면접",
            "[토론과제] 사무행정 문서작성 업무에서 결재 마감이 임박한 상태로 자료 누락이 발견되었습니다. 문서 요구사항 파악을 위한 보안 기준 강화 입장과 신속한 자료 공유 입장이 충돌합니다. 각 입장의 근거와 위험을 검토하고 반대 의견을 조정해 주세요. 합의할 수 있다면 공통 실행안을, 합의가 어렵다면 미합의 쟁점과 결정권자 이송 기준을 제시해 주세요. 공통안 또는 이송안에는 문서 요구사항 파악 수행 절차와 품질 검증 산출물을 포함해 주세요.",
            [
                "문서 요구사항 파악 관점에서 본인의 초기 입장을 뒷받침하는 핵심 근거는 무엇입니까?",
                "반대 의견 중 수용할 수 있는 부분은 무엇입니까?",
                "공통안 또는 이송안에 포함할 조정 기준은 무엇입니까?",
            ],
            ["입장발표 근거", "반대 의견 경청", "갈등 조정", "최종 합의안 도출"],
        ),
        (
            "인바스켓면접",
            "[인바스켓과제] 사무행정 문서작성 요청, 자료 오류 정정 문서, 상급자 보고 요청이 동시에 들어왔습니다. 문서 요구사항 파악을 실제로 수행해 우선순위와 보고, 위임, 직접처리 판단을 제시하고, 첫 조치와 기록 산출물을 포함해 주세요.",
            [
                "여러 문서와 요청을 어떤 기준으로 분류하겠습니까?",
                "문서 요구사항 파악을 기준으로 가장 먼저 처리할 문서와 보류할 요청은 무엇입니까?",
                "보고, 위임, 직접처리 중 어떤 방식을 선택하고 기록하겠습니까?",
            ],
            ["우선순위 판단", "문서·요청 분류", "보고와 위임 판단", "직접 처리 및 시간관리", "리스크 통제"],
        ),
        (
            "직무지식면접",
            "사무행정 문서작성에서 문서 요구사항 파악 결과를 산출물에 반영할 때 확인해야 할 절차와 기준을 설명하고, 예외상황에서 오류를 예방하는 직무지식 적용 방안을 제시해 주세요.",
            [
                "문서 요구사항 확인 기준이나 규정은 무엇입니까?",
                "예외상황에서는 어떤 순서로 판단하고 보완책을 세우겠습니까?",
                "최종 산출물 품질과 오류 예방은 어떻게 점검하겠습니까?",
            ],
            ["절차·기준 이해", "직무지식 적용", "산출물 품질", "예외상황 대응", "오류 예방"],
        ),
        (
            "창의적 문제해결력면접",
            "[창의적 문제해결력과제] 사무행정 문서작성 업무에서 문서 요구사항 파악 오류가 반복되는 복합 문제가 발생했습니다. 미래예측 관점에서 핵심 문제를 정의하고 원인 가설, 창의적 대안 2가지, 검증 방법, 실현가능성, 의사결정 기준, 실행계획과 성과지표를 제시해 주세요.",
            [
                "핵심 문제정의를 위해 문서 요구사항 중 먼저 확인할 기준은 무엇입니까?",
                "문서작성 오류의 원인 가설은 어떻게 세우고 검증하겠습니까?",
                "대안 중 실행 우선순위를 높게 둘 방안과 리스크 보완책은 무엇입니까?",
            ],
            ["미래예측과 문제 정의", "창의적 사고와 대안 도출", "검증 방법", "실현가능성", "의사결정과 실행계획", "리스크 보완"],
        ),
    ],
)
def test_adjust_questions_preserves_ready_model_questions_for_all_methods(
    method: str,
    question: str,
    follow_ups: list[str],
    evaluation_points: list[str],
) -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    ncs_matches = [
        {
            "ncsClCd": "0202030201_25v3",
            "compeUnitName": "문서작성",
            "ncsSclasCdnm": "일반사무",
            "ncsSubdCdnm": "사무행정",
            "matchedDetailName": "사무행정",
        }
    ]
    ncs_ksa = [
        {
            "ncsClCd": "0202030201_25v3",
            "compeUnitName": "문서작성",
            "factorName": "문서 요구사항 파악",
            "factorSource": "ncs-mcp",
            "ksaStatus": "official",
        }
    ]

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": question,
                    "follow_ups": follow_ups,
                    "evaluation_points": evaluation_points,
                }
            ]
        },
        plan,
        [method],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )
    out = _attach_ksa_evidence_to_strategy(out, ncs_ksa)
    preserved = out["interview_questions"][0]
    quality = out["question_quality_report"]["items"][0]

    assert preserved["question_focus"] == "문서 요구사항 파악"
    assert preserved["question_focus_surface"] == "문서 요구사항 확인 절차"
    assert preserved["question_evidence_id"]
    expected_public_question, _ = replace_official_ksa_surface(
        question,
        "문서 요구사항 파악",
        "문서 요구사항 확인 절차",
    )
    assert preserved["question"] == expected_public_question
    assert preserved["question_source"] == "model_main_quality_repaired_fields"
    assert preserved["model_question_preserved"] is True
    assert preserved["model_replacement_reasons"] == []
    assert "question" in preserved["candidate_surface_repairs"]
    if method in {"발표면접", "토론면접", "인바스켓면접", "창의적 문제해결력면접"}:
        assert preserved["task_conditions"]["time_plan"]
    assert preserved["type"] == method
    assert preserved["ncs_detail"] == "사무행정"
    assert quality["ready"] is True
    assert quality["issues"] == []


def test_adjust_questions_replaces_model_question_when_followups_are_generic() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    model_question = (
        "문서작성 업무에서 문서 요구사항 파악 오류와 일정 지연이 동시에 발생한 상황입니다. "
        "어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?"
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": model_question,
                    "follow_ups": [
                        "더 자세히 설명해 주세요.",
                        "그 이유를 말씀해 주세요.",
                        "마지막으로 보완할 점을 설명해 주세요.",
                    ],
                    "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
                }
            ]
        },
        plan,
        ["상황면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "일반사무",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][0]

    assert question["question_source"] == "model_main_repaired_followups"
    assert question["question"] == model_question.replace(
        "문서 요구사항 파악",
        "문서 요구사항 확인 절차",
    )
    assert question["model_question_preserved"] is True
    assert question["model_replacement_reasons"] == ["follow_up_focus_injected"]
    assert "문서 요구사항 확인 절차" in " | ".join(question["follow_ups"])


def test_adjust_questions_repairs_model_followups_by_injecting_focus() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    model_question = (
        "문서작성 업무에서 문서 요구사항 파악 오류와 일정 지연이 동시에 발생한 상황입니다. "
        "어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?"
    )
    raw_followups = [
        "우선 확인할 사실은 무엇입니까?",
        "그 판단에 따른 행동의 이유는 무엇입니까?",
        "후속점검은 어떻게 진행하겠습니까?",
    ]

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": model_question,
                    "follow_ups": raw_followups,
                    "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
                }
            ]
        },
        plan,
        ["상황면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "일반사무",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )
    out = _attach_ksa_evidence_to_strategy(
        out,
        [{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][0]
    quality = out["question_quality_report"]["items"][0]

    assert question["question_source"] == "model_main_repaired_followups"
    assert question["question"] == model_question.replace(
        "문서 요구사항 파악",
        "문서 요구사항 확인 절차",
    )
    assert question["model_question_preserved"] is True
    assert question["model_followups_raw"] == raw_followups
    assert question["model_replacement_reasons"] == ["follow_up_focus_injected"]
    assert question["follow_ups"][0] == raw_followups[0]
    assert "문서 요구사항 확인 절차와 관련해" in question["follow_ups"][1]
    assert "문서작성 상황에서" in question["follow_ups"][1]
    assert raw_followups[1] in question["follow_ups"][1]
    assert question["follow_ups"][2] == raw_followups[2]
    assert quality["ready"] is True
    assert quality["issues"] == []


def test_adjust_questions_repairs_presentation_followups_in_method_focus_slot() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "구조물해체", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["구조물해체"],
    )
    model_question = (
        "[발표과제] 구조물해체 도면파악에서 도면 숙지 의지 관련 자료가 주어졌다고 가정하고 "
        "준비시간 20분 후 현황을 진단하고 대안 2가지, 실행계획, 성과지표를 5분 발표하고 5분 질의응답 답변을 포함해 주세요."
    )
    raw_followups = [
        "진단의 근거 자료는 무엇입니까?",
        "선택한 대안의 이유는 무엇입니까?",
        "성과지표는 어떻게 설정하겠습니까?",
    ]

    out = _adjust_generated_questions(
        {"interview_questions": [{"question": model_question, "follow_ups": raw_followups}]},
        plan,
        ["발표면접"],
        ncs_matches=[
            {
                "ncsClCd": "1403020101_25v3",
                "compeUnitName": "구조물해체 도면파악",
                "ncsSclasCdnm": "구조물해체",
                "ncsSubdCdnm": "구조물해체",
                "matchedDetailName": "구조물해체",
            }
        ],
        ncs_ksa=[{"ncsClCd": "1403020101_25v3", "factorName": "도면 숙지 의지"}],
    )
    out = _attach_ksa_evidence_to_strategy(
        out,
        [{"ncsClCd": "1403020101_25v3", "factorName": "도면 숙지 의지"}],
    )

    question = out["interview_questions"][0]
    quality = out["question_quality_report"]["items"][0]

    assert question["question_source"] == "template_fallback"
    assert question["model_question_preserved"] is False
    assert "ksa_measurement_task" in question["model_replacement_reasons"]
    assert "도면 숙지 행동 기준이 드러나는 선택을 판단하기 위한" in question["question"]
    assert "상충하는 요구와 압박 속에서 선택한 행동" in question["question"]
    assert "감수할 상충비용 또는 불이익" in question["question"]
    assert any("도면 숙지 행동 기준" in follow_up for follow_up in question["follow_ups"])
    assert quality["ready"] is False
    assert quality["issues"] == ["field_realism"]
    assert quality["checks"]["ksa_measurement_task"] is True


def test_adjust_questions_injects_job_context_into_presentation_main_question() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "화물운송", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["화물운송"],
    )
    model_question = (
        "운임원가산정에 대한 분석적 태도와 관련 자료가 주어졌다고 가정하고 준비시간 20분 후 "
        "현황을 진단하고 대안 2가지, 실행계획, 성과지표를 5분 발표하고 5분 질의응답 답변을 포함해 주세요."
    )
    raw_followups = [
        "운임원가산정에 대한 분석적 태도을 발표 쟁점으로 볼 때 화물자동차운송운임산정 현황 진단의 근거자료는 무엇입니까?",
        "선택한 대안의 이유는 무엇입니까?",
        "질의응답에서 예상되는 반대 의견에 대한 대응 방안은 무엇입니까?",
    ]

    out = _adjust_generated_questions(
        {"interview_questions": [{"question": model_question, "follow_ups": raw_followups}]},
        plan,
        ["발표면접"],
        ncs_matches=[
            {
                "ncsClCd": "0901010203_23v2",
                "compeUnitName": "화물자동차운송운임산정",
                "ncsSclasCdnm": "육상운송",
                "ncsSubdCdnm": "화물운송",
                "matchedDetailName": "화물운송",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0901010203_23v2", "factorName": "운임원가산정에 대한 분석적 태도"}],
    )
    out = _attach_ksa_evidence_to_strategy(
        out,
        [{"ncsClCd": "0901010203_23v2", "factorName": "운임원가산정에 대한 분석적 태도"}],
    )

    question = out["interview_questions"][0]
    quality = out["question_quality_report"]["items"][0]

    assert question["question_source"] == "template_fallback"
    assert question["model_question_preserved"] is False
    assert "ksa_measurement_task" in question["model_replacement_reasons"]
    assert question["model_question_raw"] == model_question
    assert question["question"].startswith("[발표과제] 화물자동차운송운임산정 업무에서")
    assert "운임원가산정에 대한 분석적 행동 기준이 드러나는 선택을 판단하기 위한" in question["question"]
    assert "상충하는 요구와 압박 속에서 선택한 행동" in question["question"]
    assert "그 선택으로 감수할 상충비용 또는 불이익" in question["question"]
    assert any("운임원가산정에 대한 분석적 행동 기준" in follow_up for follow_up in question["follow_ups"])
    assert quality["ready"] is False
    assert quality["issues"] == ["field_realism"]
    assert quality["checks"]["ksa_measurement_task"] is True


def test_adjust_questions_replaces_shallow_focus_restatement_in_experience_question() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사회복지 사례관리", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사회복지 사례관리"],
    )
    model_question = (
        "사회복지사례관리 실행계획 수립에서 강점관점 개념을 적용했던 경험을 말씀해 주세요. "
        "당시 상황, 본인 역할, 선택한 행동, 결과와 학습을 포함해 설명해 주세요."
    )
    raw_followups = [
        "당시에 적용한 강점관점 개념에 대해 구체적으로 설명해 주세요.",
        "사회복지사례관리 실행계획 수립 과정에서 어려움은 무엇이었습니까?",
        "결과적으로 어떤 학습을 하셨습니까?",
    ]

    out = _adjust_generated_questions(
        {"interview_questions": [{"question": model_question, "follow_ups": raw_followups}]},
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": "0701020505_25v3",
                "compeUnitName": "사회복지사례관리 실행계획 수립",
                "ncsSclasCdnm": "사회복지 사례관리",
                "ncsSubdCdnm": "사회복지 사례관리",
                "matchedDetailName": "사회복지 사례관리",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0701020505_25v3", "factorName": "강점관점 개념"}],
    )
    out = _attach_ksa_evidence_to_strategy(
        out,
        [{"ncsClCd": "0701020505_25v3", "factorName": "강점관점 개념"}],
    )

    question = out["interview_questions"][0]
    quality = out["question_quality_report"]["items"][0]

    assert question["question_source"] == "template_fallback"
    assert question["question"] != model_question
    assert question["model_question_preserved"] is False
    assert question["model_followups_raw"] == raw_followups
    assert "ksa_measurement_task" in question["model_replacement_reasons"]
    assert "실제 판단에 사용한 장면을 골라 무엇을 확인했고 어떤 기준으로 판단" in question["question"]
    assert "실제 행동과 결과" in question["question"]
    assert any("강점관점 확인·판단 기준" in follow_up for follow_up in question["follow_ups"])
    assert quality["ready"] is False
    assert quality["issues"] == ["field_realism"]


def test_question_quality_accepts_inbasket_time_amount_followup_as_open_prompt() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "인바스켓면접",
                "competency": "화물자동차운행관리",
                    "ncsClCd": "0904010201_25v3",
                "ncs_detail": "화물운송",
                "question_focus": "화물취급지침 교육스킬",
                "question_focus_surface": "화물취급지침 교육 수행 절차",
                "ksa_refs": ["화물취급지침 교육스킬"],
                    "ksa_evidence": [
                        {
                            "ncsClCd": "0904010201_25v3",
                            "factorName": "화물취급지침 교육스킬",
                            "factorType": "기술",
                            "ksaStatus": "official",
                        }
                    ],
                    "question": (
                    "[인바스켓과제] 화물자동차운행관리 관련 여러 문서와 요청이 동시에 들어왔습니다. "
                        "화물취급지침 교육 수행 절차를 기준으로 우선순위, 보고, 위임, 직접처리 판단을 제시해 주세요."
                    ),
                "follow_ups": [
                    "화물취급지침 교육 수행 절차를 처리 기준으로 삼아 화물자동차운행관리 우선순위를 정한 이유는 무엇입니까?",
                    "각 요청 사항에 대한 보고 및 위임 방안은 어떻게 설정하실 건가요?",
                    "직접 처리할 경우 예상되는 시간 소요는 얼마입니까?",
                ],
                "evaluation_points": ["우선순위 판단", "문서·요청 분류", "보고·위임·직접처리 판단", "시간관리"],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["follow_up_quality"] is True
    assert item["ready"] is True
    assert item["issues"] == []


def test_adjust_questions_repaired_followups_fill_requested_count() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 5}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    model_question = (
        "문서작성 업무에서 문서 요구사항 파악 오류와 일정 지연이 동시에 발생한 상황입니다. "
        "어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?"
    )
    raw_followups = [
        "우선 확인할 사실은 무엇입니까?",
        "그 판단에 따른 행동의 이유는 무엇입니까?",
        "후속점검은 어떻게 진행하겠습니까?",
    ]

    out = _adjust_generated_questions(
        {"interview_questions": [{"question": model_question, "follow_ups": raw_followups}]},
        plan,
        ["상황면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "일반사무",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][0]

    assert question["question_source"] == "model_main_repaired_followups"
    assert len(question["follow_ups"]) == 5
    assert question["follow_ups"][0] == raw_followups[0]
    assert raw_followups[1] in question["follow_ups"][1]
    assert question["follow_ups"][2] == raw_followups[2]


@pytest.mark.parametrize(
    ("method", "detail", "code", "competency", "focus", "question", "raw_followups"),
    [
        (
            "경험면접",
            "사회복지사례관리",
            "0701020203_20v1",
            "사회복지사례관리 실행계획 수립",
            "강점관점 개념",
            (
                "사회복지사례관리에서 강점관점 개념을 적용했던 경험을 말씀해 주세요. "
                "당시 상황, 본인 역할, 선택한 행동, 결과와 학습을 포함해 설명해 주세요."
            ),
            [
                "당시의 구체적 상황은 어땠습니까?",
                "본인이 맡은 역할과 선택한 행동은 무엇이었나요?",
                "그 경험을 통해 어떤 교훈을 얻었나요?",
            ],
        ),
        (
            "경험면접",
            "총무",
            "0202010101_22v3",
            "사업계획수립",
            "산업동향",
            (
                "총무 업무에서 산업동향을 적용했던 경험을 말씀해 주세요. "
                "당시 상황, 본인 역할, 선택한 행동, 결과와 학습을 포함해 설명해 주세요."
            ),
            [
                "당시 어떤 산업동향을 확인했는지 설명해 주실 수 있나요?",
                "그 판단을 내린 이유는 무엇이었나요?",
                "결과를 통해 어떤 교훈을 얻으셨나요?",
            ],
        ),
        (
            "인바스켓면접",
            "화물운송",
            "0901010205_15v1",
            "화물자동차운행관리",
            "화물취급지침 교육스킬",
                (
                    "[인바스켓과제] 화물운송 관련 여러 문서와 요청이 동시에 들어왔습니다. "
                    "화물취급지침 교육스킬을 기준으로 우선순위, 보고, 위임, 직접처리 판단과 첫 조치, 기록 산출물을 제시해 주세요."
            ),
            [
                "우선 확인할 문서는 무엇인가요?",
                "우선순위를 정한 이유는 무엇인가요?",
                "첫 번째 조치는 무엇으로 하셨나요?",
            ],
        ),
        (
            "인바스켓면접",
            "화물운송",
            "0901010205_15v1",
            "화물자동차운행관리",
            "화물취급지침 교육스킬",
                (
                    "[인바스켓과제] 화물운송 관련 여러 문서와 요청이 동시에 들어왔습니다. "
                    "화물취급지침 교육스킬을 기준으로 우선순위, 보고, 위임, 직접처리 판단과 첫 조치, 기록 산출물을 제시해 주세요."
            ),
            [
                "우선 확인할 문서는 무엇입니까?",
                "그 판단 기준으로 어떠한 행동을 선택하셨습니까?",
                "결과적으로 어떤 조치를 취하였습니까?",
            ],
        ),
        (
            "창의적 문제해결력면접",
            "화물운송",
            "0901010207_15v1",
            "화물자동차운전",
            "도로교통 관련 법규",
            (
                "[창의적 문제해결력과제] 화물운송에서 도로교통 관련 법규와 관련해 복합 문제가 발생했습니다. "
                "미래예측 관점에서 핵심 문제를 정의하고 원인 가설, 창의적 대안 2가지, 검증 방법, 실현가능성, 의사결정 기준, 실행계획과 성과지표를 제시해 주세요."
            ),
            [
                "문제를 정의하기 위해 어떤 정보를 수집했나요?",
                "원인 가설을 세우신 이유는 무엇인가요?",
                "실행계획은 어떻게 수립하셨나요?",
            ],
        ),
    ],
)
def test_adjust_questions_repairs_real_alio_followup_anchor_variants(
    method: str,
    detail: str,
    code: str,
    competency: str,
    focus: str,
    question: str,
    raw_followups: list[str],
) -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )

    out = _adjust_generated_questions(
        {"interview_questions": [{"question": question, "follow_ups": raw_followups}]},
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": code,
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[{"ncsClCd": code, "factorName": focus}],
    )
    out = _attach_ksa_evidence_to_strategy(out, [{"ncsClCd": code, "factorName": focus}])

    item = out["interview_questions"][0]
    quality = out["question_quality_report"]["items"][0]

    if method == "경험면접":
        assert item["question_source"] == "template_fallback"
        assert item["model_question_preserved"] is False
        assert "ksa_measurement_task" in item["model_replacement_reasons"]
        assert item["question"] != question
        assert "실제 행동과 결과" in item["question"]
    elif method == "인바스켓면접" and focus == "화물취급지침 교육스킬":
        assert item["question_source"] == "model_main_repaired_followups", item["model_replacement_reasons"]
        assert item["model_question_preserved"] is True
        assert item["model_replacement_reasons"] == ["follow_up_focus_injected"]
        assert item["question_focus_surface"] in item["question"]
        assert focus not in item["question"]
    else:
        assert item["question_source"] in {
            "model",
            "model_main_quality_repaired_fields",
            "model_main_repaired_followups",
        }
        if item["question_source"] == "model_main_repaired_followups":
            assert item["model_replacement_reasons"] == ["follow_up_focus_injected"]
        else:
            assert item["model_replacement_reasons"] == []
    prompt_focus = item["question_focus_surface"]
    assert any(prompt_focus in follow_up for follow_up in item["follow_ups"])
    if item["question_source"] == "template_fallback":
        assert quality["ready"] is False
        assert quality["issues"] == ["field_realism"]
    else:
        assert quality["ready"] is True
        assert quality["issues"] == []
    assert quality["checks"]["ksa_measurement_task"] is True


def test_adjust_questions_replaces_model_question_when_only_followups_match_method() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": "문서작성 업무에서 중요한 점을 설명해 주세요.",
                    "follow_ups": [
                        "본인의 초기 입장을 뒷받침하는 근거는 무엇입니까?",
                        "반대 의견 중 수용할 부분은 무엇입니까?",
                        "합의안에는 어떤 기준이 포함되어야 합니까?",
                    ],
                    "evaluation_points": ["근거 제시", "경청과 상호작용", "갈등 조정", "합의안 도출"],
                }
            ]
        },
        plan,
        ["토론면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "일반사무",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 보안 기준 확인"}],
    )

    question = out["interview_questions"][0]

    assert question["question_source"] == "template_fallback"
    assert question["model_question_preserved"] is False
    assert "main_question_method_shape" in question["model_replacement_reasons"]
    assert "[토론과제]" in question["question"]


def test_adjust_questions_replaces_model_question_when_required_main_terms_are_missing() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": "문서작성 업무에서 요구사항을 파악한 경험과 당시 상황, 본인 행동을 말씀해 주세요.",
                    "follow_ups": [
                        "결과를 어떤 기준으로 확인했습니까?",
                        "어떤 점을 개선하겠습니까?",
                        "협업 과정은 어떠했습니까?",
                    ],
                    "evaluation_points": ["구체적 상황 설명", "본인 역할과 행동", "성과와 학습", "직무관련성"],
                }
            ]
        },
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "일반사무",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][0]

    assert _method_shape_ok("경험면접", question["model_question_raw"]) is False
    assert question["question_source"] == "template_fallback"
    assert question["model_question_preserved"] is False
    assert "main_question_method_shape" in question["model_replacement_reasons"]
    assert "결과" in question["question"]


def test_adjust_questions_refreshes_repeat_metadata_after_quality_field_repair() -> None:
    detail = "사무행정"
    competency = "문서작성"
    focus = "문서 요구사항 파악"
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": (
                        f"{competency} 업무에서 {focus}을 적용해 문제를 해결한 경험을 말씀해 주세요. "
                        "당시 상황, 본인 역할, 선택한 행동, 결과를 포함해 설명해 주세요."
                    ),
                    "follow_ups": [
                        f"당시 상황과 {competency}에서 본인이 맡은 역할을 구체적으로 설명해 주세요.",
                        f"{focus}을 적용하기 위해 어떤 행동을 선택했습니까?",
                        "결과와 성과를 어떤 기준으로 확인했고 무엇을 개선하시겠습니까?",
                    ],
                    "evaluation_points": ["성실성", "태도", "열정", "자신감"],
                }
            ]
        },
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": focus}],
    )
    out = _attach_ksa_evidence_to_strategy(out, [{"ncsClCd": "0202030201_25v3", "factorName": focus}])

    question = out["interview_questions"][0]
    report_item = out["question_quality_report"]["items"][0]

    assert question["question_source"] == "model_main_quality_repaired_fields"
    assert question["model_question_preserved"] is True
    assert "quality_field_repair_evaluation_points_quality" in question["quality_repair_reasons"]
    assert question["model_replacement_reasons"] == []
    assert question["quality_repaired_fields"] == [
        "assessment_guide",
        "evaluation_points",
        "follow_ups",
    ]
    assert question["question_intent"] == report_item["question_intent"]
    assert question["question_repeat_signature"] == report_item["question_repeat_signature"]
    assert question["question_repeat_duplicate"] is False
    assert report_item["question_repeat_duplicate"] is False


def test_method_templates_avoid_awkward_ksa_noun_glue() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "식음료접객", "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["식음료접객"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["발표면접", "토론면접"],
        ncs_matches=[
            {
                "ncsClCd": "1301020101_22v3",
                "compeUnitName": "식음료 영업 준비",
                "ncsSclasCdnm": "식음료서비스",
                "ncsSubdCdnm": "식음료접객",
                "matchedDetailName": "식음료접객",
            }
        ],
        ncs_ksa=[{"ncsClCd": "1301020101_22v3", "factorName": "영업장 메뉴"}],
    )

    questions = [q["question"] for q in out["interview_questions"]]

    assert "를 높이기" not in questions[0]
    assert "추진을 두고" not in questions[1]
    assert "현황 문제를 진단하고 개선안" in questions[0]
    assert "위생·품질 기준을 강화하는 입장" in questions[1]


def test_method_templates_use_domain_specific_field_scenarios() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "한식조리", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["한식조리"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["인바스켓면접"],
        ncs_matches=[
            {
                "ncsClCd": "1301010103_21v4",
                "compeUnitName": "한식 면류조리",
                "ncsSclasCdnm": "한식조리",
                "ncsSubdCdnm": "한식조리",
                "matchedDetailName": "한식조리",
            }
        ],
        ncs_ksa=[{"ncsClCd": "1301010103_21v4", "factorName": "식재료 선별능력"}],
    )

    question = out["interview_questions"][0]
    merged = "\n".join([question["question"], *question["follow_ups"]])

    assert "식재료 재고표" in question["question"]
    assert "위생점검 요청" in question["question"]
    assert "고객 불만 접수" in question["question"]
    assert "조리 일정 변경 문서" in question["question"]
    assert "관련 문서·요청이 동시에" in question["question"]
    assert "문서이 동시에" not in question["question"]
    assert question["question_focus_surface"] == "식재료 선정·확인 절차"
    assert "식재료 선정·확인 절차를 실제로 수행" in question["question"]
    assert "선별능력을 실제로 수행" not in question["question"]
    assert "'식재료 선별능력'를" not in merged
    assert "자료 오류 정정" not in question["question"]
    assert "조리장" in merged
    assert "직접 처리" in merged


def test_situational_template_uses_domain_specific_risk_event() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "환경미화", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["환경미화"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["상황면접"],
        ncs_matches=[
            {
                "ncsClCd": "1101010101_14v1",
                "compeUnitName": "청소계획수립",
                "ncsSclasCdnm": "환경미화",
                "ncsSubdCdnm": "환경미화",
                "matchedDetailName": "환경미화",
            }
        ],
        ncs_ksa=[{"ncsClCd": "1101010101_14v1", "factorName": "청소범위 설정능력"}],
    )

    question = out["interview_questions"][0]

    assert "청소 범위 변경" in question["question"]
    assert "반복 민원" in question["question"]
    assert "안전사고 위험 구역 발견" in question["question"]
    assert "안전사고 위험 구역 발견이 동시에" in question["question"]
    assert question["question_focus_surface"] == "청소범위 설정·확인 절차"
    assert "청소범위 설정·확인 절차를 실제로 수행해야 하는 가운데" in question["question"]
    assert "설정능력을 실제로 수행" not in question["question"]
    assert "'청소범위 설정능력'와" not in "\n".join([question["question"], *question["follow_ups"]])
    assert "자료 오류" not in question["question"]


@pytest.mark.parametrize(
    ("focus_type", "official_focus", "operational_focus", "public_focus"),
    [
        ("기술", "소비자 패턴분석 능력", "소비자 패턴분석", "소비자 패턴 검토 절차"),
        ("기술", "문서 오류 검증 기술", "문서 오류 검증", "문서 오류 확인·검증 절차"),
        ("지식", "문서 보안 법규 지식", "문서 보안 법규", "문서 보안 규정 적용·판단 기준"),
        ("태도", "정확성을 우선하려는 태도", "정확성을 우선하려는 태도", "정확성 우선 행동 기준"),
    ],
)
def test_official_ksa_suffix_is_removed_only_from_prompt_wording(
    focus_type: str,
    official_focus: str,
    operational_focus: str,
    public_focus: str,
) -> None:
    question = _question_for_method(
        "경험면접",
        "문서관리",
        official_focus,
        "사무행정",
        "문서 오류를 확인하고 품질을 관리한다.",
        focus_type,
    )

    assert _operational_focus_label(official_focus, focus_type) == operational_focus
    rendered_focus, _ = public_task_object(factor_name=official_focus, ksa_type=focus_type)
    assert rendered_focus == public_focus
    assert public_focus in question
    assert official_focus not in question
    assert "능력을 직접 수행" not in question
    assert "기술을 직접 수행" not in question
    assert "지식을 판단 근거" not in question


def test_skill_factor_object_uses_application_verb_for_method_or_tool() -> None:
    method_question = _question_for_method(
        "경험면접",
        "스포츠시설 경영기획",
        "수요예측 기법",
        "스포츠시설운영관리",
        "이용객 자료를 분석해 수요를 예측한다.",
        "기술",
    )
    tool_question = _question_for_method(
        "상황면접",
        "문서관리",
        "문서관리 시스템",
        "사무행정",
        "문서를 등록하고 오류를 검증한다.",
        "기술",
    )

    assert "수요 예측·검증 절차가 요구된 장면을 골라" in method_question
    assert "수요예측 기법" not in method_question
    assert "문서관리 도구 활용 절차를 실제로 수행해야" in tool_question
    assert _natural_question_wording_ok(
        {"type": "경험면접", "question_focus": "수요예측 기법", "question_focus_type": "기술"},
        method_question,
        [],
    ) is True
    assert _natural_question_wording_ok(
        {"type": "경험면접", "question_focus": "수요예측 기법", "question_focus_type": "기술"},
        method_question.replace(
            "수요 예측·검증 절차가 요구된 장면을 골라",
            "수요예측 기법을 직접 수행했는지",
        ),
        [],
    ) is False


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
def test_all_interview_methods_apply_technique_factor_as_observable_task(method: str) -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {
                        "detail": "스포츠시설운영관리",
                        "enabled": True,
                        "main_count": 1,
                        "follow_up_count": 3,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        ["스포츠시설운영관리"],
    )
    code = "1204020201_22v3"
    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": code,
                "compeUnitName": "스포츠시설 경영기획",
                "ncsSclasCdnm": "스포츠시설운영관리",
                "ncsSubdCdnm": "스포츠시설운영관리",
                "matchedDetailName": "스포츠시설운영관리",
                "compeUnitDef": "이용객 자료를 분석해 수요를 예측하고 운영계획을 수립한다.",
            }
        ],
        ncs_ksa=[
            {
                "ncsClCd": code,
                "factorName": "수요예측 기법",
                "ksaTypeName": "기술",
                "factorSource": "ncs-mcp",
                "ksaStatus": "official",
            }
        ],
    )
    out = _attach_ksa_evidence_to_strategy(
        out,
        [
            {
                "ncsClCd": code,
                "factorName": "수요예측 기법",
                "ksaTypeName": "기술",
                "factorSource": "ncs-mcp",
                "ksaStatus": "official",
            }
        ],
    )

    item = out["interview_questions"][0]
    visible = "\n".join([item["question"], *item["follow_ups"]])
    assert item["question_focus"] == "수요예측 기법"
    assert item["question_focus_surface"] == "수요 예측·검증 절차"
    assert "수요 예측·검증 절차" in visible
    assert "수요예측 기법" not in visible
    assert any(token in visible for token in ("적용", "수행"))
    assert "기법'을 직접 수행" not in visible
    assert "기법'을 실제로 수행" not in visible
    quality = out["question_quality_report"]["items"][0]
    assert quality["ready"] is False
    assert quality["issues"] == ["field_realism"]


def test_compound_skill_suffix_still_counts_as_followup_focus_context() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {
                        "detail": "사회복지 사례관리",
                        "enabled": True,
                        "main_count": 1,
                        "follow_up_count": 3,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        ["사회복지 사례관리"],
    )
    code = "0701020505_25v3"
    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": code,
                "compeUnitName": "사회복지사례관리 실행계획 수립",
                "ncsSclasCdnm": "사회복지 사례관리",
                "ncsSubdCdnm": "사회복지 사례관리",
                "matchedDetailName": "사회복지 사례관리",
            }
        ],
        ncs_ksa=[
            {
                "ncsClCd": code,
                "factorName": "갈등중재기술",
                "ksaTypeName": "기술",
            }
        ],
    )
    out = _attach_ksa_evidence_to_strategy(
        out,
        [{"ncsClCd": code, "factorName": "갈등중재기술", "ksaTypeName": "기술"}],
    )

    item = out["interview_questions"][0]
    assert "갈등중재기술" not in item["question"]
    assert item["question_focus_surface"] == "갈등 조정 절차"
    assert "갈등 조정 절차가 요구된 장면을 골라" in item["question"]
    quality = out["question_quality_report"]["items"][0]
    assert quality["ready"] is False
    assert quality["issues"] == ["field_realism"]


def test_domain_templates_choose_natural_korean_particles() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "화물운송", "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["화물운송"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["상황면접", "발표면접"],
        ncs_matches=[
            {
                "ncsClCd": "0904010101_22v3",
                "compeUnitName": "화물자동차운송운임산정",
                "ncsSclasCdnm": "화물운송",
                "ncsSubdCdnm": "화물운송",
                "matchedDetailName": "화물운송",
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "0904010101_22v3", "factorName": "운임원가산정"},
            {"ncsClCd": "0904010101_22v3", "factorName": "화주특징 분석 능력"},
        ],
    )

    merged = "\n".join(q["question"] for q in out["interview_questions"])

    assert "운임 산정 오류가 동시에" in merged
    assert "화주 요청 변경 내역" in merged
    assert "변경 요청서가 주어졌습니다" in merged
    assert "운임원가 계산·검증 절차를 실제로 수행" in merged
    assert "'운임원가산정'와" not in merged
    assert "오류이 동시에" not in merged


def test_domain_templates_prioritize_leisure_over_generic_facility() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "객실관리", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["객실관리"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["인바스켓면접"],
        ncs_matches=[
            {
                "ncsClCd": "1203020201_22v3",
                "compeUnitName": "체크 인",
                "ncsSclasCdnm": "객실관리",
                "ncsSubdCdnm": "객실관리",
                "matchedDetailName": "객실관리",
            }
        ],
        ncs_ksa=[{"ncsClCd": "1203020201_22v3", "factorName": "객실 배정 능력"}],
    )

    question = out["interview_questions"][0]["question"]

    assert "예약 현황표" in question
    assert "이용객 민원" in question
    assert "순찰 기록표" not in question


def test_document_security_focus_does_not_switch_to_physical_guard_context() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["발표면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서관리",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
                "compeUnitDef": "문서 보안 기준에 따라 기록과 결재 문서를 관리한다.",
            }
        ],
        ncs_ksa=[
            {
                "ncsClCd": "0202030201_25v3",
                "factorName": "문서 보안 법규 지식",
                "ksaTypeName": "지식",
            }
        ],
    )

    question = out["interview_questions"][0]["question"]

    assert "문서 보안 규정" in question
    assert "접근권한 목록" in question
    assert "보존·폐기 기록" in question
    assert "순찰 기록" not in question
    assert "경비 배치" not in question
    assert "출입 통제 로그" not in question


def test_cost_reduction_attitude_gets_a_real_cost_and_resource_tradeoff() -> None:
    question = _question_for_method(
        "상황면접",
        "행사지원관리",
        "원가절감 의지",
        "총무",
        "행사 운영 요구사항과 예산을 조정한다.",
        focus_type="태도",
    )

    assert "예산·자원 제약" in question
    assert "원가절감 행동 기준" in question
    assert "원가절감 의지" not in question
    assert "상충하는 요구와 압박" in question


def test_tax_law_presentation_uses_tax_application_evidence() -> None:
    question = _question_for_method(
        "발표면접",
        "부동산관리",
        "세법",
        "총무",
        "보유 부동산의 세무 자료와 신고 일정을 관리한다.",
        focus_type="지식",
    )

    assert "과세대상 자산대장" in question
    assert "세액 산정표" in question
    assert "예외·감면 검토서" in question
    assert "적용 범위·예외" in question


def test_domain_templates_prioritize_energy_and_water_operations() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {"detail": "화력발전설비운영", "enabled": True, "main_count": 1, "follow_up_count": 3},
                    {"detail": "하수처리시설운영관리", "enabled": True, "main_count": 1, "follow_up_count": 3},
                ]
            },
            ensure_ascii=False,
        ),
        ["화력발전설비운영", "하수처리시설운영관리"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["발표면접", "상황면접"],
        ncs_matches=[
            {
                "ncsClCd": "1901010301_20v3",
                "compeUnitName": "화력발전 환경설비운전",
                "ncsSclasCdnm": "화력발전설비운영",
                "ncsSubdCdnm": "화력발전설비운영",
                "matchedDetailName": "화력발전설비운영",
            },
            {
                "ncsClCd": "2301030101_20v3",
                "compeUnitName": "하수처리시설 운전",
                "ncsSclasCdnm": "하수처리시설운영관리",
                "ncsSubdCdnm": "하수처리시설운영관리",
                "matchedDetailName": "하수처리시설운영관리",
            },
        ],
        ncs_ksa=[
            {"ncsClCd": "1901010301_20v3", "factorName": "환경오염 최소화 의식"},
            {"ncsClCd": "2301030101_20v3", "factorName": "수질측정값 해석"},
        ],
    )

    questions = [q["question"] for q in out["interview_questions"]]

    assert "설비 알람 로그" in questions[0]
    assert "장애 티켓" in questions[0]
    assert "수질 경보" in questions[1]
    assert "계측기 이상값" in questions[1]


def test_domain_templates_keep_it_context_separate_from_energy_operations() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "정보기술기획", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["정보기술기획"],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["발표면접"],
        ncs_matches=[
            {
                "ncsClCd": "2001010101_22v3",
                "compeUnitName": "IT 비즈니스 환경분석",
                "ncsSclasCdnm": "정보기술기획",
                "ncsSubdCdnm": "정보기술기획",
                "matchedDetailName": "정보기술기획",
            }
        ],
        ncs_ksa=[{"ncsClCd": "2001010101_22v3", "factorName": "비용편익분석"}],
    )

    question = out["interview_questions"][0]["question"]

    assert "요구사항 정의서" in question
    assert "SLA 현황" in question
    assert "비용편익분석표" in question
    assert "운전일지" not in question
    assert "설비 알람 로그" not in question


def test_adjust_questions_uses_inbasket_template_when_selected() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["인바스켓면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "총무·인사",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][0]
    assert question["type"] == "인바스켓면접"
    assert "[인바스켓과제]" in question["question"]
    assert "제한시간" not in question["question"]
    assert question["task_conditions"]["time_plan"] == [
        {"phase": "문서 검토 및 의사결정", "minutes": 30}
    ]
    assert len(question["follow_ups"]) == 3


@pytest.mark.parametrize(
    ("method", "question_marker", "followup_marker", "evaluation_marker"),
    [
        ("경험면접", "실제 경험 한 가지를 선택해 주세요", "맡은 역할", "선택 근거와 직접 행동"),
        ("상황면접", "어떤 기준으로 판단", "먼저 확인해야 할 사실", "대안별 위험을 반영한 판단"),
        ("발표면접", "[발표과제]", "핵심 근거 자료", "대안 비교와 우선순위"),
        ("토론면접", "[토론과제]", "초기 입장", "공통안 또는 미합의 이송안의 실행 가능성"),
        ("인바스켓면접", "[인바스켓과제]", "분류할 절차", "문서별 긴급도·영향도 판단"),
        ("직무지식면접", "기준·수행 순서", "기준이나 규정", "절차·기준의 근거"),
        ("창의적 문제해결력면접", "[창의적 문제해결력과제]", "핵심 문제정의", "근거 기반 문제 정의"),
    ],
)
def test_adjust_questions_distinguishes_all_interview_methods(
    method: str,
    question_marker: str,
    followup_marker: str,
    evaluation_marker: str,
) -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 4}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    out = _adjust_generated_questions(
        {"interview_questions": [{"question": "모델 원문 질문"}]},
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "총무·인사",
                "compeUnitDef": "요구사항을 파악하여 문서를 작성하는 능력이다.",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][0]
    assert question["type"] == method
    assert question_marker in question["question"]
    assert any(followup_marker in f for f in question["follow_ups"])
    assert any(
        evaluation_marker in point
        for point in question["evaluation_points"]
    )
    assert question["ncs_detail"] == "사무행정"
    assert question["ncsClCd"] == "0202030201_25v3"
    conditions = question["task_conditions"]
    assert len(conditions["candidate_instruction"]) >= 25
    assert len(conditions["required_outputs"]) >= 2
    assert "동일한 자료" in conditions["standardization"]
    if method in {"발표면접", "토론면접", "인바스켓면접", "창의적 문제해결력면접"}:
        assert all(row["minutes"] > 0 for row in conditions["time_plan"])


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("행동형", "경험면접"),
        ("PT", "발표면접"),
        ("토의면접", "토론면접"),
        ("in-basket", "인바스켓면접"),
        ("직무지식형", "직무지식면접"),
        ("창의적 문제해결력", "창의적 문제해결력면접"),
        ("situational", "상황면접"),
    ],
)
def test_parse_interview_methods_canonicalizes_one_alias(alias: str, expected: str) -> None:
    assert _parse_interview_methods(json.dumps([alias], ensure_ascii=False)) == [expected]


def test_parse_interview_methods_rejects_multiple_values() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_interview_methods(
            json.dumps(["경험면접", "상황면접"], ensure_ascii=False)
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "interview_method_capacity_exceeded"
    assert exc_info.value.detail["max_interview_methods"] == 1


def test_parse_interview_methods_defaults_to_experience_interview() -> None:
    methods = _parse_interview_methods("")

    assert methods == ["경험면접"]


def test_adjust_questions_rotates_selected_methods_without_blind_hiring_cues() -> None:
    selected_methods = ["경험면접", "상황면접", "발표면접", "토론면접", "인바스켓면접", "직무지식면접"]
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 6, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        selected_methods,
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "총무·인사",
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "0202030201_25v3", "factorName": "단정한 용모 복장 유지"},
            {"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"},
        ],
    )

    questions = out["interview_questions"]
    assert [q["type"] for q in questions] == selected_methods
    merged_text = "\n".join(
        [q["question"] for q in questions]
        + [item for q in questions for item in q["follow_ups"]]
        + [item for q in questions for item in q["evaluation_points"]]
    )
    for banned in ["가족", "나이", "출신학교", "출신 지역", "혼인", "임신", "외모", "용모"]:
        assert banned not in merged_text


def test_question_quality_report_marks_ready_method_grounded_question() -> None:
    evidence = {
        "ncsClCd": "0202030201_25v3",
        "compeUnitName": "문서작성",
        "factorName": "문서 요구사항 파악",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    strategy = {
        "interview_questions": [
            {
                "type": "인바스켓면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question_focus": "문서 요구사항 파악",
                "question_focus_surface": "문서 요구사항 확인 절차",
                "question_focus_type": "기술",
                "question_evidence_id": stable_ksa_evidence_id(evidence),
                "question_evidence_required": True,
                "question": "[인바스켓과제] 사무행정 문서작성 관련 여러 문서와 보고 요청이 동시에 들어왔습니다. 문서 요구사항 확인 절차를 기준으로 처리 우선순위와 보고, 위임, 직접처리 판단 및 첫 조치 계획을 제시해 주세요.",
                "follow_ups": [
                    "여러 문서와 요청을 어떤 기준으로 분류하겠습니까?",
                    "가장 먼저 처리할 항목과 보류할 항목은 무엇입니까?",
                    "상급자 보고, 위임, 직접 처리 중 어떤 방식을 선택하겠습니까?",
                    "후속 확인과 기록은 어떻게 남기겠습니까?",
                ],
                "evaluation_points": ["우선순위 판단", "문서·요청 분류", "시간관리", "리스크 대응"],
                "ksa_refs": ["문서 요구사항 파악"],
                "ksa_evidence": [evidence],
            }
        ]
    }

    out = _attach_question_quality_report(strategy)
    report = out["question_quality_report"]

    assert report["passed"] is True
    assert report["summary"]["ready_count"] == 1
    assert report["items"][0]["ready"] is True
    assert report["items"][0]["checks"]["main_question_method_shape"] is True
    assert report["items"][0]["checks"]["official_sample_format"] is True
    assert report["items"][0]["issues"] == []


def test_adjust_questions_repairs_missing_task_marker_when_model_shape_is_valid() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )

    model_question = (
        "사무행정 문서작성 업무에서 문서 요구사항 파악 오류 현황을 진단하고 "
        "대안 2가지와 실행계획, 성과지표를 발표한 뒤 질의응답에 답해 주세요."
    )
    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "type": "발표면접",
                    "competency": "문서작성",
                    "ncsClCd": "0202030201_25v3",
                    "question": model_question,
                    "follow_ups": [
                        "문서 요구사항 파악을 발표에서 진단할 때 핵심 근거자료는 무엇입니까?",
                        "문서 요구사항 파악을 기준으로 대안 우선순위를 선택한 이유는 무엇입니까?",
                        "성과지표와 리스크 보완 계획은 어떻게 답변하시겠습니까?",
                    ],
                    "evaluation_points": [
                        "자료 분석력",
                        "논리적 구조화",
                        "대안의 실행가능성",
                        "실행계획 구체성",
                        "성과지표 설계",
                    ],
                }
            ]
        },
        plan,
        ["발표면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][0]

    assert question["question_source"] == "model_main_quality_repaired_fields"
    assert question["model_question_raw"] == model_question
    assert question["question"].startswith("[발표과제] ")
    assert "문서 요구사항 확인 절차" in question["question"]
    assert "문서 요구사항 파악" not in question["question"]
    assert question["model_replacement_reasons"] == []


def test_adjust_questions_repairs_inbasket_marker_when_prefix_makes_shape_valid() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사회복지 사례관리", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사회복지 사례관리"],
    )
    model_question = (
        "사회복지사례관리 실행계획 수립 관련 여러 문서와 요청이 들어왔습니다. "
        "정보수집 기술을 실제로 발휘해 우선순위, 보고, 위임, 직접처리 판단을 제시하고, "
        "각 문서에서 정보를 수집하는 첫 조치와 기록 산출물을 포함해 주세요."
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "type": "인바스켓면접",
                    "competency": "사회복지사례관리 실행계획 수립",
                    "ncsClCd": "0701020505_25v3",
                    "question": model_question,
                    "follow_ups": [
                        "정보수집 기술을 처리 기준으로 삼아 사회복지사례관리 실행계획 수립 우선순위를 정한 이유는 무엇입니까?",
                        "각 문서와 요청의 중요성을 어떻게 평가하였는지 설명해 주세요.",
                        "직접처리와 보고, 위임을 어떤 기준으로 선택하였습니까?",
                    ],
                    "evaluation_points": ["우선순위 판단", "문서·요청 분류", "보고·위임·직접처리 판단", "시간관리"],
                }
            ]
        },
        plan,
        ["인바스켓면접"],
        ncs_matches=[
            {
                "ncsClCd": "0701020505_25v3",
                "compeUnitName": "사회복지사례관리 실행계획 수립",
                "ncsSclasCdnm": "사회복지 사례관리",
                "ncsSubdCdnm": "사회복지 사례관리",
                "matchedDetailName": "사회복지 사례관리",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0701020505_25v3", "factorName": "정보수집 기술"}],
    )

    question = out["interview_questions"][0]

    assert question["question_source"] == "model_main_quality_repaired_fields"
    assert question["model_question_raw"] == model_question
    assert question["question"].startswith("[인바스켓과제] ")
    assert "제한시간" not in question["question"]
    assert "정보 수집·확인 절차" in question["question"]
    assert "정보수집 기술" not in question["question"]
    assert question["model_replacement_reasons"] == []


def test_adjust_questions_prefers_official_ksa_factor_used_by_model_over_cyclic_focus() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    model_question = (
        "문서작성 업무의 문서 요구사항 파악 단계에서 두 부서의 요구가 충돌해 초안 승인이 지연된 실제 경험 한 가지를 선택해 주세요. "
        "그 상황과 본인 역할, 실제로 한 대조·협의 행동, 수정 산출물과 승인 소요시간이라는 결과를 포함해 설명해 주세요."
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {},
                {
                    "type": "경험면접",
                    "competency": "문서작성",
                    "ncsClCd": "0202030201_25v3",
                    "question": model_question,
                    "follow_ups": [
                        "당시 상충한 요구와 본인이 맡은 역할은 무엇이었습니까?",
                        "문서 요구사항 파악 과정에서 어떤 문서를 어떤 순서로 대조했습니까?",
                        "수정 산출물과 승인 소요시간의 변화는 무엇이었습니까?",
                    ],
                    "evaluation_points": [
                        "구체적 상황 설명",
                        "본인 역할과 행동",
                        "판단 근거와 협업",
                        "결과 지표와 학습",
                    ],
                },
            ]
        },
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"},
            {"ncsClCd": "0202030201_25v3", "factorName": "문서 작성 절차"},
        ],
    )

    question = out["interview_questions"][1]

    assert question["question_focus"].startswith("문서 요구사항 파악")
    assert question["question_source"] == "model_main_repaired_followups"
    assert question["model_question_preserved"] is True
    assert question["model_replacement_reasons"] == ["follow_up_focus_injected"]


def test_adjust_questions_does_not_promote_model_ksa_refs_when_official_ksa_exists() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {},
                {
                    "type": "경험면접",
                    "competency": "문서작성",
                    "ncsClCd": "0202030201_25v3",
                    "ksa_refs": ["모델이 임의로 만든 기술"],
                },
            ]
        },
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "ncsSclasCdnm": "사무행정",
                "ncsSubdCdnm": "사무행정",
                "matchedDetailName": "사무행정",
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": "문서 요구사항 파악"}],
    )

    question = out["interview_questions"][1]
    merged = " ".join(
        [
            question["question"],
            *question["follow_ups"],
            *question["evaluation_points"],
        ]
    )

    assert question["question_focus"].startswith("문서 요구사항 파악")
    assert "모델이 임의로 만든 기술" not in merged


def test_official_sample_format_check_requires_method_specific_evaluation_points() -> None:
    assert _official_sample_format_ok(
        "발표면접",
        "[발표과제] 문서작성 업무에서 요구사항 분석 자료를 준비시간 20분 동안 검토한 뒤 개선 대안 2가지를 5분 발표하고 성과지표와 5분 질의응답 답변을 제시해 주세요.",
        [
            "발표에서 제시한 진단의 핵심 근거 자료는 무엇입니까?",
            "대안 중 우선순위를 가장 높게 둔 방안과 그 이유는 무엇입니까?",
            "실행 일정, 필요 자원, 성과지표를 어떻게 설정하겠습니까?",
        ],
        ["자료 분석력", "논리적 구조화", "대안의 실행가능성", "의사소통 명확성"],
    ) is True

    strategy = {
        "interview_questions": [
            {
                "type": "발표면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "[발표과제] 문서작성 업무에서 준비시간 20분 후 개선 대안 2가지를 5분 발표하고 성과지표와 5분 질의응답 답변을 제시해 주세요.",
                "follow_ups": [
                    "발표에서 제시한 진단의 핵심 근거 자료는 무엇입니까?",
                    "대안 중 우선순위를 가장 높게 둔 방안과 그 이유는 무엇입니까?",
                    "실행 일정, 필요 자원, 성과지표를 어떻게 설정하겠습니까?",
                ],
                "evaluation_points": ["성실성", "태도", "자신감", "표현력"],
                "ksa_refs": ["문서 요구사항 파악"],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["method_shape"] is True
    assert item["checks"]["official_sample_format"] is False
    assert item["checks"]["evaluation_points_quality"] is False
    assert "official_sample_format" in item["issues"]
    assert "evaluation_points_quality" in item["issues"]


def test_experience_official_format_requires_the_task_dimension_of_star() -> None:
    evaluation_points = [
        "구체적 상황 설명",
        "당시 역할과 목표",
        "판단 근거와 실제 행동",
        "결과 확인 근거와 학습",
    ]
    assert _official_sample_format_ok(
        "경험면접",
        "자료 불일치를 해결한 경험을 말씀해 주세요. 당시 상황에서 본인이 선택한 행동과 결과를 설명해 주세요.",
        ["행동의 근거는 무엇입니까?", "결과는 무엇입니까?", "무엇을 학습했습니까?"],
        evaluation_points,
    ) is False
    assert _official_sample_format_ok(
        "경험면접",
        "자료 불일치를 해결한 경험을 말씀해 주세요. 당시 상황과 맡은 역할, 본인이 선택한 행동과 결과를 설명해 주세요.",
        ["맡은 목표는 무엇이었습니까?", "행동의 근거는 무엇입니까?", "결과와 학습은 무엇입니까?"],
        evaluation_points,
    ) is True


def test_complete_experience_question_star_keeps_already_complete_star_question() -> None:
    question = (
        "간행물 점검 기준이 바뀐 뒤 누락이 생겼을 때 당시 담당 역할과 목표를 먼저 정리하고 "
        "본인이 직접 점검표와 배포 순서를 바꾼 경험을 말씀해 주세요. "
        "그 결과를 점검 기록과 오류 감소 수치로 어떻게 확인했는지도 설명해 주세요."
    )

    completed, changed = _complete_experience_question_star(
        question,
        focus_type="지식",
    )

    assert changed is False
    assert completed == question
    assert "이어서" not in completed


def test_complete_experience_question_star_adds_only_missing_slots_without_repeating_task_clause() -> None:
    question = (
        "간행물 관리 점검 절차·점검 절차·점검 절차가 바뀐 뒤 누락이 생겼을 때 "
        "당시 담당 역할과 목표를 말씀해 주세요."
    )

    completed, changed = _complete_experience_question_star(
        question,
        focus_type="지식",
    )

    assert changed is True
    assert "이어서 당시 맡은 역할과 목표" not in completed
    assert "확인한 규정·문서·자료와 적용 범위의 판단 근거" in completed
    assert "그에 따라 직접 취한 행동" in completed
    assert "문서·수치·기록·피드백으로 확인한 결과" in completed
    assert "점검 절차·점검 절차·점검 절차" not in completed


def test_adjust_questions_cli_experience_star_completion_does_not_repeat_existing_task_clause() -> None:
    detail = "사무행정"
    focus = "간행물 관리 점검 절차"
    evidence = {
        "ncsClCd": "0202030201_25v3",
        "compeUnitName": "문서작성",
        "factorName": focus,
        "ksaTypeName": "지식",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    plan = _parse_question_plan_json(
        json.dumps(
            {
                "items": [
                    {
                        "detail": detail,
                        "enabled": True,
                        "main_count": 1,
                        "follow_up_count": 3,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        [detail],
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "type": "경험면접",
                    "question_source": "openai_api",
                    "question_evidence_id": stable_ksa_evidence_id(evidence),
                    "question": (
                        "간행물 관리 점검 절차·점검 절차·점검 절차가 바뀐 뒤 누락이 생겼을 때 "
                        "당시 담당 역할과 목표를 말씀해 주세요."
                    ),
                    "follow_ups": [
                        "방금 말씀하신 역할에서 무엇을 먼저 확인했습니까?",
                        "앞서 언급한 누락을 줄이기 위해 어떤 자료를 봤습니까?",
                        "그 경험 이후 어떤 점을 보완했습니까?",
                    ],
                    "evaluation_points": [
                        "관련 확인·판단 기준",
                        "관련 확인·판단 기준",
                        "관련 확인·판단 기준",
                        "관련 확인·판단 기준",
                    ],
                }
            ]
        },
        plan,
        ["경험면접"],
        ncs_matches=[
            {
                "ncsClCd": evidence["ncsClCd"],
                "compeUnitName": evidence["compeUnitName"],
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[evidence],
    )

    question = out["interview_questions"][0]

    assert question["model_question_preserved"] is True
    assert question["question_source"] == "openai_api_quality_repaired_fields"
    assert "이어서 당시 맡은 역할과 목표" not in question["question"]
    assert "확인한 규정·문서·자료와 적용 범위의 판단 근거" in question["question"]
    assert "그에 따라 직접 취한 행동" in question["question"]
    assert "문서·수치·기록·피드백으로 확인한 결과" in question["question"]
    assert "점검 절차·점검 절차·점검 절차" not in question["question"]


def test_experience_method_evaluation_points_do_not_flatten_distinct_ksa_types_to_one_generic_phrase() -> None:
    knowledge_points = _method_evaluation_points(
        "경험면접",
        ["승인된 변경에 대한 지식"],
        "지식",
    )
    skill_points = _method_evaluation_points(
        "경험면접",
        ["과거 단계 문서 검토 능력"],
        "기술",
    )
    attitude_points = _method_evaluation_points(
        "경험면접",
        ["과거 프로젝트 교훈 반영 태도"],
        "태도",
    )

    assert knowledge_points != skill_points
    assert skill_points != attitude_points
    assert knowledge_points[1] == "규정 적용 근거"
    assert skill_points[1] == "자료·도구를 사용한 수행 순서"
    assert attitude_points[2] == "선택으로 감수한 점"
    for points in (knowledge_points, skill_points, attitude_points):
        assert "관련 확인·판단 기준" not in points


def test_main_question_shape_requires_official_sample_procedure_terms() -> None:
    assert _method_shape_ok(
        "발표면접",
        "[발표과제] 문서작성 업무에서 준비시간 20분 후 현황을 진단하고 대안 2가지를 5분 발표하며 실행계획, 성과지표, 질의응답 답변을 제시해 주세요.",
    ) is True
    assert _method_shape_ok(
        "발표면접",
        "[발표과제] 문서작성 업무에서 현황을 진단하고 대안 2가지를 발표하며 실행계획과 성과지표를 제시해 주세요.",
    ) is False
    assert _method_shape_ok(
        "토론면접",
        "[토론과제] 보안 강화 입장과 공유 효율 입장이 충돌하는 상황에서 각 입장의 근거와 위험을 검토하고 최종 합의안을 제시해 주세요.",
    ) is True
    assert _method_shape_ok(
        "창의적 문제해결력면접",
        "[창의적 문제해결력과제] 미래예측 관점에서 문제를 정의하고 창의적 대안, 검증 방법, 실현가능성, 의사결정 기준, 실행계획을 제시해 주세요.",
    ) is True
    assert _method_shape_ok(
        "창의적 문제해결력면접",
        "[창의적 문제해결력과제] 문제를 정의하고 창의적 대안, 검증 방법, 실행계획을 제시해 주세요.",
    ) is False
    assert _method_shape_ok(
        "인바스켓면접",
        "[인바스켓과제] 제한시간 30분 안에 문서 우선순위 보고 위임 직접처리 기준을 설명해 주세요.",
    ) is False
    assert _method_shape_ok(
        "직무지식면접",
        "문서작성에서 절차, 기준, 산출물, 예외상황을 적용했던 경험을 말씀해 주세요. 당시 상황, 본인 역할, 선택한 행동을 포함해 설명해 주세요.",
    ) is False


def test_job_knowledge_shape_allows_procedural_action_order_language() -> None:
    assert _method_shape_ok(
        "직무지식면접",
        "문서관리에서 문서 보존 기준과 관련해 확인해야 할 절차, 기준, 산출물, 예외상황 발생 시 행동 순서와 품질 점검 방법을 설명해 주세요.",
    ) is True


def test_question_quality_report_rejects_vague_evaluation_points_even_with_one_anchor() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "문서작성 상황에서 문서 요구사항 충돌이 발생하면 어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?",
                "follow_ups": [
                    "문서 요구사항 중 먼저 확인할 사실은 무엇입니까?",
                    "관련 부서에는 어떤 기준으로 설명하겠습니까?",
                    "후속 위험은 어떻게 점검하겠습니까?",
                ],
                "evaluation_points": ["판단 기준", "성실성", "태도", "자신감"],
                "ksa_refs": ["문서 요구사항 파악"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["evaluation_points"] is True
    assert item["checks"]["evaluation_points_quality"] is False
    assert item["ready"] is False
    assert "evaluation_points_quality" in item["issues"]


def test_question_quality_report_rejects_cross_method_evaluation_points() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "발표면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "[발표과제] 사무행정 문서작성 업무에서 문서 요구사항 오류 자료가 주어졌다고 가정하고 준비시간 20분 후 현황을 진단하고 개선 대안을 5분 발표해 주세요. 발표에는 대안 2가지, 실행 우선순위, 성과지표, 5분 질의응답 답변을 포함하세요.",
                "follow_ups": [
                    "문서 요구사항 쟁점을 발표에서 진단할 때 핵심 근거 자료는 무엇입니까?",
                    "대안 중 우선순위를 가장 높게 둔 방안과 그 이유는 무엇입니까?",
                    "면접위원이 반대 의견을 제시한다면 어떤 근거로 답변하시겠습니까?",
                ],
                "evaluation_points": ["자료 분석력", "논리적 구조화", "행동 순서와 첫 조치", "위험요인 인식"],
                "ksa_refs": ["문서 요구사항 파악"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["official_sample_format"] is True
    assert item["checks"]["evaluation_points_quality"] is False
    assert item["ready"] is False
    assert "evaluation_points_quality" in item["issues"]


def test_question_quality_report_requires_job_specific_context_tokens() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "업무 상황에서 여러 요청이 충돌하면 어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?",
                "follow_ups": [
                    "먼저 확인할 사실은 무엇입니까?",
                    "관련 부서에는 어떤 기준으로 설명하겠습니까?",
                    "후속 위험은 어떻게 점검하겠습니까?",
                ],
                "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
                "ksa_refs": ["문서 요구사항 파악"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["specific_context"] is True
    assert item["checks"]["job_specific_context"] is False
    assert item["ready"] is False
    assert "job_specific_context" in item["issues"]


def test_question_quality_report_requires_visible_primary_job_context_not_only_ksa_terms() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question_focus": "문서 요구사항 파악",
                "question": "문서 요구사항 파악 상황에서 오류가 발생하면 어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?",
                "follow_ups": [
                    "문서 요구사항 중 먼저 살펴볼 사실은 무엇입니까?",
                    "문서 요구사항 파악을 기준으로 그 행동을 선택한 이유는 무엇입니까?",
                    "후속 위험을 어떻게 점검하고 예방하겠습니까?",
                ],
                "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["ksa_grounded"] is True
    assert item["checks"]["job_specific_context"] is False
    assert item["ready"] is False
    assert "job_specific_context" in item["issues"]


def test_question_quality_report_requires_method_specific_followups() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "문서작성 상황에서 문서 요구사항 충돌이 발생하면 어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?",
                "follow_ups": [
                    "더 자세히 설명해 주세요.",
                    "그 이유를 말씀해 주세요.",
                    "마지막으로 보완할 점을 설명해 주세요.",
                ],
                "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
                "ksa_refs": ["문서 요구사항 파악"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["follow_up_depth"] is True
    assert item["checks"]["follow_up_quality"] is False
    assert item["ready"] is False
    assert "follow_up_quality" in item["issues"]


def test_question_quality_report_requires_inbasket_followups_to_probe_routing_decision() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "인바스켓면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": (
                    "[인바스켓과제] 제한시간 30분 안에 사무행정 문서작성 관련 문서와 요청이 동시에 들어왔습니다. "
                    "문서 요구사항 파악을 기준으로 처리 우선순위와 상급자 보고, 위임, 직접처리 판단 및 첫 조치 계획을 제시해 주세요."
                ),
                "follow_ups": [
                    "문서와 요청을 어떤 기준으로 분류하겠습니까?",
                    "가장 먼저 처리할 항목과 보류할 항목은 무엇입니까?",
                    "제한시간 안에 우선순위를 정할 때 어떤 기준을 적용하겠습니까?",
                ],
                "evaluation_points": ["우선순위 판단", "문서·요청 분류", "시간관리", "리스크 대응"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["main_question_method_shape"] is True
    assert item["checks"]["official_sample_format"] is True
    assert item["checks"]["follow_up_quality"] is False
    assert item["ready"] is False
    assert "follow_up_quality" in item["issues"]


def test_question_quality_report_requires_job_knowledge_followups_to_probe_output_or_exception() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "직무지식면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": (
                    "사무행정 문서작성에서 문서 요구사항 파악을 적용하기 위해 확인해야 할 절차, 기준, 산출물을 설명하고 "
                    "예외상황에서 오류를 예방하는 직무지식 적용 방안을 제시해 주세요."
                ),
                "follow_ups": [
                    "문서 요구사항 파악 관련 기준이나 규정은 무엇입니까?",
                    "그 기준을 적용하는 순서는 어떻게 정하겠습니까?",
                    "신규 담당자에게 관련 규정을 어떤 순서로 교육하겠습니까?",
                ],
                "evaluation_points": ["절차·기준 이해", "직무지식 적용", "예외상황 판단", "산출물 품질"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["main_question_method_shape"] is True
    assert item["checks"]["official_sample_format"] is True
    assert item["checks"]["follow_up_quality"] is False
    assert item["ready"] is False
    assert "follow_up_quality" in item["issues"]


def test_question_quality_report_requires_followups_to_probe_question_focus() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "경험면접",
                "competency": "수·배송계획수립",
                "ncsClCd": "0904010101_25v3",
                "ncs_detail": "화물운송",
                "question_focus": "수ㆍ배송 개념",
                "question": (
                    "화물운송에서 수ㆍ배송 개념을 적용했던 경험을 말씀해 주세요. "
                    "당시 상황, 본인 역할, 선택한 행동, 결과와 학습을 포함해 설명해 주세요."
                ),
                "follow_ups": [
                    "당시 화물의 종류와 수량은 무엇이었습니까?",
                    "그 판단에 따라 어떤 행동을 선택했습니까?",
                    "결과적으로 어떤 학습을 하셨습니까?",
                ],
                "evaluation_points": ["구체적 상황 설명", "본인 역할과 행동", "성과와 학습", "판단 근거와 협업"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0904010101_25v3",
                        "compeUnitName": "수·배송계획수립",
                        "factorName": "수ㆍ배송 개념",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["follow_up_quality"] is False
    assert item["ready"] is False
    assert "follow_up_quality" in item["issues"]


def test_ksa_evidence_attachment_prioritizes_question_focus_over_existing_refs() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "인바스켓면접",
                "competency": "한식 면류조리",
                "ncsClCd": "1301010103_21v4",
                "ncs_detail": "한식조리",
                "question_focus": "식재료 선별능력",
                "ksa_refs": ["주재료의 종류"],
                "question": (
                    "[인바스켓과제] 제한시간 안에 한식 면류조리 관련 여러 문서와 요청이 들어왔습니다. "
                    "식재료 선별능력을 기준으로 우선순위, 보고, 위임, 직접처리 판단을 제시해 주세요."
                ),
                "follow_ups": [
                    "식재료 선별능력 기준으로 먼저 확인할 문서와 요청은 무엇입니까?",
                    "식재료 선별능력을 기준으로 어떤 우선순위로 처리할 것인가요?",
                    "최종 결정 후 후속 점검은 어떻게 하겠습니까?",
                ],
                "evaluation_points": ["우선순위 판단", "문서·요청 분류", "보고·위임·직접처리 판단", "시간관리"],
            }
        ]
    }

    out = _attach_ksa_evidence_to_strategy(
        strategy,
        [
            {"ncsClCd": "1301010103_21v4", "factorName": "주재료의 종류"},
            {"ncsClCd": "1301010103_21v4", "factorName": "식재료 선별능력"},
        ],
    )

    question = out["interview_questions"][0]

    assert question["ksa_evidence"][0]["factorName"] == "식재료 선별능력"
    assert "식재료 선별능력" in question["ksa_refs"]
    assert out["question_quality_report"]["items"][0]["checks"]["ksa_grounded"] is True


def test_question_quality_report_rejects_unrelated_ksa_for_same_ncs_code() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "사무행정 문서작성 상황에서 문서 요구사항 충돌이 발생하면 어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?",
                "follow_ups": [
                    "문서 요구사항 중 먼저 확인할 사실은 무엇입니까?",
                    "문서작성 기준과 관련해 그 행동을 선택한 이유와 예상되는 위험요인은 무엇입니까?",
                    "관련 부서에는 어떤 순서와 방식으로 설명하시겠습니까?",
                ],
                "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
                "ksa_refs": ["민원 응대", "회의 운영"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "민원 응대",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    },
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "회의 운영",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    },
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["job_specific_context"] is True
    assert item["checks"]["ksa_grounded"] is False
    assert item["ready"] is False
    assert "ksa_grounded" in item["issues"]


def test_assigned_evidence_cannot_be_rescued_by_another_factor_from_same_unit() -> None:
    assigned = {
        "ncsClCd": "0101010205_17v2",
        "compeUnitName": "프로젝트 인적자원관리",
        "factorName": "승인된 변경에 대한 지식",
        "ksaTypeName": "지식",
        "factorSource": "ncs-mcp",
    }
    alternate = {
        "ncsClCd": "0101010205_17v2",
        "compeUnitName": "프로젝트 인적자원관리",
        "factorName": "팀원 역할 분담 조정 능력",
        "ksaTypeName": "기술",
        "factorSource": "ncs-mcp",
    }
    strategy = {
        "interview_questions": [
            {
                "type": "경험면접",
                "competency": "프로젝트 인적자원관리",
                "ncsClCd": assigned["ncsClCd"],
                "ncs_detail": "프로젝트관리",
                "question": "팀원 역할 분담을 조정한 경험을 말씀해 주세요. 당시 본인의 행동과 결과는 무엇이었습니까?",
                "follow_ups": [
                    "방금 언급한 역할 분담 자료는 무엇입니까?",
                    "앞서 말한 조정 행동을 선택한 이유는 무엇입니까?",
                    "조정 결과는 어떤 피드백으로 확인했습니까?",
                ],
                "evaluation_points": ["구체적 상황", "본인 역할", "조정 행동", "결과 증거"],
                "question_evidence_required": True,
                "question_evidence_id": stable_ksa_evidence_id(assigned),
                "question_focus": assigned["factorName"],
                "question_focus_surface": "승인된 변경 관련 확인·판단 기준",
                "ksa_refs": [assigned["factorName"]],
                "ksa_evidence": [assigned, alternate],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["ksa_grounded"] is False
    assert "ksa_grounded" in item["issues"]


def test_question_quality_report_rejects_ksa_grounding_when_factor_only_in_metadata() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question_focus": "민원 응대 기준",
                "question": "사무행정 문서작성 상황에서 자료 오류와 마감 지연이 동시에 발생했습니다. 어떤 판단 기준과 순서로 행동하고 위험을 통제하겠습니까?",
                "follow_ups": [
                    "문서작성 자료에서 먼저 살펴볼 사실은 무엇입니까?",
                    "사무행정 담당자로서 그 행동을 선택한 이유와 예상되는 위험요인은 무엇입니까?",
                    "관련 부서에는 어떤 순서와 방식으로 설명하시겠습니까?",
                ],
                "evaluation_points": ["핵심 사실 확인", "판단 기준", "행동 순서와 첫 조치", "위험요인 인식"],
                "ksa_refs": ["민원 응대 기준"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "민원 응대 기준",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["job_specific_context"] is True
    assert item["checks"]["ksa_grounded"] is False
    assert item["ready"] is False
    assert "ksa_grounded" in item["issues"]


def test_question_quality_report_rejects_ksa_grounding_when_factor_only_in_evaluation_points() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question_focus": "고객 불만 응대 기준",
                "question": (
                    "사무행정 문서작성 상황에서 자료 오류와 마감 지연이 동시에 발생했습니다. "
                    "어떤 판단 기준과 순서로 행동하고 위험을 통제하시겠습니까?"
                ),
                "follow_ups": [
                    "문서작성 자료에서 먼저 확인할 사실은 무엇입니까?",
                    "사무행정 담당자로서 그 행동 순서를 선택한 이유는 무엇입니까?",
                    "후속 위험을 예방하기 위해 어떤 점검을 하시겠습니까?",
                ],
                "evaluation_points": [
                    "고객 불만 응대 기준",
                    "사실 확인",
                    "판단 기준",
                    "행동 순서와 위험 통제",
                ],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "고객 불만 응대 기준",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["job_specific_context"] is True
    assert item["checks"]["ksa_grounded"] is False
    assert item["ready"] is False
    assert "ksa_grounded" in item["issues"]


def test_question_quality_report_rejects_unresolved_ksa_placeholder() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "상황면접",
                "competency": "경비계획",
                "ncsClCd": "1101010101_25v3",
                "ncs_detail": "보안",
                "question": (
                    "보안 경비계획 상황에서 KSA 관련 문제가 발생했습니다. "
                    "어떤 판단 기준과 순서로 행동하고 위험을 통제하시겠습니까?"
                ),
                "follow_ups": [
                    "현장에서 먼저 확인할 사실과 기준은 무엇입니까?",
                    "현장조사 능력과 관련해 그 행동을 선택한 이유와 예상되는 위험요인은 무엇입니까?",
                    "방문객과 현장 담당자에게 어떤 순서와 방식으로 설명하시겠습니까?",
                ],
                "evaluation_points": ["사실확인", "판단기준", "행동순서", "위험요인"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "1101010101_25v3",
                        "compeUnitName": "경비계획",
                        "factorName": "현장조사 능력",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["ksa_grounded"] is False
    assert item["ready"] is False
    assert "ksa_grounded" in item["issues"]


def test_question_quality_report_rejects_preserved_model_question_with_wrong_job_context() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "발표면접",
                "competency": "구조물해체 도면파악",
                "ncsClCd": "1403020101_25v3",
                "ncs_detail": "구조물해체",
                "ncsSubdCdnm": "구조물해체",
                "question_source": "model_main_template_followups",
                "model_question_preserved": True,
                "question": (
                    "[발표과제] 워터파크 안전관리 관련 자료가 주어졌다고 가정하고 "
                    "준비시간 20분 후 현황을 진단하고 대안 2가지, 실행계획, 성과지표를 5분 발표하고 5분 질의응답 답변을 포함해 주세요."
                ),
                "follow_ups": [
                    "도면 숙지 의지 쟁점을 발표에서 진단할 때 핵심 근거 자료는 무엇입니까?",
                    "대안 중 우선순위를 가장 높게 둔 방안과 그 이유는 무엇입니까?",
                    "면접위원이 반대 의견을 제시한다면 어떤 근거로 답변하시겠습니까?",
                ],
                "evaluation_points": ["자료분석력", "논리적구조화", "대안의실행가능성", "실행계획"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "1403020101_25v3",
                        "compeUnitName": "구조물해체 도면파악",
                        "factorName": "도면 숙지 의지",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["main_question_job_context"] is False
    assert item["ready"] is False
    assert "main_question_job_context" in item["issues"]


def test_method_evaluation_points_keep_four_method_anchors_and_ksa_evidence() -> None:
    points = _method_evaluation_points("발표면접", ["문서 요구사항 파악"])
    visible = "\n".join(points)

    assert len(points) == 4
    assert "수행 순서·산출물·품질 확인" in visible
    assert "자료 근거와 현황·원인 분석" in visible
    assert "성과지표와 질의응답 대응" in visible


def test_method_evaluation_points_measure_ksa_without_repeating_public_label() -> None:
    attitude = _method_evaluation_points("인바스켓면접", ["관찰하는 태도"], "태도")
    knowledge_vowel = _method_evaluation_points("직무지식면접", ["메뉴 이해"], "지식")
    knowledge_consonant = _method_evaluation_points("직무지식면접", ["문서 보안 규정"], "지식")

    visible = "\n".join([*attitude, *knowledge_vowel, *knowledge_consonant])
    assert "압박 상황에서 드러난 선택 행동과 책임" in visible
    assert visible.count("판단 근거와 적용 범위·예외 구분") == 2
    assert "관찰 관련 행동 기준" not in visible
    assert "메뉴 이해·판단 기준" not in visible
    assert "문서 보안 규정 적용·판단 기준" not in visible


def test_question_quality_report_requires_main_question_method_shape() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "인바스켓면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "문서작성 업무에서 중요한 점을 설명해 주세요.",
                "follow_ups": [
                    "[인바스켓과제] 제한시간 30분 안에 여러 문서와 보고 요청이 들어온 상황을 어떻게 분류하겠습니까?",
                    "처리 우선순위와 보류할 항목은 무엇입니까?",
                    "상급자 보고, 위임, 직접 처리 중 어떤 방식을 선택하겠습니까?",
                ],
                "evaluation_points": ["우선순위 판단", "문서·요청 분류", "시간관리", "리스크 대응"],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "문서작성",
                        "factorName": "문서 요구사항 파악",
                        "factorSource": "ncs-mcp",
                        "ksaStatus": "official",
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["checks"]["method_shape"] is True
    assert item["checks"]["main_question_method_shape"] is False
    assert item["ready"] is False
    assert "main_question_method_shape" in item["issues"]


def test_ksa_evidence_is_not_attached_from_other_ncs_code() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "직무지식면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "문서작성에서 요구사항 파악을 적용하기 위해 확인해야 할 절차, 기준, 산출물을 설명해 주세요.",
                "follow_ups": [
                    "관련 기준이나 규정은 무엇입니까?",
                    "예외상황은 어떻게 판단하겠습니까?",
                    "산출물 품질을 어떻게 점검하겠습니까?",
                ],
                "evaluation_points": ["절차·기준 이해", "직무지식 적용", "예외상황 판단", "산출물 품질"],
            }
        ]
    }

    out = _attach_ksa_evidence_to_strategy(
        strategy,
        [
            {
                "ncsClCd": "9999999999_25v3",
                "compeUnitName": "다른 능력단위",
                "factorName": "다른 코드의 KSA",
                "factorSource": "ncs-mcp",
                "ksaStatus": "official",
            }
        ],
    )
    question = out["interview_questions"][0]
    item = out["question_quality_report"]["items"][0]

    assert "ksa_refs" not in question
    assert "ksa_evidence" not in question
    assert item["checks"]["ksa_grounded"] is False
    assert "ksa_grounded" in item["issues"]


def test_ksa_evidence_is_not_attached_without_ncs_code() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "\uc9c1\ubb34\uc9c0\uc2dd\uba74\uc811",
                "competency": "\ubb38\uc11c\uc791\uc131",
                "ncsClCd": "",
                "ncs_detail": "\uc0ac\ubb34\ud589\uc815",
                "question": "\ubb38\uc11c\uc791\uc131\uc5d0\uc11c \ud655\uc778\ud574\uc57c \ud560 \uc808\ucc28, \uae30\uc900, \uc0b0\ucd9c\ubb3c\uc744 \uc124\uba85\ud574 \uc8fc\uc138\uc694.",
                "follow_ups": [
                    "\uad00\ub828 \uae30\uc900\uc774\ub098 \uaddc\uc815\uc740 \ubb34\uc5c7\uc785\ub2c8\uae4c?",
                    "\uc608\uc678\uc0c1\ud669\uc740 \uc5b4\ub5bb\uac8c \ud310\ub2e8\ud558\uaca0\uc2b5\ub2c8\uae4c?",
                    "\uc0b0\ucd9c\ubb3c \ud488\uc9c8\uc744 \uc5b4\ub5bb\uac8c \uc810\uac80\ud558\uaca0\uc2b5\ub2c8\uae4c?",
                ],
                "evaluation_points": [
                    "\uc808\ucc28\u00b7\uae30\uc900 \uc774\ud574",
                    "\uc9c1\ubb34\uc9c0\uc2dd \uc801\uc6a9",
                    "\uc608\uc678\uc0c1\ud669 \ud310\ub2e8",
                    "\uc0b0\ucd9c\ubb3c \ud488\uc9c8",
                ],
            }
        ]
    }

    out = _attach_ksa_evidence_to_strategy(
        strategy,
        [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "\ubb38\uc11c\uc791\uc131",
                "factorName": "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545",
                "factorSource": "ncs-mcp",
                "ksaStatus": "official",
            }
        ],
    )
    question = out["interview_questions"][0]
    item = out["question_quality_report"]["items"][0]

    assert "ksa_refs" not in question
    assert "ksa_evidence" not in question
    assert item["checks"]["ksa_grounded"] is False
    assert "ksa_grounded" in item["issues"]


def test_question_quality_report_flags_method_grounding_and_blind_hiring_gaps() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "가치·태도형",
                "competency": "",
                "ncsClCd": "",
                "question": "출신학교와 가족 배경을 포함해 설명해 주세요.",
                "follow_ups": ["추가 설명해 주세요."],
                "evaluation_points": ["태도"],
            }
        ]
    }

    out = _attach_question_quality_report(strategy)
    item = out["question_quality_report"]["items"][0]

    assert item["ready"] is False
    assert item["checks"]["blind_hiring_safe"] is False
    assert item["checks"]["supported_method"] is False
    assert item["checks"]["ksa_grounded"] is False
    assert item["checks"]["detail_grounded"] is False
    assert "blind_hiring_safe" in item["issues"]


def test_question_quality_report_marks_only_later_duplicate_question() -> None:
    question = {
        "type": "경험면접",
        "method": "경험면접",
        "competency": "문서작성",
        "ncsClCd": "0202030201_25v3",
        "ncs_detail": "사무행정",
        "question_focus": "문서 요구사항 파악",
        "ksa_refs": ["문서 요구사항 파악"],
        "question": (
            "사무행정 문서작성 업무에서 문서 요구사항 파악을 적용해 문제를 해결한 경험을 말씀해 주세요. "
            "당시 상황, 본인 역할, 선택한 행동, 결과와 학습을 포함해 설명해 주세요."
        ),
        "follow_ups": [
            "당시 상황과 문서작성에서 본인이 맡은 역할을 구체적으로 설명해 주세요.",
            "문서 요구사항 파악을 적용하기 위해 실제로 취한 행동은 무엇이었습니까?",
            "성과를 어떤 기준으로 확인했고 같은 상황에서 무엇을 개선하시겠습니까?",
        ],
        "evaluation_points": ["상황과 역할", "판단 기준", "행동 근거", "성과와 학습"],
    }

    out = _attach_question_quality_report({"interview_questions": [dict(question), dict(question)]})
    items = out["question_quality_report"]["items"]

    assert items[0]["checks"]["unique_question"] is True
    assert items[0]["question_repeat_duplicate"] is False
    assert items[1]["checks"]["unique_question"] is False
    assert items[1]["question_repeat_duplicate"] is True


def test_adjust_questions_replaces_repeated_intent_with_alternate_ksa_focus() -> None:
    detail = "\uc0ac\ubb34\ud589\uc815"
    competency = "\ubb38\uc11c\uc791\uc131"
    focus_1 = "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545"
    focus_2 = "\uc77c\uc815 \uacc4\ud68d \uc218\ub9bd"
    method = "\uacbd\ud5d8\uba74\uc811"
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )
    repeated_model_question = (
        f"{detail} {competency} 업무에서 {focus_1}을 적용한 경험을 말씀해 주세요. "
        "당시 상황, 본인 역할, 선택한 행동, 결과를 포함해 설명해 주세요."
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {"question": repeated_model_question},
                {"question": repeated_model_question},
            ]
        },
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
                "compeUnitDef": "\uc694\uad6c\uc0ac\ud56d\uc744 \ud30c\uc545\ud558\uc5ec \ubb38\uc11c\ub97c \uc791\uc131\ud558\ub294 \ub2a5\ub825\uc774\ub2e4.",
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "0202030201_25v3", "factorName": focus_1},
            {"ncsClCd": "0202030201_25v3", "factorName": focus_2},
        ],
    )

    questions = out["interview_questions"]
    assert len(questions) == 2
    assert questions[0]["question_focus"] == focus_1
    assert questions[1]["question_focus"] == focus_2
    assert questions[1]["question_source"] == "template_fallback"
    assert "duplicate_question_intent" in questions[1]["model_replacement_reasons"]
    assert questions[0]["question_repeat_signature"] != questions[1]["question_repeat_signature"]
    assert questions[1]["question_repeat_duplicate"] is False


def test_adjust_questions_keeps_planning_ksa_focuses_distinct() -> None:
    detail = "\uc0ac\ubb34\ud589\uc815"
    competency = "\ubb38\uc11c\uc791\uc131"
    focus_1 = "\uc77c\uc815 \uacc4\ud68d \uc218\ub9bd"
    focus_2 = "\uc608\uc0b0 \uacc4\ud68d \uc218\ub9bd"
    method = "\uacbd\ud5d8\uba74\uc811"
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": (
                        f"{detail} {competency} 업무에서 {focus_1}을 적용해 문제를 해결한 경험을 말씀해 주세요. "
                        "당시 상황, 본인 역할, 선택한 행동, 결과를 포함해 설명해 주세요."
                    )
                },
                {
                    "question": (
                        f"{detail} {competency} 업무에서 {focus_2}을 적용해 문제를 해결한 경험을 말씀해 주세요. "
                        "당시 상황, 본인 역할, 선택한 행동, 결과를 포함해 설명해 주세요."
                    )
                },
            ]
        },
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "0202030201_25v3", "factorName": focus_1},
            {"ncsClCd": "0202030201_25v3", "factorName": focus_2},
        ],
    )

    questions = out["interview_questions"]
    assert [q["question_focus"] for q in questions] == [focus_1, focus_2]
    assert [q["question_focus_surface"] for q in questions] == [
        "일정 계획 작성·검토 절차",
        "예산 계획 작성·검토 절차",
    ]
    assert [q["question_intent"] for q in questions] == ["experience_behavior", "experience_behavior"]
    assert questions[0]["question_repeat_signature"] != questions[1]["question_repeat_signature"]
    assert all(q["question_repeat_duplicate"] is False for q in questions)


def test_adjust_questions_scopes_discussion_conflict_by_focus() -> None:
    detail = "사무행정"
    competency = "문서작성"
    focus_1 = "문서 요구사항 파악"
    focus_2 = "일정 계획 수립"
    method = "토론면접"
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "0202030201_25v3", "factorName": focus_1},
            {"ncsClCd": "0202030201_25v3", "factorName": focus_2},
        ],
    )

    questions = out["interview_questions"]
    assert [q["question_focus"] for q in questions] == [focus_1, focus_2]
    assert [q["question_intent"] for q in questions] == ["discussion_task", "discussion_task"]
    assert questions[0]["question_repeat_signature"] != questions[1]["question_repeat_signature"]
    assert all(not q["question_repeat_signature"].endswith("|general") for q in questions)
    assert all(q["question_repeat_duplicate"] is False for q in questions)


def test_adjust_questions_preserves_same_focus_model_questions_when_scenario_differs() -> None:
    detail = "사무행정"
    competency = "문서작성"
    focus = "문서 요구사항 파악"
    method = "토론면접"
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 2, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )
    common_followups = [
        f"{focus}을 토론 쟁점으로 볼 때 {competency} 입장발표의 근거는 무엇입니까?",
        "반대 의견 중 수용할 수 있는 부분과 조정하기 어려운 부분은 무엇입니까?",
        "최종 합의안과 후속점검 기준을 어떻게 정리하시겠습니까?",
    ]
    common_evaluation_points = ["입장발표 근거", "경청과 상호작용", "갈등 조정", "최종 합의안 도출"]

    out = _adjust_generated_questions(
        {
            "interview_questions": [
                {
                    "question": (
                        f"[토론과제] {detail} {competency} 업무에서 {focus} 기준을 강화하는 입장과 "
                        "처리 속도를 우선하는 입장이 충돌합니다. 토론시간 20분 동안 1분 입장발표 후 "
                        "반대 의견을 검토하고 조정 방식과 최종 합의안을 제시해 주세요. "
                        f"합의안에는 {focus} 수행 절차와 품질 검증 산출물을 포함해 주세요."
                    ),
                    "follow_ups": list(common_followups),
                    "evaluation_points": list(common_evaluation_points),
                },
                {
                    "question": (
                        f"[토론과제] {detail} {competency} 업무에서 {focus} 관련 정보 공유를 확대해야 한다는 입장과 "
                        "보안 책임을 강화해야 한다는 입장이 충돌합니다. 토론시간 20분 동안 1분 입장발표 후 "
                        "반대 의견을 검토하고 조정 방식과 최종 합의안을 제시해 주세요. "
                        f"합의안에는 {focus} 수행 절차와 품질 검증 산출물을 포함해 주세요."
                    ),
                    "follow_ups": list(common_followups),
                    "evaluation_points": list(common_evaluation_points),
                },
            ]
        },
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": focus}],
    )

    questions = out["interview_questions"]

    assert [q["question_source"] for q in questions] == ["template_fallback", "template_fallback"]
    assert all(q["question_focus"] == focus for q in questions)
    assert questions[0]["question_repeat_signature"] == questions[1]["question_repeat_signature"]
    assert all(q["question_repeat_duplicate"] is False for q in questions)
    assert all(q["model_question_preserved"] is False for q in questions)
    assert all("main_question_method_shape" in q["model_replacement_reasons"] for q in questions)
    assert questions[0]["question"] != questions[1]["question"]


def test_adjust_questions_splits_single_ksa_into_distinct_focus_angles() -> None:
    detail = "\uc0ac\ubb34\ud589\uc815"
    competency = "\ubb38\uc11c\uc791\uc131"
    focus = "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545"
    method = "\uacbd\ud5d8\uba74\uc811"
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 3, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        [method],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": focus}],
    )

    questions = out["interview_questions"]
    assert len(questions) == 3
    assert questions[0]["question_focus"] == focus
    assert questions[1]["question_focus"].startswith(focus)
    assert questions[2]["question_focus"].startswith(focus)
    assert len({q["question_repeat_signature"] for q in questions}) == 3
    assert all(q["question_repeat_duplicate"] is False for q in questions)
    assert all(q["question_focus_surface"] in q["question"] for q in questions)


def test_adjust_questions_classifies_creative_problem_before_presentation_overlap() -> None:
    detail = "\uc0ac\ubb34\ud589\uc815"
    competency = "\ubb38\uc11c\uc791\uc131"
    focus = "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545"
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": detail, "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        [detail],
    )

    out = _adjust_generated_questions(
        {"interview_questions": []},
        plan,
        ["\ucc3d\uc758\uc801 \ubb38\uc81c\ud574\uacb0\ub825\uba74\uc811"],
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": competency,
                "ncsSclasCdnm": detail,
                "ncsSubdCdnm": detail,
                "matchedDetailName": detail,
            }
        ],
        ncs_ksa=[{"ncsClCd": "0202030201_25v3", "factorName": focus}],
    )

    question = out["interview_questions"][0]

    assert question["type"] == "\ucc3d\uc758\uc801 \ubb38\uc81c\ud574\uacb0\ub825\uba74\uc811"
    assert question["question_intent"] == "creative_problem"
    assert question["question_repeat_signature"].startswith("creative_problem|")


def test_question_quality_report_exposes_question_focus_metadata() -> None:
    focus = "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545"
    strategy = {
        "interview_questions": [
            {
                "type": "\uacbd\ud5d8\uba74\uc811",
                "competency": "\ubb38\uc11c\uc791\uc131",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "\uc0ac\ubb34\ud589\uc815",
                "question_focus": focus,
                "question": (
                    f"\uc0ac\ubb34\ud589\uc815 \ubb38\uc11c\uc791\uc131 \uc5c5\ubb34\uc5d0\uc11c '{focus}'\uc744 \uc801\uc6a9\ud574 "
                    "\ubb38\uc81c\ub97c \ud574\uacb0\ud55c \uacbd\ud5d8\uc744 \ub9d0\uc500\ud574 \uc8fc\uc138\uc694."
                ),
                "follow_ups": [
                    f"'{focus}'\uc744 \ud655\uc778\ud55c \uadfc\uac70\ub294 \ubb34\uc5c7\uc785\ub2c8\uae4c?",
                    "\ub2f9\uc2dc \uc120\ud0dd\ud55c \ud589\ub3d9\uc740 \ubb34\uc5c7\uc785\ub2c8\uae4c?",
                    "\uacb0\uacfc\ub97c \uc5b4\ub5bb\uac8c \ud655\uc778\ud588\uc2b5\ub2c8\uae4c?",
                ],
                "evaluation_points": ["\uc0c1\ud669 \ud30c\uc545", "\ud589\ub3d9 \uadfc\uac70", "\uacb0\uacfc \ud655\uc778", "\ud559\uc2b5 \uc804\uc774"],
                "ksa_refs": [focus],
                "ksa_evidence": [
                    {
                        "ncsClCd": "0202030201_25v3",
                        "compeUnitName": "\ubb38\uc11c\uc791\uc131",
                        "factorName": focus,
                    }
                ],
            }
        ]
    }

    item = _attach_question_quality_report(strategy)["question_quality_report"]["items"][0]

    assert item["question_focus"] == focus


def test_grouped_interview_questions_preserve_repeat_metadata() -> None:
    grouped = _group_interview_questions_for_response(
        [
            {
                "type": "\uacbd\ud5d8\uba74\uc811",
                "competency": "\ubb38\uc11c\uc791\uc131",
                "ncsClCd": "0202030201_25v3",
                "question": "\uc9c8\ubb38",
                "question_focus": "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545",
                "question_intent": "experience_behavior",
                "question_repeat_signature": "experience_behavior|\uacbd\ud5d8\uba74\uc811|focus:x",
                "question_repeat_duplicate": False,
            }
        ]
    )

    item = grouped[0]["questions"][0]

    assert item["question_focus"] == "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545"
    assert item["question_intent"] == "experience_behavior"
    assert item["question_repeat_signature"].startswith("experience_behavior|")
    assert item["question_repeat_duplicate"] is False


def test_question_quality_report_marks_later_general_intent_variant_duplicate() -> None:
    base = {
        "type": "\uacbd\ud5d8\uba74\uc811",
        "competency": "\ubb38\uc11c\uc791\uc131",
        "ncsClCd": "0202030201_25v3",
        "ncs_detail": "\uc0ac\ubb34\ud589\uc815",
        "question_focus": "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545",
        "follow_ups": [
            "\ub2f9\uc2dc \uadfc\uac70\ub294 \ubb34\uc5c7\uc785\ub2c8\uae4c?",
            "\uc120\ud0dd\ud55c \ud589\ub3d9\uc740 \ubb34\uc5c7\uc785\ub2c8\uae4c?",
            "\uacb0\uacfc\ub97c \uc5b4\ub5bb\uac8c \ud655\uc778\ud588\uc2b5\ub2c8\uae4c?",
        ],
        "evaluation_points": ["\uadfc\uac70", "\ud589\ub3d9", "\uacb0\uacfc", "\ud559\uc2b5"],
        "ksa_refs": ["\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545"],
    }
    first = {
        **base,
        "question": "\uc0ac\ubb34\ud589\uc815 \uc9c1\ubb34\uc5d0 \uc9c0\uc6d0\ud55c \ub3d9\uae30\ub97c \ub9d0\uc500\ud574 \uc8fc\uc138\uc694.",
    }
    second = {
        **base,
        "question": "\ubb38\uc11c\uc791\uc131 \uc5c5\ubb34\uc5d0 \uad00\uc2ec\uc744 \uac16\uac8c \ub41c \uc774\uc720\ub294 \ubb34\uc5c7\uc785\ub2c8\uae4c?",
    }

    items = _attach_question_quality_report({"interview_questions": [first, second]})["question_quality_report"]["items"]

    assert [item["question_repeat_signature"] for item in items] == ["motivation|general", "motivation|general"]
    assert items[0]["question_repeat_duplicate"] is False
    assert items[1]["question_repeat_duplicate"] is True
    assert items[0]["checks"]["unique_question"] is True
    assert items[1]["checks"]["unique_question"] is False


def test_question_intent_prioritizes_experience_when_problem_terms_overlap() -> None:
    question = (
        "\uc0ac\ubb34\ud589\uc815 \ubb38\uc11c\uc791\uc131 \uc5c5\ubb34\uc5d0\uc11c \ubb38\uc81c\ub97c \ud574\uacb0\ud55c "
        "\uacbd\ud5d8\uc744 \ub9d0\uc500\ud574 \uc8fc\uc138\uc694. \ub2f9\uc2dc \uc0c1\ud669, \ubcf8\uc778 \ud589\ub3d9, "
        "\uacb0\uacfc\ub97c \ud3ec\ud568\ud574 \uc124\uba85\ud574 \uc8fc\uc138\uc694."
    )

    assert _question_intent_key(question) == "experience_behavior"


def test_question_intent_keeps_experience_when_focus_mentions_collaboration() -> None:
    question = (
        "\ubb38\uc11c\uc791\uc131 \uc218\ud589 \uacfc\uc815\uc5d0\uc11c '\uc5c5\ubb34 \uc6b0\uc120\uc21c\uc704 \uc124\uc815'\uacfc "
        "'\uc774\ud574\uad00\uacc4\uc790 \ud611\uc5c5'\uc744 \uc801\uc6a9\ud574 \ubb38\uc81c\ub97c \ud574\uacb0\ud558\uac70\ub098 "
        "\uc131\uacfc\ub97c \ub0b8 \uacbd\ud5d8\uc744 \ub9d0\uc500\ud574 \uc8fc\uc138\uc694. "
        "\ub2f9\uc2dc \uc0c1\ud669, \ubcf8\uc778 \uc5ed\ud560, \uc120\ud0dd\ud55c \ud589\ub3d9, \uacb0\uacfc\uc640 "
        "\ud559\uc2b5\uc744 \ud3ec\ud568\ud574 \uc124\uba85\ud574 \uc8fc\uc138\uc694."
    )

    assert _question_intent_key(question) == "experience_behavior"


def test_question_intent_keeps_experience_in_institution_job_context() -> None:
    question = (
        "우리 기관 문서작성 업무를 수행한 경험을 말씀해 주세요. "
        "당시 상황, 본인 역할, 선택한 행동과 결과를 포함해 설명해 주세요."
    )

    assert _question_intent_key(question) == "experience_behavior"


def test_question_intent_prioritizes_job_knowledge_over_situation_markers() -> None:
    question = (
        "\ubb38\uc11c\uc791\uc131\uc5d0\uc11c '\uc5c5\ubb34 \uc6b0\uc120\uc21c\uc704 \uc124\uc815'\uacfc \uad00\ub828\ud574 "
        "\ud655\uc778\ud574\uc57c \ud560 \uc808\ucc28, \uae30\uc900, \uad00\ub828 \uadfc\uac70, \uc0b0\ucd9c\ubb3c\uc744 "
        "\uc124\uba85\ud558\uace0 \uc2e4\uc81c \uc5c5\ubb34\uc5d0 \uc801\uc6a9\ud560 \ub54c\uc758 \uc608\uc678\uc0c1\ud669, "
        "\ud488\uc9c8 \uc810\uac80 \ubc29\ubc95, \uc624\ub958 \uc608\ubc29 \uc720\uc758\uc810\uc744 \ub9d0\uc500\ud574 \uc8fc\uc138\uc694."
    )

    assert _question_intent_key(question) == "job_knowledge"


def test_method_template_scenario_changes_by_question_focus() -> None:
    document_question = _question_for_method(
        "\uc0c1\ud669\uba74\uc811",
        "\ubb38\uc11c\uc791\uc131",
        "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545",
        "\uc0ac\ubb34\ud589\uc815",
        "",
    )
    budget_question = _question_for_method(
        "\uc0c1\ud669\uba74\uc811",
        "\ubb38\uc11c\uc791\uc131",
        "\uc608\uc0b0 \uacc4\ud68d \uc218\ub9bd",
        "\uc0ac\ubb34\ud589\uc815",
        "",
    )

    assert document_question != budget_question
    assert "\uc790\ub8cc \ub204\ub77d" in document_question
    assert "\ubb38\uc11c \uae30\uc900 \ubd88\uc77c\uce58" in document_question
    assert "\uc608\uc0b0" in budget_question
    assert "\uc790\uc6d0 \uc81c\uc57d" in budget_question


def test_question_intent_keeps_situation_question_out_of_inbasket_bucket() -> None:
    question = (
        "\uc0ac\ubb34\ud589\uc815 \ubb38\uc11c\uc791\uc131 \uc5c5\ubb34 \uc911 '\ubcf4\uace0\uc11c \uc791\uc131'\uacfc \uad00\ub828\ud574 "
        "\uc790\ub8cc \ub204\ub77d\uacfc \ubb38\uc11c \uae30\uc900 \ubd88\uc77c\uce58 \uc0c1\ud669\uc774 \ubc1c\uc0dd\ud588\uc2b5\ub2c8\ub2e4. "
        "\uc5b4\ub5a4 \uae30\uc900\uc73c\ub85c \ud310\ub2e8\ud558\uace0 \uc704\ud5d8\uc694\uc778\uc744 \ud1b5\uc81c\ud558\uba70, "
        "\uc0ac\uc2e4 \ud655\uc778\ubd80\ud130 \ubcf4\uace0\uc640 \uc2e4\ud589\uae4c\uc9c0 \uc5b4\ub5a4 \uc21c\uc11c\ub85c \ud589\ub3d9\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?"
    )

    assert _question_intent_key(question) == "situation_judgment"


def test_question_intent_prioritizes_explicit_inbasket_marker() -> None:
    question = (
        "[\uc778\ubc14\uc2a4\ucf13\uacfc\uc81c] \uc81c\ud55c\uc2dc\uac04 30\ubd84 \uc548\uc5d0 \uc810\uac80 \uc694\uccad, "
        "\ubcf4\uace0 \ubb38\uc11c, \uc704\uc784 \ud544\uc694 \uc0ac\ud56d\uc774 \ub3d9\uc2dc\uc5d0 \ub4e4\uc5b4\uc654\uc2b5\ub2c8\ub2e4. "
        "\uc6b0\uc120\uc21c\uc704\uc640 \uc9c1\uc811\ucc98\ub9ac \ud310\ub2e8, \uccab \ud589\ub3d9 \uc21c\uc11c\ub97c \uc81c\uc2dc\ud574 \uc8fc\uc138\uc694."
    )

    assert _question_intent_key(question) == "inbasket_priority"


def test_question_intent_prioritizes_explicit_presentation_marker_over_growth_text() -> None:
    question = (
        "[\ubc1c\ud45c\uacfc\uc81c] \uc81c\ud488 \uc870\uc0ac \uae30\uc220 \uad00\ub828 \uc790\ub8cc\uac00 \uc8fc\uc5b4\uc84c\ub2e4\uace0 "
        "\uac00\uc815\ud558\uace0 \uc900\ube44\uc2dc\uac04 20\ubd84 \ud6c4 \ud604\ud669 \ubb38\uc81c\ub97c \uc9c4\ub2e8\ud558\uace0 "
        "\uac1c\uc120\uc548\uc744 5\ubd84 \ubc1c\ud45c\ud574 \uc8fc\uc138\uc694. "
        "\uc0ac\uc5c5 \uc774\uc775\uc5d0 \uae30\uc5ec\ud560 \uc218 \uc788\ub294 \ubc29\uc548\uc744 \ud3ec\ud568\ud558\uc138\uc694."
    )

    assert _question_intent_key(question) == "presentation_task"


def test_question_intent_prioritizes_explicit_discussion_marker_over_collaboration_text() -> None:
    question = (
        "[\ud1a0\ub860\uacfc\uc81c] \ud611\uc5c5 \ud6a8\uc728\uc744 \uc6b0\uc120\ud558\ub294 \uc785\uc7a5\uacfc "
        "\ubcf4\uc548 \uae30\uc900\uc744 \uac15\ud654\ud574\uc57c \ud55c\ub2e4\ub294 \uc785\uc7a5\uc774 \uac08\ub4f1\ud569\ub2c8\ub2e4. "
        "\ud1a0\ub860\uc2dc\uac04 20\ubd84 \ub3d9\uc548 \uc785\uc7a5\ubc1c\ud45c, \ubc18\ubc15, \uc870\uc815, "
        "\ucd5c\uc885 \ud569\uc758\uc548\uc744 \uc81c\uc2dc\ud574 \uc8fc\uc138\uc694."
    )

    assert _question_intent_key(question) == "discussion_task"


def test_security_focus_replaces_unrelated_domain_evidence() -> None:
    question = _question_for_method(
        "\ucc3d\uc758\uc801 \ubb38\uc81c\ud574\uacb0\ub825\uba74\uc811",
        "\uc2dd\uc74c\ub8cc \uc601\uc5c5 \uc900\ube44",
        "\uc704\ubcc0\uc870 \uc5ec\uad8c/\uc2e0\ubd84\uc99d \uac10\ubcc4 \uae30\uc220",
        "\uce74\uc9c0\ub178 \uace0\uac1d \uc9c0\uc6d0",
        "",
    )

    assert "\ucd9c\uc785 \ub85c\uadf8" in question
    assert "\uc2e0\ubd84 \ud655\uc778 \uae30\ub85d" in question
    assert "\uba54\ub274\ubcc4 \ud310\ub9e4\ub7c9" not in question
    assert "\uc2dd\uc7ac\ub8cc \uc7ac\uace0\ud45c" not in question


def test_method_followups_rotate_non_focus_probe_by_variant() -> None:
    focus = "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545"
    public_focus, _ = public_task_object(factor_name=focus, ksa_type="\uae30\uc220")

    first = _followups_for_method("\uacbd\ud5d8\uba74\uc811", "\ubb38\uc11c\uc791\uc131", focus, 3, variant_index=0)
    second = _followups_for_method("\uacbd\ud5d8\uba74\uc811", "\ubb38\uc11c\uc791\uc131", focus, 3, variant_index=1)

    assert len(first) == 3
    assert len(second) == 3
    assert first[0] == second[0]
    assert public_focus in first[1]
    assert public_focus in second[1]
    assert focus not in "\n".join([*first, *second])
    assert first[2] != second[2]


def test_focus_selection_prefers_skill_over_attitude_for_behavior_question() -> None:
    rows = [
        {"ncsClCd": "U1", "factorName": "문서 분류 기준 지식", "ksaTypeName": "지식"},
        {"ncsClCd": "U1", "factorName": "문서 오류 점검 능력", "ksaTypeName": "기술"},
        {"ncsClCd": "U1", "factorName": "꼼꼼하게 확인하려는 태도", "ksaTypeName": "태도"},
    ]

    assert _select_ksa_focus_for_method(rows, "U1", "경험면접") == "문서 오류 점검 능력"
    assert _select_ksa_focus_for_method(rows, "U1", "직무지식면접") == "문서 분류 기준 지식"


@pytest.mark.parametrize(
    ("method", "expected_focus"),
    [
        ("경험면접", "문서 오류 점검 능력"),
        ("상황면접", "꼼꼼하게 확인하려는 태도"),
        ("발표면접", "문서 분류 기준 지식"),
        ("토론면접", "꼼꼼하게 확인하려는 태도"),
        ("인바스켓면접", "꼼꼼하게 확인하려는 태도"),
        ("직무지식면접", "문서 분류 기준 지식"),
        ("창의적 문제해결력면접", "문서 분류 기준 지식"),
    ],
)
def test_focus_selection_uses_method_specific_observability_priority(
    method: str,
    expected_focus: str,
) -> None:
    rows = [
        {"ncsClCd": "U1", "factorName": "문서 분류 기준 지식", "ksaTypeName": "지식"},
        {"ncsClCd": "U1", "factorName": "문서 오류 점검 능력", "ksaTypeName": "기술"},
        {"ncsClCd": "U1", "factorName": "꼼꼼하게 확인하려는 태도", "ksaTypeName": "태도"},
    ]

    assert _select_ksa_focus_for_method(rows, "U1", method) == expected_focus


def test_non_work_sample_methods_do_not_prioritize_a_hands_on_skill() -> None:
    rows = [
        {"ncsClCd": "COOK-1", "factorName": "곁들임 메뉴 구성", "ksaTypeName": "지식"},
        {"ncsClCd": "COOK-1", "factorName": "면을 모양내어 담는 능력", "ksaTypeName": "기술"},
        {"ncsClCd": "COOK-1", "factorName": "관찰하는 태도", "ksaTypeName": "태도"},
    ]

    assert _select_ksa_focus_for_method(rows, "COOK-1", "인바스켓면접") == "관찰하는 태도"
    assert _select_ksa_focus_for_method(rows, "COOK-1", "창의적 문제해결력면접") == "곁들임 메뉴 구성"
    assert _select_ksa_focus_for_method(rows, "COOK-1", "경험면접") == "면을 모양내어 담는 능력"


def test_experience_template_expresses_attitude_as_observable_behavior() -> None:
    question = _question_for_method(
        "경험면접",
        "구조물해체 도면파악",
        "도면 숙지 의지",
        "구조물해체",
        "관련 도면을 보고 현장 상황을 파악하는 능력이다.",
        focus_type="태도",
    )
    follow_ups = _followups_for_method(
        "경험면접",
        "구조물해체 도면파악",
        "도면 숙지 의지",
        3,
        focus_type="태도",
    )

    assert "도면 숙지 행동 기준과 관련해 본인이 선택한 행동" in question
    assert "선택한 행동과 그 결과" in question
    assert "실제 경험 한 가지를 선택" in question
    assert "직무 경험이 없다면 본인 역할이 분명한 프로젝트나 교육실습 사례" in question
    assert "도면 숙지 의지를 적용" not in "\n".join([question, *follow_ups])
    assert "도면 숙지 행동 기준이 드러난 실제 행동과 감수한 상충비용" in follow_ups[1]


def test_discussion_template_expresses_attitude_as_joint_behavior_not_an_applied_noun() -> None:
    question = _question_for_method(
        "토론면접",
        "비품관리",
        "정확성 유지",
        "총무",
        "비품 기록의 정확성과 처리 속도를 조정한다.",
        focus_type="태도",
    )

    assert "정확성 준수 행동 기준을 지켜야 하는 가운데" in question
    assert "합의할 수 있다면 공통 실행안" in question
    assert "합의가 어렵다면 미합의 쟁점과 결정권자 이송 기준" in question
    assert "정확성 유지" not in question
    assert "태도를 어떻게 수행" not in question
    assert "'정확성 유지'를 포함" not in question


def test_natural_wording_gate_rejects_attitude_as_mechanical_application() -> None:
    q = {"question_focus": "도면 숙지 의지", "question_focus_type": "태도"}

    assert _natural_question_wording_ok(
        q,
        "도면 숙지 의지를 적용해 문제를 해결한 경험을 말씀해 주세요.",
        ["당시 본인 행동은 무엇이었습니까?"],
    ) is False


def test_natural_wording_gate_does_not_count_related_inside_ksa_label() -> None:
    focus = "도로교통 관련 법규"
    question = (
        "[창의적 문제해결력과제] 화물자동차운전 업무에서 '도로교통 관련 법규'를 판단 근거로 "
        "적용해야 하는 복합 문제가 발생했습니다. 주어진 운송 의뢰서와 배차표를 바탕으로 "
        "핵심 문제, 대안 2가지, 검증 방법과 실행계획을 설명해 주세요."
    )

    assert _natural_question_wording_ok(
        {"type": "창의적 문제해결력면접", "question_focus": focus, "question_focus_type": "지식"},
        question,
        [
            f"'{focus}'의 적용 범위와 예외를 어떻게 판별하겠습니까?",
            f"'{focus}'를 적용한 대안의 위험은 무엇입니까?",
            f"'{focus}'를 잘못 적용하지 않았음을 어떻게 검증하겠습니까?",
        ],
    ) is True


def test_creative_template_avoids_related_law_repetition() -> None:
    question = _question_for_method(
        "창의적 문제해결력면접",
        "화물자동차운전",
        "도로교통 관련 법규",
        "화물운송",
        "화물자동차를 안전하게 운행한다.",
        "지식",
    )

    assert "도로교통 규정 적용·판단 기준을 적용해야 하는 복합 문제" in question
    assert "도로교통 관련 법규" not in question
    assert "관련 법규'와 관련된" not in question


def test_presentation_template_asks_knowledge_scope_once_without_related_repetition() -> None:
    question = _question_for_method(
        "발표면접",
        "화물자동차운전",
        "도로교통 관련 법규",
        "화물운송",
        "운행 조건을 확인하고 안전하게 운전한다.",
        "지식",
    )

    assert "도로교통 규정 적용·판단 기준을 적용해 현황을 진단하는 데 필요한" in question
    assert "도로교통 관련 법규" not in question
    assert question.count("적용 범위·예외") == 1
    assert "관련 법규'와 관련된" not in question


def test_attitude_tasks_do_not_repeat_situation_interview_instruction() -> None:
    discussion = _question_for_method(
        "토론면접",
        "문서관리",
        "정확성 유지",
        "사무행정",
        "문서 품질을 확인한다.",
        "태도",
    )
    inbasket = _question_for_method(
        "인바스켓면접",
        "문서관리",
        "정확성 유지",
        "사무행정",
        "문서 품질을 확인한다.",
        "태도",
    )

    assert discussion.count("상충하는 요구와 압박 속에서") == 0
    assert "정확성 준수 행동 기준을 지켜야 하는 가운데" in discussion
    assert "정확성 유지" not in discussion
    assert inbasket.count("상충하는 요구와 압박 속에서도") == 1
    assert inbasket.count("정확성 준수 행동 기준이 드러나는") == 1
    assert "상충비용 또는 불이익" in inbasket


@pytest.mark.parametrize(
    ("method", "focus_type", "focus", "expected"),
    [
        ("경험면접", "지식", "문서 관리 규정", "적용 범위와 예외를 어떻게 판별"),
        ("상황면접", "지식", "문서 관리 규정", "범위와 예외를 어떻게 구분"),
        ("인바스켓면접", "지식", "문서 관리 규정", "기준·범위·예외"),
        ("창의적 문제해결력면접", "지식", "문서 관리 규정", "예외 가능성을 검증"),
        ("경험면접", "기술", "문서 오류 검증 기술", "순서·도구·조치와 산출물"),
        ("상황면접", "기술", "문서 오류 검증 기술", "순서·조치·산출물과 예상 위험"),
        ("인바스켓면접", "기술", "문서 오류 검증 기술", "분류할 절차와 산출물"),
        ("창의적 문제해결력면접", "기술", "문서 오류 검증 기술", "분석 절차·산출물과 검증 방법"),
        ("경험면접", "태도", "정확성 유지", "실제 행동과 감수한 상충비용"),
        ("상황면접", "태도", "정확성 유지", "드러난 구체적 행동과 예상 위험"),
        ("인바스켓면접", "태도", "정확성 유지", "드러나게 문서와 요청을 어떻게 분류"),
        ("창의적 문제해결력면접", "태도", "정확성 유지", "드러날 행동과 상충비용"),
    ],
)
def test_method_followups_operationalize_each_ksa_type(
    method: str,
    focus_type: str,
    focus: str,
    expected: str,
) -> None:
    follow_ups = _followups_for_method(
        method,
        "문서관리",
        focus,
        3,
        focus_type=focus_type,
    )

    assert expected in " | ".join(follow_ups)


@pytest.mark.parametrize(
    "question",
    [
        "문서 기준을 설명해 주세요.  또한 예외상황을 제시해 주세요.",
        "절차와 산출물을 설명하고 또한 오류 예방 방법을 말씀해 주세요.",
        "그 사례에서 소비자 패턴분석 능력을 직접 수행한 과정을 설명해 주세요.",
        "문서 오류 검증 기술을 수행한 단계와 산출물을 설명해 주세요.",
        "문서 보안 법규 지식을 판단 근거로 활용한 과정을 설명해 주세요.",
        (
            "문서관리 수행 중 오류가 발생하고 규정 변경 직후 구버전 양식이 혼재한 조건이고, "
            "검토 담당자는 증빙 완결을 우선하는 조건인 상황에서 본인의 행동과 결과를 설명해 주세요."
        ),
    ],
)
def test_natural_wording_gate_rejects_spacing_and_conjunction_artifacts(question: str) -> None:
    assert _natural_question_wording_ok(
        {"question_focus": "문서 기준 지식", "question_focus_type": "지식"},
        question,
        ["예외 판단 근거는 무엇입니까?"],
    ) is False


def test_discussion_focus_must_appear_in_the_conflict_not_only_as_a_trailing_label() -> None:
    q = {"question_focus": "기물 파지 및 운반 능력"}
    unrelated = (
        "[토론과제] 음식서비스 업무에서 위생·품질 기준 강화와 영업 효율이 충돌합니다. "
        "토론 후 합의안을 제시해 주세요. 합의 기준에는 기물 파지 및 운반 능력을 포함하세요."
    )
    grounded = (
        "[토론과제] 음식서비스 업무에서 기물별 안전한 파지·운반 절차와 처리 속도가 충돌합니다. "
        "토론 후 합의안을 제시해 주세요. 합의 기준에는 기물 파지 및 운반 능력을 포함하세요."
    )

    assert _focus_scenario_coherence_ok("토론면접", q, unrelated) is False
    assert _focus_scenario_coherence_ok("토론면접", q, grounded) is True


def test_discussion_template_grounds_handling_focus_in_the_conflict() -> None:
    question = _question_for_method(
        "토론면접",
        "음식서비스",
        "기물 파지 및 운반 능력",
        "음식서비스",
        "음식과 서비스를 제공하는 능력이다.",
        focus_type="기술",
    )

    assert "기물별 안전한 파지·운반 절차" in question
    assert _focus_scenario_coherence_ok(
        "토론면접",
        {"question_focus": "기물 파지 및 운반 능력"},
        question,
    ) is True


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
def test_official_style_evaluation_guide_has_distinct_behavior_anchors(method: str) -> None:
    guide = _behavior_anchored_evaluation(
        method,
        "문서 요구사항 파악",
        ["판단 근거", "실행 행동", "성과 확인", "위험 통제"],
    )

    assert _behavior_anchors_ok(guide) is True
    assert guide["dimensions"] == ["판단 근거", "실행 행동", "성과 확인", "위험 통제"]
    assert "anchors" not in guide
    assert guide["scale"] == "5단계 행동기반 평정"
    assert [(level["score"], level["label"]) for level in guide["rating_levels"]] == [
        (5, "탁월"),
        (4, "우수"),
        (3, "보통"),
        (2, "미흡"),
        (1, "부족"),
    ]


@pytest.mark.parametrize(
    ("focus_type", "focus", "high_marker", "low_marker"),
    [
        ("지식", "문서 보안 법규 지식", "적용 범위·예외", "암기해 말할 뿐"),
        ("기술", "문서 오류 검증 기술", "산출물의 품질", "기술 보유를 주장"),
        ("태도", "정확성을 우선하려는 태도", "압박이나 이해 충돌", "가치나 의지를 선언"),
    ],
)
def test_behavior_anchor_requires_distinct_observable_ksa_evidence(
    focus_type: str,
    focus: str,
    high_marker: str,
    low_marker: str,
) -> None:
    guide = _behavior_anchored_evaluation(
        "상황면접",
        focus,
        ["판단 근거", "실행 행동", "결과 확인", "위험 통제"],
        focus_type,
    )

    assert guide["focus_type"] == focus_type
    assert high_marker in guide["rating_levels"][0]["anchor"]
    assert low_marker in guide["rating_levels"][-1]["anchor"]
    assert "KSA 자기평가" in guide["interviewer_instruction"]
    assert _behavior_anchors_ok(guide) is True


def test_behavior_anchor_gate_rejects_label_only_scale() -> None:
    assert _behavior_anchors_ok(
        {
            "scale": "5단계 행동기반 평정",
            "anchors": {"high": "상", "medium": "중", "low": "하"},
            "interviewer_instruction": "점수를 선택하세요.",
        }
    ) is False


def test_behavior_anchor_gate_rejects_dual_scale_and_bad_sentence() -> None:
    guide = _behavior_anchored_evaluation(
        "토론면접",
        "승인된 변경에 대한 지식",
        ["초기 입장", "상대 근거 검토", "쟁점 조정", "실행 책임"],
        "지식",
    )
    guide["anchors"] = {"high": "상", "medium": "중", "low": "하"}
    assert _behavior_anchors_ok(guide) is False

    guide.pop("anchors")
    guide["rating_levels"][1]["anchor"] = "우수(4): 영향 검토 한 부분이 제한적이다"
    assert _behavior_anchors_ok(guide) is False
