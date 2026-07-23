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

    assert question["question"] == model_question
    assert question["question_source"] == "model"
    assert question["model_question_preserved"] is True
    assert question["model_replacement_reasons"] == []
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
            "[발표과제] 사무행정 문서작성 업무에서 문서 요구사항 파악 오류가 반복되는 자료가 주어졌다고 가정하고 준비시간 20분 후 현황을 진단하고 개선 대안을 5분 발표해 주세요. 발표에는 실행 계획, 성과지표, 5분 질의응답 답변을 포함하세요.",
            [
                "문서 요구사항 파악 오류 진단에 활용한 핵심 근거 자료는 무엇입니까?",
                "문서작성 개선 대안 중 우선순위를 가장 높게 둔 방안과 그 이유는 무엇입니까?",
                "반대 의견이 제기되면 어떻게 답변하고 성과지표를 보완하겠습니까?",
            ],
            ["자료 분석력", "논리적 구조화", "대안의 실행가능성", "실행계획과 성과지표", "질의응답 대응"],
        ),
        (
            "토론면접",
            "[토론과제] 사무행정 문서작성 업무에서 문서 요구사항 파악을 위한 보안 기준 강화 입장과 신속한 자료 공유 입장이 충돌합니다. 토론시간 20분 동안 1분 입장발표 후 반대 의견을 고려해 본인의 초기 입장과 최종 합의 기준을 제시해 주세요.",
            [
                "문서 요구사항 파악 관점에서 본인의 초기 입장을 뒷받침하는 핵심 근거는 무엇입니까?",
                "반대 의견 중 수용할 수 있는 부분은 무엇입니까?",
                "최종 합의안에 반드시 포함되어야 할 조정 기준은 무엇입니까?",
            ],
            ["입장발표 근거", "반대 의견 경청", "갈등 조정", "최종 합의안 도출"],
        ),
        (
            "인바스켓면접",
            "[인바스켓과제] 제한시간 30분 안에 사무행정 문서작성 요청, 자료 오류 정정 문서, 상급자 보고 요청이 동시에 들어왔습니다. 문서 요구사항 파악을 기준으로 우선순위와 보고, 위임, 직접처리 판단을 제시해 주세요.",
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

    assert preserved["question"] == question
    assert preserved["question_source"] == "model"
    assert preserved["model_question_preserved"] is True
    assert preserved["model_replacement_reasons"] == []
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

    assert question["question_source"] == "model_main_template_followups"
    assert question["question"] == model_question
    assert question["model_question_preserved"] is True
    assert "follow_up_quality" in question["model_replacement_reasons"]
    assert "'문서 요구사항 파악'과 관련해" in " | ".join(question["follow_ups"])


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
    assert question["question"] == model_question
    assert question["model_question_preserved"] is True
    assert question["model_followups_raw"] == raw_followups
    assert question["model_replacement_reasons"] == ["follow_up_focus_injected"]
    assert question["follow_ups"][0] == raw_followups[0]
    assert "'문서 요구사항 파악'과 관련해" in question["follow_ups"][1]
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

    assert question["question_source"] == "model_main_repaired_followups"
    assert "'도면 숙지 의지'를 발표 쟁점으로 볼 때" in question["follow_ups"][0]
    assert "구조물해체 도면파악 업무에서" in question["follow_ups"][0]
    assert raw_followups[0] in question["follow_ups"][0]
    assert question["follow_ups"][1:] == raw_followups[1:]
    assert quality["ready"] is True
    assert quality["issues"] == []


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

    assert question["question_source"] == "model"
    assert question["model_question_preserved"] is True
    assert question["model_replacement_reasons"] == []
    assert question["model_question_raw"] == model_question
    assert question["question"].startswith("[발표과제] 화물자동차운송운임산정 업무에서")
    assert model_question in question["question"]
    assert question["follow_ups"] == raw_followups
    assert quality["ready"] is True
    assert quality["issues"] == []


def test_adjust_questions_repairs_split_focus_and_context_in_experience_followups() -> None:
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

    assert question["question_source"] == "model_main_repaired_followups"
    assert question["question"] == model_question
    assert question["model_question_preserved"] is True
    assert question["model_followups_raw"] == raw_followups
    assert "'강점관점 개념'을 적용하는 과정에서 본인 행동과 선택 이유" in question["follow_ups"][1]
    assert "사회복지사례관리 실행계획 수립 과정" in question["follow_ups"][1]
    assert quality["ready"] is True
    assert quality["issues"] == []


def test_question_quality_accepts_inbasket_time_amount_followup_as_open_prompt() -> None:
    strategy = {
        "interview_questions": [
            {
                "type": "인바스켓면접",
                "competency": "화물자동차운행관리",
                    "ncsClCd": "0904010201_25v3",
                    "ncs_detail": "화물운송",
                    "question_focus": "화물취급지침 교육스킬",
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
                        "[인바스켓과제] 제한시간 안에 화물자동차운행관리 관련 여러 문서와 요청이 들어왔습니다. "
                        "화물취급지침 교육스킬을 기준으로 우선순위, 보고, 위임, 직접처리 판단을 제시해 주세요."
                    ),
                "follow_ups": [
                    "화물취급지침 교육스킬을 처리 기준으로 삼아 화물자동차운행관리 우선순위를 정한 이유는 무엇입니까?",
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
                "[인바스켓과제] 제한시간 안에 화물운송 관련 여러 문서와 요청이 들어왔습니다. "
                "화물취급지침 교육스킬을 기준으로 우선순위, 보고, 위임, 직접처리 판단을 제시해 주세요."
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
                "[인바스켓과제] 제한시간 안에 화물운송 관련 여러 문서와 요청이 들어왔습니다. "
                "화물취급지침 교육스킬을 기준으로 우선순위, 보고, 위임, 직접처리 판단을 제시해 주세요."
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

    assert item["question_source"] in {"model", "model_main_repaired_followups"}
    if item["question_source"] == "model_main_repaired_followups":
        assert item["model_replacement_reasons"] == ["follow_up_focus_injected"]
    else:
        assert item["model_replacement_reasons"] == []
    assert any(focus in follow_up for follow_up in item["follow_ups"])
    assert quality["ready"] is True
    assert quality["issues"] == []


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
    assert "'식재료 선별능력'을 기준" in question["question"]
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
    assert "'청소범위 설정능력'과 관련해" in question["question"]
    assert "'청소범위 설정능력'와" not in "\n".join([question["question"], *question["follow_ups"]])
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
    assert "'운임원가산정'과 관련해" in merged
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
        ("토론면접", "[토론과제]", "초기 입장", "최종 합의안 도출"),
        ("인바스켓면접", "[인바스켓과제]", "어떻게 분류", "우선순위 판단"),
        ("직무지식면접", "확인해야 할 절차", "기준이나 규정", "절차·기준 이해"),
        ("창의적 문제해결력면접", "[창의적 문제해결력과제]", "핵심 문제정의", "미래예측과 문제 정의"),
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
        json.dumps(
            ["행동형", "PT", "토의면접", "in-basket", "직무지식형", "창의적 문제해결력", "situational"],
            ensure_ascii=False,
        )
    )

    assert methods == ["경험면접", "발표면접", "토론면접", "인바스켓면접", "직무지식면접", "창의적 문제해결력면접", "상황면접"]


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
        "준비시간 20분 후 대안 2가지, 실행계획, 성과지표를 5분 발표하고 5분 질의응답 답변을 포함해 주세요."
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

    assert question["question_source"] == "model"
    assert question["model_question_raw"] == model_question
    assert question["question"].startswith("[발표과제] ")
    assert model_question in question["question"]
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
        "정보수집 기술을 기준으로 우선순위, 보고, 위임, 직접처리 판단을 제시해 주세요."
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

    assert question["question_source"] == "model"
    assert question["model_question_raw"] == model_question
    assert question["question"].startswith("[인바스켓과제] 제한시간 안에 ")
    assert model_question in question["question"]
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
        "문서작성 업무에서 문서 요구사항 파악을 적용했던 경험을 말씀해 주세요. "
        "당시 상황, 본인 역할, 선택한 행동, 결과와 학습을 포함해 설명해 주세요."
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
                        "당시 상황과 본인 역할은 무엇이었습니까?",
                        "문서 요구사항 파악을 적용하기 위해 어떤 행동을 선택했습니까?",
                        "결과와 학습은 무엇이었습니까?",
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

    assert question["question_focus"] == "문서 요구사항 파악"
    assert question["question_source"] == "model"
    assert question["model_replacement_reasons"] == []


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

    assert question["question_focus"] == "문서 요구사항 파악"
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
        "[토론과제] 토론시간 20분 동안 1분 입장발표 후 보안 강화 입장과 공유 효율 입장이 충돌하는 상황에서 반대 의견을 검토하고 최종 합의안을 제시해 주세요.",
    ) is True
    assert _method_shape_ok(
        "창의적 문제해결력면접",
        "[창의적 문제해결력과제] 미래예측 관점에서 문제를 정의하고 창의적 대안, 검증 방법, 실현가능성, 의사결정 기준, 실행계획을 제시해 주세요.",
    ) is True
    assert _method_shape_ok(
        "창의적 문제해결력면접",
        "[창의적 문제해결력과제] 문제를 정의하고 창의적 대안, 검증 방법, 실행계획을 제시해 주세요.",
    ) is False


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
