from __future__ import annotations

import copy
import json

import app.services.server_question_fallback as server_fallback
from app.services.jd_strategy import (
    _build_ncs_code_template_fallback_question,
    normalize_question_dedup_key,
)
from app.services.question_surface import stable_ksa_evidence_id


def _fixtures() -> tuple[dict, list[dict], list[dict]]:
    question_plan = {
        "total_main_count": 1,
        "question_sequence": [
            {
                "detail": "전기설비운영",
                "follow_up_count": 3,
            }
        ],
    }
    ncs_matches = [
        {
            "ncsClCd": "1901060101_23v1",
            "compeUnitName": "전기설비운영",
            "compeUnitDef": "전기설비의 상태를 확인하고 안전하게 유지한다.",
            "ncsSubdCdnm": "전기설비운영",
        }
    ]
    ncs_ksa = [
        {
            "ncsClCd": "1901060101_23v1",
            "compeUnitName": "전기설비운영",
            "elementName": "전기설비 점검",
            "factorName": "전기설비 점검 및 보수 능력",
            "ksaTypeName": "기술",
            "factorSource": "NCS_MCP",
        }
    ]
    return question_plan, ncs_matches, ncs_ksa


def test_provider_free_fallback_rotates_same_ksa_into_distinct_questions() -> None:
    questions = [
        _build_ncs_code_template_fallback_question(
            unit={
                "ncsClCd": "0202030209_22v2",
                "compeUnitName": "\uBB38\uC11C\uC791\uC131",
                "ncsSubdCdnm": "\uC0AC\uBB34\uD589\uC815",
                "compeUnitDef": "\uC5C5\uBB34 \uC790\uB8CC\uB97C \uC815\uB9AC\uD558\uACE0 \uBB38\uC11C\uB97C \uC791\uC131\uD558\uB294 \uB2A5\uB825",
            },
            comp_name="\uBB38\uC11C\uC791\uC131",
            ncs_code="0202030209_22v2",
            ksa_terms=["\uBB38\uC11C \uC791\uC131 \uC808\uCC28 \uC9C0\uCE68"],
            evidence_terms=["\uBB38\uC11C \uC791\uC131 \uC808\uCC28 \uC9C0\uCE68"],
            evidence_rows=[{"factorName": "\uBB38\uC11C \uC791\uC131 \uC808\uCC28 \uC9C0\uCE68", "ksaTypeName": "\uC9C0\uC2DD"}],
            index=index,
            method_override="\uACBD\uD5D8\uBA74\uC811",
            case_slot_id=f"slot-{index}",
            case_slot_signature=f"slot:{index}",
        )["question"]
        for index in range(5)
    ]
    assert len(questions) == 5
    assert len(set(questions)) == 5


def test_provider_free_fallback_keeps_all_interview_methods_distinct() -> None:
    methods = [
        "\uACBD\uD5D8\uBA74\uC811",
        "\uC0C1\uD669\uBA74\uC811",
        "\uBC1C\uD45C\uBA74\uC811",
        "\uD1A0\uB860\uBA74\uC811",
        "\uC778\uBC14\uC2A4\uCF13\uBA74\uC811",
        "\uC9C1\uBB34\uC9C0\uC2DD\uBA74\uC811",
        "\uCC3D\uC758\uC801 \uBB38\uC81C\uD574\uACB0\uB825\uBA74\uC811",
    ]
    for method in methods:
        questions = [
            _build_ncs_code_template_fallback_question(
                unit={
                    "ncsClCd": "0202030209_22v2",
                    "compeUnitName": "\uBB38\uC11C\uC791\uC131",
                    "ncsSubdCdnm": "\uC0AC\uBB34\uD589\uC815",
                },
                comp_name="\uBB38\uC11C\uC791\uC131",
                ncs_code="0202030209_22v2",
                ksa_terms=["\uBB38\uC11C \uC791\uC131 \uC808\uCC28 \uC9C0\uCE68"],
                evidence_terms=["\uBB38\uC11C \uC791\uC131 \uC808\uCC28 \uC9C0\uCE68"],
                index=index,
                method_override=method,
                case_slot_id=f"{method}-{index}",
                case_slot_signature=f"{method}:{index}",
            )["question"]
            for index in range(5)
        ]
        assert len(set(questions)) == 5, method


