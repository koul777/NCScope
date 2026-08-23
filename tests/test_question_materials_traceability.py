from __future__ import annotations

from app.main import (
    _attach_ksa_evidence_to_strategy,
    _attach_presentation_material_packet,
    _build_presentation_material_packet,
    _ensure_question_material_reference,
    _presentation_material_prompt_text,
    _question_topic_axis,
    _task_conditions_for_method,
)
from app.services.jd_strategy import _build_ncs_code_template_fallback_question


CODE = "0202030201_25v3"


def test_presentation_packet_is_server_generated_and_attachable() -> None:
    presentation = "\uBC1C\uD45C\uBA74\uC811"
    detail = "\uC804\uAE30\uC124\uBE44\uC6B4\uC601"
    packet = _build_presentation_material_packet(
        interview_methods=[presentation],
        jd_text="\uC804\uAE30\uC124\uBE44 \uC720\uC9C0\uBCF4\uC218",
        notice_text="\uACF5\uACE0\uBB38",
        duty_text="\uC804\uAE30\uC124\uBE44 \uC810\uAC80",
        question_plan={"selected_items": [{"detail": detail, "enabled": True, "main_count": 1}]},
        ncs_matches=[
            {
                "ncsClCd": "1901060302_25v2",
                "ncsSubdCdnm": detail,
                "compeUnitName": "\uC804\uAE30\uC124\uBE44\uC6B4\uC601 \uB300\uAD00\uC5C5\uBB34",
                "compeUnitDef": "\uC804\uAE30\uC124\uBE44 \uC6B4\uC601\uACFC \uB300\uAD00\uC5C5\uBB34",
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "1901060302_25v2", "factorName": "\uC804\uAE30\uC124\uBE44 \uC810\uAC80 \uBC0F \uBCF4\uC218 \uB2A5\uB825"}
        ],
    )
    assert packet and packet["generated"] is True
    assert len(packet["slide_outline"]) == 4
    assert packet["case_materials"]
    assert packet["task_prompt"]
    assert packet["constraints"]
    assert packet["required_deliverables"]
    assert "[\uC11C\uBC84 \uC790\uB3D9 \uC0DD\uC131 \uBC1C\uD45C\uC790\uB8CC]" in _presentation_material_prompt_text(packet)
    strategy = {"interview_questions": [{"type": presentation, "task_conditions": {}}]}
    _attach_presentation_material_packet(strategy, packet)
    assert strategy["presentation_material_generated"] is True
    assert strategy["interview_questions"][0]["presentation_material"] == packet
    assert strategy["interview_questions"][0]["task_conditions"]["case_facts"] == packet["case_facts"]
    assert all("긴급 요청 1건" not in fact for fact in strategy["interview_questions"][0]["task_conditions"]["case_facts"])
    assert strategy["interview_questions"][0]["task_conditions"]["provided_materials"] == packet["provided_materials"]
    assert "접수 현황표" not in strategy["interview_questions"][0]["task_conditions"]["provided_materials"]


def test_presentation_fallback_surfaces_packet_task_without_ncs_label() -> None:
    result = _build_ncs_code_template_fallback_question(
        unit={
            "ncsClCd": "0202030201_25v3",
            "ncsSubdCdnm": "\uC815\uCC45\uAE30\uD68D",
            "compeUnitName": "\uC815\uCC45\uBD84\uC11D",
            "compeUnitDef": "\uC815\uCC45 \uC790\uB8CC\uB97C \uBD84\uC11D\uD558\uACE0 \uC131\uACFC\uB97C \uAD00\uB9AC\uD558\uB294 \uB2A5\uB825",
        },
        comp_name="\uC815\uCC45\uBD84\uC11D",
        ncs_code="0202030201_25v3",
        ksa_terms=["\uC815\uCC45 \uADFC\uAC70 \uD655\uC778"],
        index=0,
        method_override="\uBC1C\uD45C\uBA74\uC811",
        presentation_material_text=(
            "\uBC1C\uD45C \uBA54\uC778 \uACFC\uC81C: \uACF5\uACE0\uBB38\uACFC \uC9C1\uBB34\uAE30\uC220\uC11C\uC5D0 \uC81C\uC2DC\uB41C \uC5C5\uBB34\uC758 \uB204\uB77D \uC0AC\uC2E4\uACFC \uC77C\uC815 \uC81C\uC57D\uC744 \uBE44\uAD50\uD558\uACE0 \uB300\uC548\uC744 \uC120\uD0DD\uD558\uC138\uC694."
        ),
    )

    question = result["question"]
    assert question.startswith("[\uBC1C\uD45C\uACFC\uC81C] \uC81C\uACF5\uB41C \uC9C1\uBB34 \uC790\uB8CC\uC5D0\uC11C")
    assert "\uC815\uCC45\uAE30\uD68D \uC815\uCC45\uBD84\uC11D \uC5C5\uBB34\uC5D0\uC11C" not in question
    assert "\uB204\uB77D \uC0AC\uC2E4" in question


