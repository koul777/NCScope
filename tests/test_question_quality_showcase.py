from __future__ import annotations

from difflib import SequenceMatcher
import json
import re

from app.main import (
    SUPPORTED_INTERVIEW_METHODS,
    _case_materials_sufficient_ok,
    _debate_case_neutrality_ok,
    _decision_authority_context_ok,
    _inbasket_authority_context_ok,
)
from scripts.export_question_quality_showcase import build_showcase


OFFICIAL_FACTOR = "승인된 변경에 대한 지식"


def _row() -> dict[str, str]:
    return {
        "major_code": "01",
        "major_name": "사업관리",
        "sub_name": "프로젝트관리",
        "unit_code": "0101010205_17v2",
        "unit_name": "프로젝트 인적자원관리",
        "unit_definition": "승인된 변경에 따라 프로젝트 인력과 역할을 조정하고 성과를 관리하는 능력이다.",
        "element_name": "프로젝트 팀 구성하기",
        "ksa_type": "지식",
        "factor_name": OFFICIAL_FACTOR,
        "ksa_no": "1",
    }


def test_exact_ksa_showcase_separates_internal_evidence_from_candidate_copy() -> None:
    report = build_showcase(_row())

    assert report["summary"] == {
        "method_count": len(SUPPORTED_INTERVIEW_METHODS),
        "ready_count": len(SUPPORTED_INTERVIEW_METHODS),
        "failed_count": 0,
        "passed": True,
    }
    assert report["source"]["official_factor"] == OFFICIAL_FACTOR

    for item in report["questions"]:
        candidate_text = json.dumps(item["candidate_view"], ensure_ascii=False)
        assert OFFICIAL_FACTOR not in candidate_text
        assert "승인된 변경에 대한" not in candidate_text
        assert item["traceability"]["public_task_object"] == "승인된 변경 관련 확인·판단 기준"
        assert item["traceability"]["evidence_id"]
        assert item["quality"]["ready"] is True
        assert item["quality"]["issues"] == []

    experience = next(item for item in report["questions"] if item["method"] == "경험면접")
    assert experience["quality"]["check_statuses"]["debate_case_neutrality"] == "not_applicable"
    assert experience["quality"]["check_statuses"]["case_materials_sufficient"] == "not_applicable"
    assert experience["quality"]["check_statuses"]["decision_authority_context"] == "not_applicable"
    assert experience["quality"]["check_statuses"]["behavior_anchored_evaluation"] == "pass"
    assert "필요했다면" not in experience["candidate_view"]["question"]
    assert "있었다면" not in experience["candidate_view"]["question"]


def test_exact_ksa_showcase_keeps_timing_out_of_substantive_task() -> None:
    report = build_showcase(_row())

    for item in report["questions"]:
        question = item["candidate_view"]["question"]
        assert "분 동안" not in question
        assert "분 이내" not in question

    debate = next(item for item in report["questions"] if item["method"] == "토론면접")
    assert debate["candidate_view"]["task_conditions"]["time_plan"] == [
        {"phase": "개별 입장발표", "minutes": 1},
        {"phase": "전체 토론", "minutes": 20},
    ]
    checks = debate["quality"]["checks"]
    assert checks["decision_dilemma_quality"] is True
    assert checks["debate_option_defensibility"] is True
    assert checks["debate_outcome_flexibility"] is True
    assert checks["operating_conditions_separated"] is True