def test_presentation_fallback_surfaces_packet_main_task_in_question():
    question = _build_ncs_code_template_fallback_question(
        unit={
            "ncsClCd": "0202030209_22v2",
            "compeUnitName": "문서작성",
            "ncsSubdCdnm": "사무행정",
        },
        comp_name="문서작성",
        ncs_code="0202030209_22v2",
        ksa_terms=["문서 요구사항 확인"],
        evidence_terms=["문서 요구사항 확인"],
        index=0,
        method_override="발표면접",
        presentation_material_text=(
            "[서버 자동 생성 발표자료]\n"
            "발표 메인 과제: 공고문에 제시된 민원 처리 업무의 오류 원인을 비교하고 개선안을 발표하십시오."
        ),
    )

    assert "민원 처리 업무의 오류 원인을 비교하고 개선안을 발표하십시오." in question["question"]


def test_builds_exact_deterministic_official_ksa_question_without_provider():
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    original_inputs = copy.deepcopy((question_plan, ncs_matches, ncs_ksa))

    first = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )
    second = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )

    assert first == second
    assert (question_plan, ncs_matches, ncs_ksa) == original_inputs
    assert first["fallback_generated"] is True
    assert first["fallback_failure_code"] == ""
    assert first["question_count"] == first["requested_question_count"] == 1
    assert first["question_source"] == "server_ksa_fallback"
    assert first["provider_fallback_used"] is True
    assert first["degraded"] is True
    assert first["human_review_required"] is True
    assert first["question_release_status"] == "human_review_required"
    assert len(first["interview_by_competency"]) == 1

    question = first["interview_questions"][0]
    assert question["question"]
    assert question["type"] == question["question_type"] == question["method"] == "상황면접"
    assert question["question_source"] == "server_ksa_fallback"
    assert question["provider_fallback_used"] is True
    assert question["degraded"] is True
    assert question["human_review_required"] is True
    assert question["question_focus"] == "전기설비 점검 및 보수 능력"
    assert question["question_focus_source"] == "official_ksa"
    assert question["ksa_refs"] == ["전기설비 점검 및 보수 능력"]
    assert question["question_evidence_id"] == stable_ksa_evidence_id(ncs_ksa[0])
    assert question["question_evidence_required"] is True
    assert len(question["follow_ups"]) == 3
    assert len(question["evaluation_points"]) == 4


def test_runtime_evidence_lock_selects_the_exact_official_factor():
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    selected = {
        **ncs_ksa[0],
        "elementName": "비상상황 복구",
        "factorName": "정전 사고 응급조치 및 복구 능력",
    }
    ncs_ksa.append(selected)
    question_plan["question_sequence"][0].update(
        {
            "ncsClCd": selected["ncsClCd"],
            "evidence_id": stable_ksa_evidence_id(selected),
            "type": "직무지식면접",
        }
    )

    result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접", "직무지식면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )

    question = result["interview_questions"][0]
    assert question["type"] == "직무지식면접"
    assert question["question_focus"] == selected["factorName"]
    assert question["ksa_refs"] == [selected["factorName"]]
    assert question["question_evidence_id"] == stable_ksa_evidence_id(selected)


