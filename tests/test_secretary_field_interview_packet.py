from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from app import main
from app.services.ai_question_quality_review import _deterministic_contract_issues


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "secretary_02020301_interview_packet.json"
CATALOG_PATH = ROOT / "app" / "data" / "ncs_unit_catalog.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _official_units() -> list[dict]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return [
        row for row in catalog["units"] if row.get("detail_code") == "02020301"
    ]


def _match_rows(fixture: dict) -> list[dict]:
    return [
        {
            "ncsClCd": row["ncsClCd"],
            "officialUnitBaseCode": row["ncsClCd"].split("_", 1)[0],
            "officialUnitName": row["competency"],
            "compeUnitName": row["competency"],
            "officialDetailCode": fixture["detail"]["code"],
            "officialDetailName": fixture["detail"]["name"],
            "unitCatalogVerified": True,
            "detailPathCodeVerified": True,
            "unitResolutionKind": "catalog_full_code_exact",
        }
        for row in fixture["questions"]
    ]


def _exact_bundle(fixture: dict) -> dict:
    codes = [row["ncsClCd"] for row in fixture["questions"]]
    fingerprint = hashlib.sha256("\n".join(sorted(codes)).encode("utf-8")).hexdigest()
    return {
        "status": "exact_complete",
        "eligible_for_generation": True,
        "matcher_version": "fixture-exact-complete-v1",
        "fingerprint": fingerprint,
        "details": [
            {
                "officialDetailCode": fixture["detail"]["code"],
                "officialDetailName": fixture["detail"]["name"],
                "detailExpectedUnitBaseCount": 11,
                "detailVerifiedUnitBaseCount": 11,
                "detailRetrievalComplete": True,
                "detailRetrievalCapLimited": False,
            }
        ],
    }


def _assemble_packet() -> tuple[dict, list[dict], dict]:
    fixture = _fixture()
    evidence_rows: list[dict] = []
    questions: list[dict] = []
    for index, row in enumerate(fixture["questions"]):
        evidence = dict(row["evidence"])
        evidence_rows.append(evidence)
        method = row["method"]
        evaluation_points = main._method_evaluation_points(
            method,
            [evidence["factorName"]],
            evidence["ksaTypeName"],
            row["surface_focus"],
        )
        questions.append(
            {
                "type": method,
                "method": method,
                "competency": row["competency"],
                "ncsClCd": row["ncsClCd"],
                "ncs_detail": fixture["detail"]["name"],
                "officialDetailCode": fixture["detail"]["code"],
                "compeUnitDef": row["compeUnitDef"],
                "question": row["question"],
                "follow_ups": list(row["follow_ups"]),
                "evaluation_points": evaluation_points,
                "question_focus": evidence["factorName"],
                "question_focus_surface": row["surface_focus"],
                "question_focus_type": evidence["ksaTypeName"],
                "question_evidence_id": main.stable_ksa_evidence_id(evidence),
                "question_evidence_required": True,
                # This checked-in sample was authored and reviewed in the Codex
                # path; it is not presented as an upstream provider response.
                "question_source": "codex_cli",
                "model_question_preserved": True,
                "question_variation_index": index,
                "question_variation_axis": f"unit-{row['ncsClCd']}",
                "schedule_role": row["schedule_role"],
                "planned_minutes": row["planned_minutes"],
            }
        )

    strategy = main._attach_ksa_evidence_to_strategy(
        {
            "question_plan_used": {"total_main_count": len(questions)},
            "interview_questions": questions,
        },
        evidence_rows,
    )
    matches = _match_rows(fixture)
    bundle = _exact_bundle(fixture)
    for question in strategy["interview_questions"]:
        digest, _artifact = main._question_artifact_hash(
            question,
            ncs_matches=matches,
            ncs_match_bundle=bundle,
        )
        question["question_artifact_hash"] = digest
        question["match_bundle_fingerprint"] = bundle["fingerprint"]
    return strategy, matches, bundle


def test_secretary_packet_is_a_realistic_forty_minute_core_plus_alternate_bank() -> None:
    fixture = _fixture()
    plan = fixture["session_plan"]
    core_rows = [row for row in fixture["questions"] if row["schedule_role"] == "core"]
    alternate_rows = [
        row for row in fixture["questions"] if row["schedule_role"] == "alternate"
    ]

    scheduled_minutes = (
        plan["opening_minutes"]
        + sum(row["planned_minutes"] for row in core_rows)
        + plan["closing_minutes"]
    )
    assert scheduled_minutes == plan["target_minutes"] == 40
    assert plan["minimum_minutes"] <= scheduled_minutes <= plan["maximum_minutes"]
    assert len(core_rows) == 5
    assert len(alternate_rows) == 6
    assert {row["ncsClCd"] for row in core_rows} == set(plan["core_unit_codes"])
    assert {row["ncsClCd"] for row in alternate_rows} == set(
        plan["alternate_unit_codes"]
    )
    swap_policy = plan["swap_policy"]
    assert swap_policy["mode"] == "one_for_one_core_slot"
    assert swap_policy["slot_minutes"] == 7
    assert swap_policy["scheduled_swap_minutes"] == 0
    assert set(swap_policy["replaceable_core_unit_codes"]) == set(
        plan["core_unit_codes"]
    )
    assert all(
        row["planned_minutes"] == 0
        and swap_policy["slot_minutes"] == core_rows[0]["planned_minutes"]
        for row in alternate_rows
    )
    assert all(len(row["follow_ups"]) == 3 for row in fixture["questions"])
    assert all(
        "임원" in row["question"] or "비서실" in row["question"]
        for row in fixture["questions"]
    )
    normalized = [re.sub(r"\s+", "", row["question"]).casefold() for row in fixture["questions"]]
    assert len(normalized) == len(set(normalized)) == 11


