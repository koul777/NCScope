from __future__ import annotations

import csv
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FINALIZE_PATH = ROOT / "scripts" / "finalize_ncs_recruitment_goldset.py"
PREPARE_PATH = ROOT / "scripts" / "prepare_ncs_recruitment_goldset.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def seed(tmp_path: Path):
    prepare = load_module(PREPARE_PATH, f"prepare_goldset_{tmp_path.name}")
    finalize = load_module(FINALIZE_PATH, f"finalize_goldset_{tmp_path.name}")
    fake_root = tmp_path / "repo"
    document_dir = fake_root / "private"
    output_dir = fake_root / "tmp" / "goldset"
    document_dir.mkdir(parents=True)
    first = document_dir / "one.pdf"
    second = document_dir / "two.pdf"
    first.write_bytes(b"first evaluation-only document")
    second.write_bytes(b"second evaluation-only document")
    benchmark = {
        "summary": {},
        "cases": [
            {"case_id": "case-1", "posting_id": "posting-1", "status": "ok"},
            {"case_id": "case-2", "posting_id": "posting-2", "status": "ok"},
        ],
    }
    workflow = prepare.build_workflow(
        benchmark,
        [
            {
                "case_id": "case-1",
                "local_document_path": str(first),
                "source_url": "https://www.ncs.go.kr/blind/one",
            },
            {
                "case_id": "case-2",
                "local_document_path": str(second),
                "source_url": "https://www.ncs.go.kr/blind/two",
            },
        ],
    )
    prepare.ROOT = fake_root
    paths = prepare.write_workflow(workflow, output_dir)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    integrity = json.loads(paths["integrity"].read_text(encoding="utf-8"))
    reviewer_a = _read_csv(paths["reviewer_a"])
    reviewer_b = _read_csv(paths["reviewer_b"])
    reviewer_a = [
        {field: row[field] for field in finalize.REVIEWER_FIELDS}
        for row in reviewer_a
    ]
    reviewer_b = [
        {field: row[field] for field in finalize.REVIEWER_FIELDS}
        for row in reviewer_b
    ]
    adjudication = _read_csv(paths["adjudication"])
    finalize.ROOT = fake_root
    return finalize, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication


def complete_review(
    row: dict[str, str],
    *,
    reviewer_id: str,
    mapping_state: str = "official_current",
    names: list[str] | None = None,
    codes: list[str] | None = None,
) -> None:
    row.update(
        {
            "reviewer_id": reviewer_id,
            "reviewed_at_utc": "2026-08-25T01:23:45Z",
            "review_status": "completed_independent_review",
            "mapping_state": mapping_state,
            "detail_names_json": json.dumps(
                ["경영기획"] if names is None else names, ensure_ascii=False
            ),
            "detail_codes_json": json.dumps(
                ["02010101"] if codes is None else codes, ensure_ascii=False
            ),
            "evidence_json": json.dumps(
                [{"quote": "NCS 세분류: 경영기획", "page": 3}],
                ensure_ascii=False,
            ),
            "confidence": "high",
        }
    )


def finalize_call(
    mod,
    paths,
    manifest,
    integrity,
    reviewer_a,
    reviewer_b,
    adjudication,
    **kwargs,
):
    defaults = {
        "reviewer_a_kind": "ai_agent",
        "reviewer_b_kind": "ai_agent",
        "reviewer_a_provenance": "isolated agent A source-document review",
        "reviewer_b_provenance": "isolated agent B source-document review",
    }
    defaults.update(kwargs)
    return mod.finalize_reference(
        manifest,
        integrity,
        reviewer_a,
        reviewer_b,
        adjudication,
        manifest_path=paths["manifest"],
        do_not_tune_path=paths["do_not_tune"],
        **defaults,
    )


def test_ai_consensus_is_a_reference_but_never_human_gold(tmp_path: Path) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(row, reviewer_id="agent-a")
    for row in reviewer_b:
        complete_review(row, reviewer_id="agent-b")

    result = finalize_call(
        mod,
        paths,
        manifest,
        integrity,
        reviewer_a,
        reviewer_b,
        adjudication,
    )

    assert result["evaluation_basis"] == (
        "independent_ai_agent_adjudicated_reference_not_human_gold"
    )
    assert result["is_human_gold"] is False
    assert result["is_gold_accuracy"] is False
    assert result["is_gold"] is False
    assert result["automatic_predictions_are_gold"] is False
    assert result["summary"]["disagreement_count"] == 0
    assert all(
        row["resolution_type"] == "exact_two_reviewer_consensus"
        for row in result["records"]
    )