def test_presentation_packet_uses_each_job_source_without_domain_hardcoding() -> None:
    presentation = "\uBC1C\uD45C\uBA74\uC811"
    detail = "\uC815\uCC45\uAE30\uD68D"
    packet = _build_presentation_material_packet(
        interview_methods=[presentation],
        notice_text="\uACF5\uACE0\uBB38: \uC815\uCC45\uAE30\uD68D \uB2F4\uB2F9\uC790\uB294 \uC815\uCC45 \uC2E0\uB8B0\uB3C4\uC640 \uC18C\uD1B5\uC744 \uD655\uBCF4\uD55C\uB2E4.",
        jd_text="\uC9C1\uBB34\uAE30\uC220\uC11C: \uC815\uCC45 \uBD84\uC11D, \uC790\uB8CC \uAC80\uC99D, \uC131\uACFC \uC9C0\uD45C \uC124\uACC4\uB97C \uC218\uD589\uD55C\uB2E4.",
        duty_text="\uB2F4\uB2F9\uC5C5\uBB34: \uAD00\uB828 \uBD80\uC11C \uC758\uACAC\uC744 \uC218\uC9D1\uD558\uACE0 \uC815\uCC45 \uBCF4\uACE0\uC11C\uB97C \uC791\uC131\uD55C\uB2E4.",
        question_plan={"selected_items": [{"detail": detail, "enabled": True, "main_count": 1}]},
        ncs_matches=[
            {
                "ncsClCd": "0201010101_25v3",
                "ncsSubdCdnm": detail,
                "compeUnitName": "\uC815\uCC45 \uBD84\uC11D",
                "compeUnitDef": "\uC815\uCC45 \uC790\uB8CC\uB97C \uBD84\uC11D\uD558\uACE0 \uC131\uACFC\uB97C \uAD00\uB9AC\uD558\uB294 \uB2A5\uB825",
            }
        ],
        ncs_ksa=[
            {"ncsClCd": "0201010101_25v3", "factorName": "\uC815\uCC45 \uADFC\uAC70 \uD655\uC778 \uBC0F \uC131\uACFC\uC9C0\uD45C \uBD84\uC11D"}
        ],
    )
    assert packet and packet["generated"] is True
    assert "\uC804\uAE30" not in packet["task_prompt"]
    assert "\uC815\uCC45" in packet["task_prompt"]
    source_labels = {row["source"] for row in packet["case_materials"]}
    assert {"\uACF5\uACE0\uBB38", "\uC9C1\uBB34\uAE30\uC220\uC11C", "\uB2F4\uB2F9\uC5C5\uBB34 \uBCF4\uC644"} <= source_labels
    assert any("\uC815\uCC45 \uBD84\uC11D" in str(row["value"]) for row in packet["case_materials"])
    assert "\uC791\uC5C5 \uC911\uB2E8" not in packet["slide_outline"][0]["instruction"]
    # Do not silently import the legacy domain-profile examples when the
    # uploaded source documents contain their own facts.
    packet_text = " ".join(
        [
            str(packet.get("task_prompt") or ""),
            *[str(value) for value in (packet.get("case_facts") or [])],
            *[
                str(row.get("value") or "")
                for row in (packet.get("case_materials") or [])
                if isinstance(row, dict)
            ],
        ]
    )
    assert "\uAE34\uAE09 \uC694\uCCAD 1\uAC74" not in packet_text
    assert "\uC811\uC218 \uD604\uD669\uD45C" not in packet_text


def test_presentation_and_debate_questions_get_concrete_case_rows() -> None:
    for method in ("발표면접", "토론면접"):
        conditions = _task_conditions_for_method(
            method=method,
            subject="문서작성",
            focus="문서 요구사항 파악",
            variation_index=1,
        )
        rows = conditions["case_materials"]
        assert len(rows) >= 3
        assert all(row["source"] and row["field"] and row["value"] for row in rows)
        question = _ensure_question_material_reference(
            f"[{method}] 자료를 바탕으로 판단해 주세요.",
            method,
            conditions,
        )
        assert ("[제공자료]" if method == "발표면접" else "[공통자료]") in question
        assert rows[0]["value"] in question


def test_topic_axis_is_distinct_and_persisted_in_case_pack() -> None:
    assert _question_topic_axis(0) != _question_topic_axis(1)
    conditions = _task_conditions_for_method(
        method="상황면접",
        subject="문서작성",
        focus="문서 요구사항 파악",
        variation_index=4,
    )
    assert any(_question_topic_axis(4) in fact for fact in conditions["case_facts"])
    assert any(_question_topic_axis(4) in row["value"] for row in conditions["case_materials"])


def test_ncs_traceability_keeps_ability_element_and_performance_criteria() -> None:
    evidence = {
        "ncsClCd": CODE,
        "compeUnitName": "문서작성",
        "elementName": "문서 요구사항 파악",
        "elementId": "el-01",
        "factorName": "문서 요구사항 파악 능력",
        "ksaTypeName": "기술",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
        "performanceCriteria": ["요구사항을 분류한다.", "누락 항목을 확인한다."],
    }
    result = _attach_ksa_evidence_to_strategy(
        {
            "interview_questions": [
                {
                    "type": "상황면접",
                    "competency": "문서작성",
                    "ncsClCd": CODE,
                    "question_evidence_id": "",
                    "question_focus": "문서 요구사항 파악 능력",
                    "question": "자료 누락 상황에서 무엇을 확인하시겠습니까?",
                }
            ]
        },
        [evidence],
    )
    trace = result["interview_questions"][0]["ncs_traceability"]
    assert trace["ability_unit_name"] == "문서작성"
    assert trace["element_name"] == "문서 요구사항 파악"
    assert trace["performance_criteria_linked"] is True
    assert trace["performance_criteria"] == ["요구사항을 분류한다.", "누락 항목을 확인한다."]
