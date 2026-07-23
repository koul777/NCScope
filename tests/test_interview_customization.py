import json

from app.main import _adjust_generated_questions, _parse_interview_methods, _parse_question_plan_json


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
    assert out["question_customization_policy"] == "guidebook_method_templates_and_exact_counts"


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