def test_secretary_packet_matches_all_current_official_units_and_passes_hard_gate() -> None:
    fixture = _fixture()
    official = _official_units()
    strategy, _matches, _bundle = _assemble_packet()
    report = strategy["question_quality_report"]

    assert len(official) == fixture["detail"]["expected_official_unit_count"] == 11
    assert {(row["code"], row["name"]) for row in official} == {
        (row["ncsClCd"], row["competency"]) for row in fixture["questions"]
    }
    assert report["passed"] is True
    assert report["summary"]["ready_count"] == 11
    assert report["summary"]["needs_review_count"] == 0
    assert report["summary"]["precision_grounding_failed_count"] == 0
    assert all(not item["issues"] for item in report["items"])


def test_secretary_packet_has_executable_probes_rubrics_and_ncs_traceability() -> None:
    strategy, _matches, _bundle = _assemble_packet()

    for question in strategy["interview_questions"]:
        probes = question["structured_follow_ups"]
        assert len(probes) == 3
        assert [probe["prompt"] for probe in probes] == question["follow_ups"]
        assert [probe["trigger"] for probe in probes] == [
            "always",
            "if_decision_or_action_basis_missing",
            "if_result_or_verification_missing",
        ]
        assert all(probe["answer_slot"] for probe in probes)
        assert all(probe["evidence_target"] for probe in probes)
        assert all(probe["ask_limit"] == 1 for probe in probes)
        assert all(
            main._probe_answer_slot_semantically_matches_prompt(
                probe["answer_slot"], probe["prompt"]
            )
            for probe in probes
        )
        assert all(
            main._probe_contract_semantics_ok(
                probe["answer_slot"], probe["evidence_target"], probe["prompt"]
            )
            for probe in probes
        )

        guide = question["assessment_guide"]
        rubrics = guide["dimension_rubrics"]
        assert len(rubrics) == 4
        assert guide["dimension_weight_total"] == 100
        assert sum(row["weight"] for row in rubrics) == 100
        assert guide["critical_floor"]["score"] == 1
        assert guide["no_answer_rule"]["score"] == 1
        for rubric in rubrics:
            assert rubric["observable_evidence"]
            assert rubric["critical_floor"] == 1
            assert rubric["no_answer_rule"]
            assert set(rubric["anchors"]) == {"5", "3", "1"}
            assert all(rubric["anchors"].values())
            assert all(
                question["question_focus_surface"] in rubric["anchors"][score]
                for score in ("5", "3", "1")
            )

        if question["method"] == "경험면접":
            assert "유사경험" in question["question"]
            assert "모두에게 같은 표준 대체상황" in question["question"]

        trace = question["ncs_traceability"]
        assert trace["detail_code"] == "02020301"
        assert trace["detail_name"] == "비서"
        assert trace["ability_unit_base_code"] == question["ncsClCd"].split("_", 1)[0]
        assert trace["ability_unit_name"] == question["competency"]
        assert trace["element_id"]
        assert trace["element_name"]
        assert trace["performance_criteria_linked"] is True
        assert trace["evidence_id"] == question["question_evidence_id"]


def test_secretary_packet_passes_the_independent_release_contract_gate() -> None:
    strategy, _matches, _bundle = _assemble_packet()

    assert {
        index: _deterministic_contract_issues(question)
        for index, question in enumerate(strategy["interview_questions"], start=1)
    } == {index: [] for index in range(1, 12)}


def test_secretary_packet_artifact_hash_and_every_public_view_keep_the_full_contract() -> None:
    strategy, matches, bundle = _assemble_packet()
    direct = strategy["interview_questions"]
    flat = main._extract_all_generated_question_items(strategy)
    text_rows = main._build_generated_question_text_rows(strategy)
    grouped = [
        question
        for group in main._group_interview_questions_for_response(direct)
        for question in group["questions"]
    ]

    hashes = [row["question_artifact_hash"] for row in direct]
    assert len(hashes) == len(set(hashes)) == 11
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in hashes)
    assert all(row["match_bundle_fingerprint"] == bundle["fingerprint"] for row in direct)

    for rows in (flat, text_rows, grouped):
        assert len(rows) == 11
        for row in rows:
            assert len(row["structured_follow_ups"]) == 3
            assert len(row["assessment_guide"]["dimension_rubrics"]) == 4
            assert row["assessment_guide"]["dimension_weight_total"] == 100
            assert row["ncs_traceability"]["detail_code"] == "02020301"
            assert row["question_artifact_hash"]
            assert row["match_bundle_fingerprint"] == bundle["fingerprint"]

    changed = copy.deepcopy(direct[0])
    changed["structured_follow_ups"][1]["prompt"] += " 추가 확인"
    changed_hash, _artifact = main._question_artifact_hash(
        changed,
        ncs_matches=matches,
        ncs_match_bundle=bundle,
    )
    assert changed_hash != direct[0]["question_artifact_hash"]


def test_secretary_packet_ui_contract_exposes_probe_rubric_trace_and_hash() -> None:
    page = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

    for token in (
        "structured_follow_ups",
        "assessment_guide",
        "dimension_rubrics",
        "ncs_traceability",
        "question_artifact_hash",
    ):
        assert token in page
