from __future__ import annotations

from app.services.question_generation import (
    _build_question_generation_prompt,
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
    assert "JSON" in prompt
    assert "\ufffd" not in prompt


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
    assert questions[0]["type"] == "\uc9c1\ubb34\uc9c0\uc2dd"
    assert questions[0]["ncsClCd"] == "0201010103_22v2"
    assert len(questions[0]["evaluation_points"]) == 4
    assert len(questions[0]["follow_ups"]) == 3
    assert questions[0]["ksa_refs"] == ["\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d"]


def test_parse_normalizes_missing_fields_with_clean_defaults():
    questions = _parse_openai_response('[{"question": "\uc9c8\ubb38\ub9cc \uc788\ub294 \uacbd\uc6b0"}]')

    assert len(questions) == 1
    assert questions[0]["type"] == "\uacbd\ud5d8"
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