def test_builds_every_planned_slot_or_returns_nothing():
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    question_plan["total_main_count"] = 2
    question_plan["question_sequence"].append(
        {"detail": "전기설비운영", "follow_up_count": 5}
    )
    second_code = "1901060102_23v1"
    ncs_matches.append(
        {
            "ncsClCd": second_code,
            "compeUnitName": "전기설비안전관리",
            "compeUnitDef": "전기설비 사고 위험을 확인하고 안전조치를 수행한다.",
            "ncsSubdCdnm": "전기설비운영",
        }
    )
    ncs_ksa.append(
        {
            "ncsClCd": second_code,
            "compeUnitName": "전기설비안전관리",
            "elementName": "사고 위험 통제",
            "factorName": "전기안전 법령 적용 능력",
            "ksaTypeName": "지식",
        }
    )

    result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접", "직무지식면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )

    assert result["question_count"] == result["requested_question_count"] == 2
    assert [row["ncsClCd"] for row in result["interview_questions"]] == [
        "1901060101_23v1",
        second_code,
    ]
    assert [len(row["follow_ups"]) for row in result["interview_questions"]] == [3, 5]


def test_missing_ksa_fails_closed_with_degraded_metadata():
    question_plan, ncs_matches, _ = _fixtures()

    result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=[],
    )

    assert result["interview_questions"] == []
    assert result["interview_by_competency"] == []
    assert result["question_count"] == 0
    assert result["requested_question_count"] == 1
    assert result["fallback_generated"] is False
    assert result["fallback_failure_code"] == "fallback_official_ksa_unavailable"
    assert result["provider_fallback_used"] is True
    assert result["degraded"] is True
    assert result["human_review_required"] is True


def test_plan_count_mismatch_fails_closed_instead_of_returning_partial_questions():
    question_plan, ncs_matches, ncs_ksa = _fixtures()

    result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        target_count=2,
    )

    assert result["interview_questions"] == []
    assert result["question_count"] == 0
    assert result["requested_question_count"] == 2
    assert result["fallback_failure_code"] == "fallback_question_plan_count_mismatch"


def test_mismatched_ncs_evidence_fails_closed():
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    ncs_ksa[0]["ncsClCd"] = "DIFFERENT_UNIT"

    result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )

    assert result["interview_questions"] == []
    assert result["fallback_generated"] is False
    assert result["fallback_failure_code"] == "fallback_evidence_assignment_failed"


def test_internal_exception_never_reflects_secret_or_exception_text(monkeypatch):
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    secret = "sk-or-should-never-be-returned"

    def fail_builder(**_kwargs):
        raise RuntimeError(f"provider exploded: {secret}")

    monkeypatch.setattr(
        server_fallback,
        "_build_ncs_code_template_fallback_question",
        fail_builder,
    )
    result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["interview_questions"] == []
    assert result["fallback_failure_code"] == "fallback_question_build_failed"
    assert secret not in serialized
    assert "provider exploded" not in serialized


def test_same_detail_fallback_slots_use_distinct_situation_frames():
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    question_plan["total_main_count"] = 5
    question_plan["question_sequence"] = [
        {"detail": question_plan["question_sequence"][0]["detail"], "follow_up_count": 3}
        for _ in range(5)
    ]

    result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
    )

    questions = result["interview_questions"]
    assert len(questions) == 5
    assert len({item["question"] for item in questions}) == 5
    assert len({item["follow_ups"][0] for item in questions}) == 5


def test_fallback_generation_offset_moves_to_next_question_frame():
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    first = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        generation_offset=0,
    )
    next_result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["상황면접"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        generation_offset=1,
    )

    assert first["interview_questions"][0]["question"] != next_result["interview_questions"][0]["question"]


def test_fallback_history_skips_exact_previous_question_without_collapsing_variations():
    question_plan, ncs_matches, ncs_ksa = _fixtures()
    first = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["?곹솴硫댁젒"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        generation_offset=0,
    )
    previous = first["interview_questions"][0]["question"]
    next_result = server_fallback.build_server_ksa_fallback_strategy(
        question_plan=question_plan,
        interview_methods=["?곹솴硫댁젒"],
        ncs_matches=ncs_matches,
        ncs_ksa=ncs_ksa,
        generation_offset=0,
        avoid_questions=[previous],
    )

    next_question = next_result["interview_questions"][0]["question"]
    assert next_question
    assert normalize_question_dedup_key(next_question) != normalize_question_dedup_key(previous)
