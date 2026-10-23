"""Experience-interview template wording fixed after the interview-panel review.

These are template-level regressions: they render the deterministic
experience prompts directly instead of relying on the quality checks.
"""

from __future__ import annotations

import re

import pytest

import json

from app.main import (
    _QUESTION_VARIATION_CONSTRAINTS,
    _adjust_generated_questions,
    _parse_question_plan_json,
    _experience_scenario_condition,
    _followups_for_method,
    _question_for_method,
    _question_variation_constraint,
)

SUBJECT = "공적개발원조사업 총괄운영관리"
DETAIL = "공적개발원조"
DEFINITION = "공적개발원조사업을 총괄 운영한다."
CASES = (
    ("지식", "자국의 대외정책, 공적개발원조정책, 국별협력전략"),
    ("기술", "프로젝트 관리 영역들 간의 관계를 조율하는 능력"),
    ("기술", "수요예측 기법"),
    ("태도", "원활한 커뮤니케이션을 하고자 하는 적극적인 태도"),
)
VARIATION_INDEXES = (
    *range(1, len(_QUESTION_VARIATION_CONSTRAINTS)),
    19,
    27,
    170,
    500,
    1170,
)
ROLE_PROBLEM_FOLLOW_UP = "당시 어떤 문제가 있었고, 본인은 어떤 역할을 맡았습니까?"
SUBSTITUTE_NOTICE = "해당 경험이 없으면 면접위원이 모든 지원자에게 동일한 대체상황을 드립니다."


def _question(focus_type: str, focus: str, index: int = 0, **kwargs) -> str:
    return _question_for_method(
        "경험면접",
        SUBJECT,
        focus,
        DETAIL,
        DEFINITION,
        focus_type,
        variation_index=index,
        **kwargs,
    )


@pytest.mark.parametrize(("focus_type", "focus"), CASES)
def test_variation_only_guides_the_choice_of_one_case(focus_type: str, focus: str) -> None:
    assert len(VARIATION_INDEXES) == 23  # all 18 direct conditions and 5 combined forms
    for index in VARIATION_INDEXES:
        question = _question(focus_type, focus, index)
        condition = _experience_scenario_condition(_question_variation_constraint(index))

        assert f"가능하면 {condition}처럼, 비슷한 일을 겪은 경험을 골라 주세요." in question, index
        # ``비슷한`` qualifies the chosen case, never the condition noun.
        assert re.findall(r"비슷한\s*(\S+)", question) == ["일을"], index
        assert f"비슷한 {condition}" not in question
        # One question asks for one case only.
        assert question.count("한 가지") == 1
        for phrase in ("함께 설명", "대응했다면", "두 가지", "사례를 각각"):
            assert phrase not in question, (index, phrase)


@pytest.mark.parametrize(
    ("focus", "verb", "means"),
    [
        ("프로젝트 관리 영역들 간의 관계를 조율하는 능력", "맡은", "자료나 방법"),
        ("갈등중재기술", "맡은", "자료나 방법"),
        ("수요예측 기법", "수행한", "도구"),
        ("문서관리 시스템", "수행한", "도구"),
    ],
)
def test_skill_question_asks_for_the_past_action_not_procedure_following(
    focus: str,
    verb: str,
    means: str,
) -> None:
    for index in (0, 7, 19):
        question = _question("기술", focus, index)

        assert "절차를 따라" not in question
        assert "따라 직접" not in question
        if verb == "맡은":
            # Coordination skills are asked as the coordinating act.
            assert re.search(rf"직접 맡은 .+ (?:조율|조정)에서 한 행동의 순서와 그때 쓴 {means}, ", question), question
            assert "검증 절차" not in question
            assert "을 직접 수행한" not in question
        else:
            assert re.search(rf"를 직접 {verb} 행동의 순서와 그때 쓴 {means}, ", question), question
        if means == "자료나 방법":
            assert "그때 쓴 도구" not in question


def test_attitude_question_requires_pressure_and_asks_for_actual_behavior() -> None:
    focus = "원활한 커뮤니케이션을 하고자 하는 적극적인 태도"
    for index in (0, 3, 14, 27):
        question = _question("태도", focus, index)

        assert "상충하는 요구나 압박을 받았던 장면을 골라" in question
        assert "본인이 실제로 보인 원활한 커뮤니케이션을 실행하는 적극적인 행동과 그 결과를 설명해 주세요" in question
        # The surface is the behaviour itself: no "행동 기준" and no
        # "…행동이 드러난 … 행동" repetition.
        assert "행동 기준" not in question
        assert "행동이 드러난" not in question
        assert "행동 기준을 지키" not in question
        assert "도 있었던" not in question
        assert "행동 기준과 관련해" not in question


