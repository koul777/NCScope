import json

import pytest

from app.main import (
    _adjust_generated_questions,
    _attach_ksa_evidence_to_strategy,
    _attach_question_quality_report,
    _method_evaluation_points,
    _method_shape_ok,
    _official_sample_format_ok,
    _parse_interview_methods,
    _parse_question_plan_json,
)


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


def test_adjust_questions_preserves_model_question_when_method_shape_is_valid() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    model_question = (
        "[토론과제] 사무행정 업무에서 문서 보안 기준 준수와 신속한 자료 공유 입장이 충돌합니다. "
        "반대 의견을 고려해 본인의 초기 입장과 합의 기준을 제시해 주세요."
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

    assert question["question"] == model_question
    assert question["question_source"] == "model"
    assert question["model_question_preserved"] is True
    assert question["model_replacement_reasons"] == []
    assert question["type"] == "토론면접"
    assert question["ncs_detail"] == "사무행정"


def test_adjust_questions_replaces_model_question_when_followups_are_generic() -> None:
    plan = _parse_question_plan_json(
        json.dumps(
            {"items": [{"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}]},
            ensure_ascii=False,
        ),
        ["사무행정"],
    )
    model_question = (
        "문서작성 업무에서 자료 오류와 일정 지연이 동시에 발생한 상황입니다. "
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

    assert question["question_source"] == "template_fallback"
    assert question["model_question_preserved"] is False
    assert "follow_up_quality" in question["model_replacement_reasons"]
    assert "'문서 요구사항 파악'와 관련해" in " | ".join(question["follow_ups"])


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
    assert "조리 일정 변경 문서가 동시에" in question["question"]
    assert "문서이 동시에" not in question["question"]
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
    assert "자료 오류" not in question["question"]


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
    assert "화주 요청 변경 내역이 주어졌다고" in merged
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
    assert "제한시간" in question["question"]
    assert len(question["follow_ups"]) == 3


@pytest.mark.parametrize(
    ("method", "question_marker", "followup_marker", "evaluation_marker"),
    [
        ("경험면접", "경험을 말씀해 주세요", "맡은 역할", "본인 역할과 행동"),
        ("상황면접", "어떤 기준으로 판단", "먼저 확인해야 할 사실", "판단 기준"),
        ("발표면접", "[발표과제]", "핵심 근거 자료", "논리적 구조화"),
        ("토론면접", "[토론과제]", "초기 입장", "합의안 도출"),
        ("인바스켓면접", "[인바스켓과제]", "어떻게 분류", "우선순위 판단"),
        ("직무지식면접", "확인해야 할 절차", "기준이나 규정", "절차·기준 이해"),
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
    assert evaluation_marker in question["evaluation_points"]
    assert question["ncs_detail"] == "사무행정"
    assert question["ncsClCd"] == "0202030201_25v3"


def test_parse_interview_methods_canonicalizes_aliases_and_preserves_order() -> None:
    methods = _parse_interview_methods(
        json.dumps(["행동형", "PT", "토의면접", "in-basket", "직무지식형", "situational"], ensure_ascii=False)
    )

    assert methods == ["경험면접", "발표면접", "토론면접", "인바스켓면접", "직무지식면접", "상황면접"]


def test_parse_interview_methods_defaults_to_all_supported_methods() -> None:
    methods = _parse_interview_methods("")

    assert methods == ["경험면접", "상황면접", "발표면접", "토론면접", "인바스켓면접", "직무지식면접"]


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
    strategy = {
        "interview_questions": [
            {
                "type": "인바스켓면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "ncs_detail": "사무행정",
                "question": "[인바스켓과제] 제한시간 30분 안에 여러 문서와 보고 요청이 들어왔습니다. 문서 요구사항 파악을 기준으로 처리 우선순위와 보고, 위임, 직접처리 판단 및 첫 조치 계획을 제시해 주세요.",
                "follow_ups": [
                    "여러 문서와 요청을 어떤 기준으로 분류하겠습니까?",
                    "가장 먼저 처리할 항목과 보류할 항목은 무엇입니까?",
                    "상급자 보고, 위임, 직접 처리 중 어떤 방식을 선택하겠습니까?",
                    "후속 확인과 기록은 어떻게 남기겠습니까?",
                ],
                "evaluation_points": ["우선순위 판단", "문서·요청 분류", "시간관리", "리스크 대응"],
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

    out = _attach_question_quality_report(strategy)
    report = out["question_quality_report"]

    assert report["passed"] is True
    assert report["summary"]["ready_count"] == 1
    assert report["items"][0]["ready"] is True
    assert report["items"][0]["checks"]["main_question_method_shape"] is True
    assert report["items"][0]["checks"]["official_sample_format"] is True
    assert report["items"][0]["issues"] == []


def test_official_sample_format_check_requires_method_specific_evaluation_points() -> None:
    assert _official_sample_format_ok(
        "발표면접",
        "[발표과제] 문서작성 업무에서 요구사항 분석 개선 대안 2가지를 발표하고 성과지표를 제시해 주세요.",
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
                "question": "[발표과제] 문서작성 업무에서 개선 대안 2가지를 발표하고 성과지표를 제시해 주세요.",
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
                "question": "[발표과제] 사무행정 문서작성 업무에서 문서 요구사항 오류 자료가 주어졌다고 가정하고 현황을 진단하고 개선 대안을 발표해 주세요. 발표에는 대안 2가지, 실행 우선순위, 성과지표를 포함하세요.",
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


def test_method_evaluation_points_reserve_ksa_slot_for_full_default_methods() -> None:
    points = _method_evaluation_points("발표면접", ["문서 요구사항 파악"])

    assert len(points) == 6
    assert "문서 요구사항 파악 적용 근거" in points
    assert "자료 분석력" in points
    assert "성과지표 설계" in points


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