def test_official_current_requires_exact_catalog_name_code_pair(
    tmp_path: Path,
) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(
            row,
            reviewer_id="agent-a",
            names=["사무행정"],
            codes=["02010101"],
        )
    for row in reviewer_b:
        complete_review(
            row,
            reviewer_id="agent-b",
            names=["사무행정"],
            codes=["02010101"],
        )

    with pytest.raises(mod.GoldsetFinalizationError, match="name/code pair"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )


def test_legacy_state_rejects_names_that_are_all_current_official(
    tmp_path: Path,
) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    current_name = next(iter(mod.CURRENT_OFFICIAL_DETAILS.values()))
    for row in reviewer_a:
        complete_review(
            row,
            reviewer_id="agent-a",
            mapping_state="legacy_or_nonstandard",
            names=[current_name],
            codes=[],
        )
    for row in reviewer_b:
        complete_review(
            row,
            reviewer_id="agent-b",
            mapping_state="legacy_or_nonstandard",
            names=[current_name],
            codes=[],
        )

    with pytest.raises(mod.GoldsetFinalizationError, match="all resolve to current"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )


def test_adjudication_decision_integrity_binds_completed_csv_and_counts(
    tmp_path: Path,
) -> None:
    mod = load_module(FINALIZE_PATH, "finalize_decision_integrity")
    completed = tmp_path / "adjudication_completed.csv"
    completed.write_text("sealed decisions", encoding="utf-8")
    worklist = tmp_path / "worklist.csv"
    worklist.write_text("sealed worklist", encoding="utf-8")
    decision_template = tmp_path / "decision_template.csv"
    decision_template.write_text("sealed template", encoding="utf-8")
    packet = tmp_path / "source.md"
    packet.write_text("세분류 01. 경영기획", encoding="utf-8")
    disputes = tmp_path / "disputes.json"
    dispute_payload = {
        "source_only": True,
        "automatic_predictions_included": False,
        "disagreement_count": 1,
        "disputes": [
            {
                "item_id": "one",
                "document_sha256": "a" * 64,
                "source_packet_path": str(packet),
                "source_packet_sha256": mod.sha256_file(packet),
            }
        ],
    }
    disputes.write_text(json.dumps(dispute_payload), encoding="utf-8")
    upstream_paths = {}
    for name in ("manifest", "seed_integrity", "do_not_tune", "reviewer_a", "reviewer_b"):
        upstream = tmp_path / name
        upstream.write_text(name, encoding="utf-8")
        upstream_paths[name] = upstream
    worklist_integrity = tmp_path / "worklist_integrity.json"
    worklist_integrity.write_text(
        json.dumps(
            {
                "worklist_version": mod.ADJUDICATION_WORKLIST_VERSION,
                "generated_at_utc": "2026-08-25T03:00:00+00:00",
                "source_only": True,
                "automatic_predictions_included": False,
                "record_count": 2,
                "agreement_count": 1,
                "disagreement_count": 1,
                "input_sha256": {
                    **{
                        name: mod.sha256_file(upstream)
                        for name, upstream in upstream_paths.items()
                    },
                    "adjudication_template": "c" * 64,
                    "packet_index": "d" * 64,
                    "packet_integrity": "e" * 64,
                },
                "output_sha256": {
                    "adjudication_csv": mod.sha256_file(worklist),
                    "dispute_json": mod.sha256_file(disputes),
                    "decision_template": mod.sha256_file(decision_template),
                },
            }
        ),
        encoding="utf-8",
    )
    integrity = tmp_path / "decision_integrity.json"
    payload = {
        "applied_version": mod.ADJUDICATION_APPLIED_VERSION,
        "generated_at_utc": "2026-08-25T03:30:00+00:00",
        "source_only": True,
        "automatic_predictions_included": False,
        "record_count": 2,
        "adjudicated_count": 1,
        "input_sha256": {
            "worklist": mod.sha256_file(worklist),
            "disputes": mod.sha256_file(disputes),
            "decisions_completed": "b" * 64,
            "worklist_integrity": mod.sha256_file(worklist_integrity),
            "decision_template_original": mod.sha256_file(decision_template),
        },
        "output_sha256": {
            "adjudication_completed": mod.sha256_file(completed),
        },
    }
    integrity.write_text(json.dumps(payload), encoding="utf-8")
    final_records = [
        {
            "item_id": "one",
            "document_sha256": "a" * 64,
            "resolution_type": "third_party_adjudication",
            "evidence": {
                "adjudicator": [
                    {"quote": "세분류 01. 경영기획", "section": "NCS 분류체계"}
                ]
            },
        }
    ]

    mod._validate_adjudication_decision_integrity(
        integrity,
        adjudication_path=completed,
        worklist_integrity_path=worklist_integrity,
        dispute_path=disputes,
        upstream_paths=upstream_paths,
        final_records=final_records,
        expected_record_count=2,
        expected_disagreement_count=1,
    )

    final_records[0]["evidence"]["adjudicator"][0]["quote"] = "fabricated quote"
    with pytest.raises(mod.GoldsetFinalizationError, match="absent from source"):
        mod._validate_adjudication_decision_integrity(
            integrity,
            adjudication_path=completed,
            worklist_integrity_path=worklist_integrity,
            dispute_path=disputes,
            upstream_paths=upstream_paths,
            final_records=final_records,
            expected_record_count=2,
            expected_disagreement_count=1,
        )
    final_records[0]["evidence"]["adjudicator"][0]["quote"] = (
        "세분류 01. 경영기획"
    )

    payload["applied_version"] = "untrusted_self_report"
    integrity.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(mod.GoldsetFinalizationError, match="applied version"):
        mod._validate_adjudication_decision_integrity(
            integrity,
            adjudication_path=completed,
            worklist_integrity_path=worklist_integrity,
            dispute_path=disputes,
            upstream_paths=upstream_paths,
            final_records=[],
            expected_record_count=2,
            expected_disagreement_count=1,
        )

    payload["applied_version"] = mod.ADJUDICATION_APPLIED_VERSION
    payload["adjudicated_count"] = 0
    integrity.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(mod.GoldsetFinalizationError, match="disagreement count"):
        mod._validate_adjudication_decision_integrity(
            integrity,
            adjudication_path=completed,
            worklist_integrity_path=worklist_integrity,
            dispute_path=disputes,
            upstream_paths=upstream_paths,
            final_records=final_records,
            expected_record_count=2,
            expected_disagreement_count=1,
        )


