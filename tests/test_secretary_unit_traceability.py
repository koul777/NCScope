from __future__ import annotations

import json
from pathlib import Path

from app.main import (
    _attach_ksa_evidence_to_strategy,
    _extract_all_generated_question_items,
    _group_interview_questions_for_response,
    _preserve_exact_complete_units_for_upload,
    _select_units_for_question_plan,
)


DETAIL_CODE = "02020301"
DETAIL_NAME = "비서"
CATALOG_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "ncs_unit_catalog.json"


def _secretary_units() -> list[dict[str, object]]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    rows = [
        row
        for row in catalog["units"]
        if row.get("detail_code") == DETAIL_CODE
    ]
    return [
        {
            "ncsClCd": row["code"],
            "officialUnitBaseCode": row["base_code"],
            "compeUnitName": row["name"],
            "officialUnitName": row["name"],
            "officialDetailCode": DETAIL_CODE,
            "officialDetailName": DETAIL_NAME,
            "matchedDetailName": DETAIL_NAME,
            "ncsSubdCdnm": DETAIL_NAME,
            "score": 1.0,
            "matched_keywords": [DETAIL_NAME],
        }
        for row in rows
    ]


def _exact_bundle(units: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "exact_complete",
        "eligible_for_generation": True,
        "units": units,
    }


def test_secretary_catalog_contains_eleven_distinct_official_units() -> None:
    units = _secretary_units()

    assert len(units) == 11
    assert len({row["officialUnitBaseCode"] for row in units}) == 11
    assert all(str(row["officialUnitBaseCode"]).startswith(DETAIL_CODE) for row in units)


def test_exact_complete_upload_preserves_all_units_after_top_k_ranking() -> None:
    units = _secretary_units()
    ranked = [dict(units[3]), dict(units[1]), dict(units[8])]

    preserved = _preserve_exact_complete_units_for_upload(ranked, _exact_bundle(units))

    assert [row["ncsClCd"] for row in preserved[:3]] == [
        row["ncsClCd"] for row in ranked
    ]
    assert len(preserved) == 11
    assert {row["ncsClCd"] for row in preserved} == {row["ncsClCd"] for row in units}

    non_exact = _preserve_exact_complete_units_for_upload(
        ranked,
        {"status": "incomplete", "eligible_for_generation": False, "units": units},
    )
    assert [row["ncsClCd"] for row in non_exact] == [row["ncsClCd"] for row in ranked]


def test_eleven_slot_plan_can_assign_every_secretary_unit_once() -> None:
    units = _secretary_units()
    preserved = _preserve_exact_complete_units_for_upload(units[:5], _exact_bundle(units))
    plan = {
        "selected_terms": [DETAIL_NAME],
        "question_sequence": [
            {"detail": DETAIL_NAME, "type": "경험면접", "follow_up_count": 3}
            for _ in range(11)
        ],
    }

    selected = _select_units_for_question_plan(plan, preserved)

    assert len(selected) == 11
    assert {row["ncsClCd"] for row in selected} == {row["ncsClCd"] for row in units}


def test_all_secretary_units_keep_question_probe_rubric_trace_in_exports() -> None:
    units = _secretary_units()
    questions: list[dict[str, object]] = []
    evidence_rows: list[dict[str, object]] = []
    for index, unit in enumerate(units, start=1):
        factor = f"{unit['compeUnitName']} 수행 절차 확인"
        questions.append(
            {
                "type": "경험면접",
                "method": "경험면접",
                "question": f"{unit['compeUnitName']} 업무를 수행한 실제 경험을 설명해 주세요.",
                "follow_ups": [
                    "당시 상황과 본인의 역할은 무엇이었습니까?",
                    "판단 기준에 따라 직접 한 행동은 무엇입니까?",
                    "결과를 어떤 기록으로 확인했습니까?",
                ],
                "evaluation_points": [
                    "당시 상황과 본인 역할",
                    "자료·도구를 사용한 수행 순서",
                    "선택 근거와 직접 행동",
                    "결과를 입증하는 기록",
                ],
                "question_focus": factor,
                "question_focus_type": "기술",
                "question_focus_surface": str(unit["compeUnitName"]),
                "question_source": "deterministic",
                "ncsClCd": unit["ncsClCd"],
                "competency": unit["compeUnitName"],
                "ncs_detail": DETAIL_NAME,
                "ksa_refs": [factor],
            }
        )
        evidence_rows.append(
            {
                "ncsClCd": unit["ncsClCd"],
                "compeUnitName": unit["compeUnitName"],
                "factorName": factor,
                "ksaTypeName": "기술",
                "elementId": f"E-{index:02d}",
                "elementName": f"{unit['compeUnitName']} 수행",
                "factorNo": "S-01",
                "factorSource": "ncs-mcp",
                "performanceCriteria": [f"{unit['compeUnitName']} 산출물을 기준에 따라 확인한다"],
            }
        )

    # A stale display label must not outrank the official unit name carried by
    # the code-matched NCS evidence row.
    questions[0]["competency"] = "과거 임시 명칭"
    strategy = {
        "interview_questions": questions,
        "question_plan_used": {"total_main_count": 11},
    }
    enriched = _attach_ksa_evidence_to_strategy(strategy, evidence_rows)
    direct_rows = enriched["interview_questions"]
    exported_rows = _extract_all_generated_question_items(enriched)
    grouped_rows = [
        row
        for group in _group_interview_questions_for_response(direct_rows)
        for row in group["questions"]
    ]

    assert len(direct_rows) == len(exported_rows) == len(grouped_rows) == 11
    assert direct_rows[0]["competency"] == units[0]["officialUnitName"]
    for source_rows in (direct_rows, exported_rows, grouped_rows):
        assert {row["ncs_traceability"]["ability_unit_base_code"] for row in source_rows} == {
            row["officialUnitBaseCode"] for row in units
        }
        for row in source_rows:
            trace = row["ncs_traceability"]
            assert trace["detail_code"] == DETAIL_CODE
            assert trace["detail_name"] == DETAIL_NAME
            assert trace["ncs_code"] == row.get("ncsClCd", trace["ncs_code"])
            assert trace["ability_unit_name"]
            assert trace["evidence_id"]
            assert trace["performance_criteria_linked"] is True
            assert [probe["prompt"] for probe in row["structured_follow_ups"]] == row["follow_ups"]
            assert all(probe["evidence_target"] for probe in row["structured_follow_ups"])
            guide = row["assessment_guide"]
            assert guide["dimension_weight_total"] == 100
            assert len(guide["dimension_rubrics"]) == 4
            assert sum(item["weight"] for item in guide["dimension_rubrics"]) == 100


def test_frontend_renders_explicit_detail_and_unit_trace_codes() -> None:
    page = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"
    ).read_text(encoding="utf-8")

    assert "ncsTrace.detail_code" in page
    assert "ncsTrace.detail_name" in page
    assert "ncsTrace.ability_unit_base_code" in page
    assert "NCS 근거: 세분류" in page
    assert "NCS 능력단위:" in page
    assert "INTERVIEW_PROBE_TRIGGER_LABELS" in page
    assert "판단 근거·직접 행동이 불명확할 때" in page
    assert "interviewerAnswerSlotLabel(operating.answer_slot)" in page
    assert "질문 상한:" in page