def test_exact_ksa_showcase_supplies_decision_grade_case_materials() -> None:
    report = build_showcase(_row())
    material_methods = {"상황면접", "발표면접", "토론면접", "인바스켓면접", "창의적 문제해결력면접"}

    for item in report["questions"]:
        if item["method"] not in material_methods:
            continue
        conditions = item["candidate_view"]["task_conditions"]
        facts = conditions["case_facts"]
        rows = conditions["case_materials"]
        assert len(facts) >= 3
        assert len(facts) == len(set(facts))
        assert any(re.search(r"(?:\d|D\+|기한|마감|예정|건|명|시간|일정)", fact) for fact in facts)
        assert len(rows) >= 3
        assert all(set(row) == {"source", "field", "value"} and all(row.values()) for row in rows)
        assert item["quality"]["checks"]["case_materials_sufficient"] is True

    debate = next(item for item in report["questions"] if item["method"] == "토론면접")
    debate_text = "\n".join(
        [
            debate["candidate_view"]["question"],
            *debate["candidate_view"]["task_conditions"]["case_facts"],
        ]
    )
    assert len(debate["candidate_view"]["question"]) <= 440
    assert "D+3" in debate_text
    assert "D+5" in debate_text
    assert "36인시" in debate_text
    assert "사전 승인 필요" in debate_text
    assert "명시되지 않음" in debate_text
    assert "승인 전 허용:" not in debate_text
    assert debate["quality"]["checks"]["debate_case_neutrality"] is True

    situation = next(item for item in report["questions"] if item["method"] == "상황면접")
    situation_question = situation["candidate_view"]["question"]
    situation_conditions = situation["candidate_view"]["task_conditions"]
    assert "불명확" not in situation_question
    assert "명시되지" not in situation_question
    assert any(row["field"] == "해석 공백" for row in situation_conditions["case_materials"])

    for method in {"상황면접", "토론면접", "인바스켓면접", "창의적 문제해결력면접"}:
        item = next(row for row in report["questions"] if row["method"] == method)
        conditions = item["candidate_view"]["task_conditions"]
        assert "업무분장표" in conditions["provided_materials"]
        assert "전결규정" in conditions["provided_materials"]
        assert _decision_authority_context_ok(method, conditions) is True
        assert item["quality"]["check_statuses"]["decision_authority_context"] == "pass"

    inbasket = next(item for item in report["questions"] if item["method"] == "인바스켓면접")
    inbasket_conditions = inbasket["candidate_view"]["task_conditions"]
    assert "업무분장표" in inbasket_conditions["provided_materials"]
    assert "전결규정" in inbasket_conditions["provided_materials"]
    assert inbasket["quality"]["checks"]["inbasket_authority_context"] is True


def test_exact_ksa_showcase_uses_distinct_behavior_anchors() -> None:
    report = build_showcase(_row())

    for item in report["questions"]:
        points = item["interviewer_view"]["evaluation_points"]
        levels = item["interviewer_view"]["assessment_guide"]["rating_levels"]
        assert 4 <= len(points) <= 5
        assert [level["score"] for level in levels] == [5, 4, 3, 2, 1]
        anchors = [level["anchor"] for level in levels]
        assert all(
            SequenceMatcher(None, anchors[index], anchors[index + 1]).ratio() < 0.82
            for index in range(len(anchors) - 1)
        )


def test_human_panel_gates_reject_title_only_materials_and_answer_leakage() -> None:
    title_only = {
        "provided_materials": ["변경요청서", "일정·원가 영향자료"],
        "case_facts": ["변경심의 D+3", "기존 마감 D+5", "핵심 인력 1명"],
    }
    assert _case_materials_sufficient_ok("발표면접", title_only) is False

    leaked = {
        **title_only,
        "case_facts": [
            "승인 전 허용: 영향분석·가용성 확인",
            "변경심의 D+3",
            "기존 마감 D+5",
        ],
    }
    debate_question = (
        "[토론과제] 승인된 변경을 다루면서 영향분석을 보류하자는 입장과 "
        "영향분석을 수행하자는 입장이 충돌합니다. 합의가 어렵다면 미합의 쟁점을 이송하세요."
    )
    assert _debate_case_neutrality_ok("토론면접", debate_question, leaked) is False
    leaked_without_label = {
        **title_only,
        "case_facts": [
            "사전 승인 없이 영향분석 가능",
            "변경심의 D+3",
            "기존 마감 D+5",
        ],
    }
    assert _debate_case_neutrality_ok("토론면접", debate_question, leaked_without_label) is False
    assert _decision_authority_context_ok("상황면접", title_only) is False
    assert _inbasket_authority_context_ok("인바스켓면접", title_only) is False