def test_human_gold_requires_explicit_human_kinds_and_attestation(
    tmp_path: Path,
) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(row, reviewer_id="human-a")
    for row in reviewer_b:
        complete_review(row, reviewer_id="human-b")

    with pytest.raises(mod.GoldsetFinalizationError, match="attestation"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
            reviewer_a_kind="human",
            reviewer_b_kind="human",
        )

    result = finalize_call(
        mod,
        paths,
        manifest,
        integrity,
        reviewer_a,
        reviewer_b,
        adjudication,
        reviewer_a_kind="human",
        reviewer_b_kind="human",
        human_gold_attestation=mod.HUMAN_ATTESTATION,
    )
    assert result["is_human_gold"] is True
    assert result["is_gold_accuracy"] is True
    assert result["evaluation_basis"] == (
        "independent_human_double_review_adjudicated_gold"
    )


def test_disagreement_requires_distinct_completed_third_party_decision(
    tmp_path: Path,
) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(row, reviewer_id="agent-a")
    for row in reviewer_b:
        complete_review(row, reviewer_id="agent-b")
    complete_review(
        reviewer_b[0],
        reviewer_id="agent-b",
        mapping_state="not_stated",
        names=[],
        codes=[],
    )

    with pytest.raises(mod.GoldsetFinalizationError, match="explicitly marked"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

    decision = adjudication[0]
    decision.update(
        {
            "agreement_status": "disagreement",
            "adjudication_status": "completed_third_party_adjudication",
            "adjudicator_id": "agent-c",
            "adjudicated_at_utc": "2026-08-25T02:00:00+00:00",
            "final_mapping_state": "official_current",
            "final_detail_names_json": '["경영기획"]',
            "final_detail_codes_json": '["02010101"]',
            "final_evidence_json": json.dumps(
                [{"text": "공식 세분류 경영기획", "section": "NCS 분류"}],
                ensure_ascii=False,
            ),
            "adjudication_rationale": "문서의 명시된 8자리 코드와 공식 명칭을 우선했다.",
        }
    )
    result = finalize_call(
        mod,
        paths,
        manifest,
        integrity,
        reviewer_a,
        reviewer_b,
        adjudication,
        adjudicator_kind="ai_agent",
        adjudicator_provenance="independent source adjudication agent C",
    )
    assert result["summary"]["disagreement_count"] == 1
    assert result["records"][0]["resolution_type"] == "third_party_adjudication"
    assert result["records"][0]["adjudicator_id"] == "agent-c"
    assert result["is_human_gold"] is False

    decision["adjudicator_id"] = "agent-a"
    with pytest.raises(mod.GoldsetFinalizationError, match="distinct third"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
            adjudicator_kind="ai_agent",
            adjudicator_provenance="agent C",
        )


def test_reviewer_identity_coverage_and_answer_format_fail_closed(
    tmp_path: Path,
) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(row, reviewer_id="same-reviewer")
    for row in reviewer_b:
        complete_review(row, reviewer_id="same-reviewer")
    with pytest.raises(mod.GoldsetFinalizationError, match="must be different"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

    reviewer_b[0]["reviewer_id"] = "reviewer-b"
    reviewer_b[1]["reviewer_id"] = "reviewer-b"
    reviewer_a[0]["detail_codes_json"] = '["0201010"]'
    with pytest.raises(mod.GoldsetFinalizationError, match="eight digits"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

    reviewer_a[0]["detail_codes_json"] = '["99999999"]'
    with pytest.raises(mod.GoldsetFinalizationError, match="current official catalog"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

    reviewer_a[0]["detail_codes_json"] = '["02010101"]'
    reviewer_a[0]["evidence_json"] = "[]"
    with pytest.raises(mod.GoldsetFinalizationError, match="source evidence"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

    reviewer_a[0]["evidence_json"] = '[{"quote":"evidence","page":1}]'
    reviewer_a[0]["reviewed_at_utc"] = "2026-08-25T10:00:00+09:00"
    with pytest.raises(mod.GoldsetFinalizationError, match="explicitly use UTC"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

    reviewer_a[0]["reviewed_at_utc"] = "2026-08-25T01:00:00Z"
    with pytest.raises(mod.GoldsetFinalizationError, match="coverage is incomplete"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a[:-1],
            reviewer_b,
            adjudication,
        )


def test_consensus_cannot_be_silently_overridden(tmp_path: Path) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(row, reviewer_id="agent-a")
    for row in reviewer_b:
        complete_review(row, reviewer_id="agent-b")
    adjudication[0]["final_mapping_state"] = "not_stated"
    adjudication[0]["final_detail_names_json"] = "[]"
    adjudication[0]["final_detail_codes_json"] = "[]"

    with pytest.raises(mod.GoldsetFinalizationError, match="unnecessary adjudication"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

def test_manifest_split_integrity_do_not_tune_and_prediction_schema_are_sealed(
    tmp_path: Path,
) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(row, reviewer_id="agent-a")
    for row in reviewer_b:
        complete_review(row, reviewer_id="agent-b")

    tampered_manifest = deepcopy(manifest)
    tampered_manifest["records"][0]["split"] = (
        "gold_validation"
        if tampered_manifest["records"][0]["split"] == "gold_holdout"
        else "gold_holdout"
    )
    with pytest.raises(mod.GoldsetFinalizationError, match="split invariant"):
        finalize_call(
            mod,
            paths,
            tampered_manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )

    bad_csv = tmp_path / "review_with_prediction.csv"
    with bad_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=[*mod.REVIEWER_FIELDS, "automatic_prediction"]
        )
        writer.writeheader()
    with pytest.raises(mod.GoldsetFinalizationError, match="automatic predictions"):
        mod._read_csv(bad_csv, fields=mod.REVIEWER_FIELDS, label="reviewer A")

    original = paths["do_not_tune"].read_text(encoding="utf-8-sig")
    paths["do_not_tune"].write_text(original + "\n", encoding="utf-8-sig")
    with pytest.raises(mod.GoldsetFinalizationError, match="integrity hash mismatch"):
        finalize_call(
            mod,
            paths,
            manifest,
            integrity,
            reviewer_a,
            reviewer_b,
            adjudication,
        )


def test_write_reference_is_local_only_and_hashes_every_input_output(
    tmp_path: Path,
) -> None:
    mod, paths, manifest, integrity, reviewer_a, reviewer_b, adjudication = seed(
        tmp_path
    )
    for row in reviewer_a:
        complete_review(row, reviewer_id="agent-a")
    for row in reviewer_b:
        complete_review(row, reviewer_id="agent-b")
    reference = finalize_call(
        mod,
        paths,
        manifest,
        integrity,
        reviewer_a,
        reviewer_b,
        adjudication,
    )
    source_paths = {
        "manifest": paths["manifest"],
        "seed_integrity": paths["integrity"],
        "do_not_tune": paths["do_not_tune"],
        "reviewer_a": paths["reviewer_a"],
        "reviewer_b": paths["reviewer_b"],
        "adjudication": paths["adjudication"],
    }
    output_paths = mod.write_final_reference(
        reference,
        mod.ROOT / "tmp" / "goldset" / "final",
        source_paths=source_paths,
    )
    final_integrity = json.loads(output_paths["integrity"].read_text(encoding="utf-8"))
    assert final_integrity["local_only"] is True
    assert final_integrity["is_human_gold"] is False
    assert set(final_integrity["source_artifact_sha256"]) == set(source_paths)
    assert set(final_integrity["output_artifact_sha256"]) == {
        "reference_json",
        "reference_csv",
    }
    for name, path in output_paths.items():
        assert path.is_file(), name
    with pytest.raises(mod.GoldsetFinalizationError, match="tmp/"):
        mod.write_final_reference(
            reference,
            mod.ROOT / "tracked",
            source_paths=source_paths,
        )
