from __future__ import annotations

from app.services.question_generation import (
    _build_question_generation_prompt,
    _contains_blind_hiring_cue,
    _parse_openai_response,
)


def test_prompt_includes_ncs_ksa_and_clean_korean_rules():
    prompt = _build_question_generation_prompt(
        ncs_matches=[
            {
                "ncsClCd": "0201010103_22v2",
                "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
                "compeUnitDef": "\uacbd\uc601\ubaa9\ud45c\ub97c \uc218\ub9bd\ud55c\ub2e4",
            }
        ],
        ncs_ksa=[
            {
                "factorName": "\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d",
                "factorSource": "ncs-mcp",
                "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
            }
        ],
        jd_text="\uc138\ubd84\ub958: \uacbd\uc601\uae30\ud68d",
        mode="ksa_driven",
        target_count=5,
    )

    assert "\uc544\ub798 \ucee8\ud14d\uc2a4\ud2b8" in prompt
    assert "\uc0dd\uc131 \uac1c\uc218: 5" in prompt
    assert "0201010103_22v2" in prompt
    assert "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd" in prompt
    assert "\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d" in prompt
    assert "ncs-mcp" in prompt
    assert "factorName 원문" in prompt
    assert "question과 follow_ups 중 지정 위치" in prompt
    assert "글자 그대로 'KSA'라고 쓰지 말고" in prompt
    assert "첫 항목은 question에 직접 쓴 주 검증 초점" in prompt
    assert "JSON" in prompt
    assert "\ufffd" not in prompt


def test_prompt_describes_all_supported_interview_methods():
    prompt = _build_question_generation_prompt(
        ncs_matches=[],
        ncs_ksa=[],
        mode="diverse",
        target_count=6,
        extra_context="",
    )

    for method in ["경험면접", "상황면접", "발표면접", "토론면접", "창의적 문제해결력면접", "인바스켓면접", "직무지식면접"]:
        assert method in prompt
    assert "STAR 방식" in prompt
    assert "제한시간 안에 여러 문서" in prompt
    assert "절차, 기준, 산출물" in prompt
    assert "[주질문 필수어]" in prompt
    assert "경험, 상황, 본인, 행동, 결과" in prompt
    assert "미래예측" in prompt
    assert "문제정의, 원인 가설, 창의적 대안" in prompt
    assert "인바스켓, 제한시간, 문서, 우선순위, 보고, 위임, 직접처리" in prompt
    assert "[꼬리질문 품질 기준]" in prompt
    assert "최소 1개는 직무/NCS/KSA 핵심어" in prompt
    assert "발표면접, 토론면접, 인바스켓면접, 직무지식면접은 follow_ups[0]" in prompt
    assert "경험면접, 상황면접, 창의적 문제해결력면접은 follow_ups[1]" in prompt
    assert "[KSA 원문 보존 예시]" in prompt
    assert '"type": "경험면접|상황면접|발표면접|토론면접|창의적 문제해결력면접|인바스켓면접|직무지식면접"' in prompt


def test_parse_valid_interview_questions_object():
    response = """
    {
      "interview_questions": [
        {
          "question": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd \uc2dc \uc2dc\uc7a5\ud658\uacbd\uc744 \uc5b4\ub5bb\uac8c \ubd84\uc11d\ud558\uaca0\uc2b5\ub2c8\uae4c?",
          "type": "\uc9c1\ubb34\uc9c0\uc2dd",
          "competency": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
          "ncsClCd": "0201010103_22v2",
          "evaluation_points": ["\uc2dc\uc7a5 \uc774\ud574", "\uadfc\uac70 \uc81c\uc2dc", "\ub300\uc548 \ube44\uad50", "\uc2e4\ud589 \uacc4\ud68d"],
          "follow_ups": ["\ubd84\uc11d \uadfc\uac70\ub294?", "\uc704\ud5d8\uc694\uc778\uc740?", "\uc131\uacfc\ub294?"],
          "ksa_refs": ["\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d"]
        }
      ]
    }
    """

    questions = _parse_openai_response(response)

    assert len(questions) == 1
    assert questions[0]["type"] == "\uc9c1\ubb34\uc9c0\uc2dd\uba74\uc811"
    assert questions[0]["ncsClCd"] == "0201010103_22v2"
    assert len(questions[0]["evaluation_points"]) == 4
    assert len(questions[0]["follow_ups"]) == 3
    assert questions[0]["ksa_refs"] == ["\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d"]