def test_attitude_probe_asks_what_was_given_up_and_how_others_reacted() -> None:
    follow_ups = _followups_for_method(
        "경험면접",
        SUBJECT,
        "원활한 커뮤니케이션을 하고자 하는 적극적인 태도",
        3,
        focus_type="태도",
    )

    assert "행동을 보이느라 포기하거나 감수한 것은 무엇이었고, 상대는 어떻게 반응했습니까?" in follow_ups[1]
    assert "행동 기준" not in follow_ups[1]


def test_knowledge_question_and_probe_do_not_ask_the_same_thing() -> None:
    focus = "자국의 대외정책, 공적개발원조정책, 국별협력전략"
    question = _question("지식", focus, 0)
    follow_ups = _followups_for_method("경험면접", SUBJECT, focus, 3, focus_type="지식")

    assert "무엇을 판단 근거로 삼았고, 그 근거로 무엇을 결정해 어떻게 행동했는지" in question
    assert "적용 범위" not in question
    assert "예외" not in question
    assert "실제로 적용해 어떤 자료를 확인하고" not in question
    assert "적용한 범위와 예외로 둔 경우" in follow_ups[1]
    assert "판단 근거로 삼았" not in follow_ups[1]


@pytest.mark.parametrize(("focus_type", "focus"), CASES)
def test_role_and_problem_follow_up_is_the_fixed_first_slot(focus_type: str, focus: str) -> None:
    for count in range(0, 6):
        for variant_index in range(0, 13):
            follow_ups = _followups_for_method(
                "경험면접",
                SUBJECT,
                focus,
                count,
                variant_index=variant_index,
                focus_type=focus_type,
            )

            assert follow_ups, (count, variant_index)
            assert follow_ups[0] == ROLE_PROBLEM_FOLLOW_UP
            assert follow_ups.count(ROLE_PROBLEM_FOLLOW_UP) == 1


@pytest.mark.parametrize(("focus_type", "focus"), CASES)
def test_substitute_situation_notice_names_who_gives_it(focus_type: str, focus: str) -> None:
    for index in (0, 5, 19):
        question = _question(focus_type, focus, index)

        assert SUBSTITUTE_NOTICE in question
        assert "동일한 대체상황" in question
        assert "사례가 없으면 동일한 대체상황을 제시합니다" not in question


def test_source_anchored_experience_question_stays_within_the_length_limit() -> None:
    job_context = (
        "[주요업무] 공적개발원조사업 총괄운영관리 업무로 국별협력전략에 따른 사업 예산 집행과 "
        "성과지표를 점검하고 협력기관 산출물의 품질을 관리하며 관계부처 협의 결과를 정리함"
    )
    for focus_type, focus in CASES:
        for index in range(0, 1300):
            question = _question(focus_type, focus, index, job_context_text=job_context)

            assert "공고·직무기술서 근거" in question, (focus_type, index)
            assert len(question) <= 360, (focus_type, index, len(question))
            assert SUBSTITUTE_NOTICE in question
            assert question.endswith("결과를 설명해 주세요.")
            assert question.count("한 가지") == 1


def test_skill_probe_does_not_presume_something_went_wrong() -> None:
    for focus in ("프로젝트 관리 영역들 간의 관계를 조율하는 능력", "수요예측 기법"):
        follow_ups = _followups_for_method("경험면접", SUBJECT, focus, 3, focus_type="기술")

        assert follow_ups[1].endswith("예상대로 되지 않은 단계가 있었다면 무엇이었고, 어떻게 바로잡았습니까?")
        assert "산출물을 바로잡" not in follow_ups[1]
        assert "단계는 무엇이었고" not in follow_ups[1]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("경험면접", [ROLE_PROBLEM_FOLLOW_UP]),
        ("상황면접", []),
        ("발표면접", []),
    ],
)
def test_zero_follow_up_plan_still_keeps_the_experience_role_slot(
    method: str,
    expected: list[str],
) -> None:
    # API callers can pass a plan whose follow-up count is already 0.
    plan = _parse_question_plan_json(
        json.dumps({"items": [{"detail": "사무행정", "enabled": True, "main_count": 1}]}),
        ["사무행정"],
    )
    plan["follow_up_count"] = 0
    for planned in [*plan["items"], *plan["selected_items"], *plan["question_sequence"]]:
        planned["follow_up_count"] = 0
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

    assert out["interview_questions"][0]["follow_ups"] == expected


def test_direct_zero_count_call_keeps_only_the_experience_role_slot() -> None:
    focus = "수요예측 기법"
    assert _followups_for_method("경험면접", SUBJECT, focus, 0, focus_type="기술") == [
        ROLE_PROBLEM_FOLLOW_UP
    ]
    assert _followups_for_method("상황면접", SUBJECT, focus, 0, focus_type="기술") == []