def test_parse_normalizes_missing_fields_with_clean_defaults():
    questions = _parse_openai_response('[{"question": "\uc9c8\ubb38\ub9cc \uc788\ub294 \uacbd\uc6b0"}]')

    assert len(questions) == 1
    assert questions[0]["type"] == "\uacbd\ud5d8\uba74\uc811"
    assert questions[0]["competency"] == ""
    assert len(questions[0]["evaluation_points"]) == 4
    assert len(questions[0]["follow_ups"]) == 3
    assert "\ufffd" not in "\n".join(questions[0]["evaluation_points"])
    assert "\ufffd" not in "\n".join(questions[0]["follow_ups"])


def test_parse_handles_markdown_json_block():
    response = """
    ```json
    [
      {
        "question": "\uc9c8\ubb381",
        "type": "\uc0c1\ud669",
        "competency": "\uc5ed\ub7c9",
        "evaluation_points": [],
        "follow_up": "\uaf2c\ub9ac\uc9c8\ubb38"
      }
    ]
    ```
    """

    questions = _parse_openai_response(response)

    assert len(questions) == 1
    assert questions[0]["question"] == "\uc9c8\ubb381"
    assert questions[0]["follow_ups"][0] == "\uaf2c\ub9ac\uc9c8\ubb38"


def test_parse_deduplicates_identical_questions():
    response = """
    [
      {"question": "\uac19\uc740 \uc9c8\ubb38", "type": "\uacbd\ud5d8"},
      {"question": "\uac19\uc740 \uc9c8\ubb38", "type": "\uc0c1\ud669"}
    ]
    """

    questions = _parse_openai_response(response)

    assert len(questions) == 1


def test_parse_falls_back_for_unsupported_interview_type():
    questions = _parse_openai_response('[{"question": "절차를 어떻게 확인하겠습니까?", "type": "가치·태도형"}]')

    assert len(questions) == 1
    assert questions[0]["type"] == "경험면접"


def test_parse_accepts_creative_problem_solving_alias():
    questions = _parse_openai_response(
        '[{"question": "복합 문제의 원인과 대안을 어떻게 검증하겠습니까?", "type": "creative_problem_solving"}]'
    )

    assert len(questions) == 1
    assert questions[0]["type"] == "창의적 문제해결력면접"


def test_parse_uses_method_specific_defaults_for_partial_items():
    questions = _parse_openai_response(
        """
        [
          {
            "question": "[발표과제] 자료를 분석하고 대안을 발표해 주세요.",
            "type": "발표면접",
            "follow_ups": ["분석 근거는 무엇입니까?"],
            "evaluation_points": ["자료 분석력"]
          }
        ]
        """
    )

    assert len(questions) == 1
    assert questions[0]["type"] == "발표면접"
    assert len(questions[0]["follow_ups"]) == 3
    assert any("질의응답" in item for item in questions[0]["follow_ups"])
    assert "가장 어려웠던 지점" not in "\n".join(questions[0]["follow_ups"])
    assert "질의응답 대응" in questions[0]["evaluation_points"]


def test_parse_drops_blind_hiring_cues():
    response = """
    [
      {"question": "출신학교와 가족 배경을 포함해 설명해 주세요.", "type": "경험면접"},
      {"question": "문서 요구사항을 어떻게 확인하겠습니까?", "type": "직무지식형"}
    ]
    """

    questions = _parse_openai_response(response)

    assert len(questions) == 1
    assert questions[0]["question"] == "문서 요구사항을 어떻게 확인하겠습니까?"
    assert questions[0]["type"] == "직무지식면접"


def test_parse_drops_extended_blind_hiring_cues():
    blocked = [
        "생년월일을 말씀해 주세요.",
        "현재 몇 살인지 설명해 주세요.",
        "군필 여부와 미필 사유를 말씀해 주세요.",
        "기혼 여부를 포함해 설명해 주세요.",
    ]
    response = [
        {"question": question, "type": "경험면접"}
        for question in blocked
    ] + [
        {"question": "문서 요구사항을 확인하는 절차를 설명해 주세요.", "type": "직무지식면접"}
    ]

    questions = _parse_openai_response(__import__("json").dumps(response, ensure_ascii=False))

    assert all(_contains_blind_hiring_cue(question) for question in blocked)
    assert len(questions) == 1
    assert questions[0]["question"] == "문서 요구사항을 확인하는 절차를 설명해 주세요."


def test_blind_hiring_filter_does_not_match_key_syllable_inside_ordinary_words():
    response = """
    [
      {"question": "고객요구를 만족시키기 위해 어떤 기준을 확인하겠습니까?", "type": "상황면접"},
      {"question": "키가 큰 지원자가 유리한 이유를 설명해 주세요.", "type": "경험면접"}
    ]
    """

    questions = _parse_openai_response(response)

    assert len(questions) == 1
    assert "만족시키기" in questions[0]["question"]
