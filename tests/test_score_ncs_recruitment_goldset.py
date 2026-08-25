from __future__ import annotations

import csv
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "score_ncs_recruitment_goldset.py"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _official_pair() -> tuple[str, str]:
    payload = json.loads(
        (ROOT / "app" / "data" / "ncs_detail_catalog.json").read_text(
            encoding="utf-8-sig"
        )
    )
    row = next(item for item in payload["details"] if item.get("usage_yn") == "Y")
    return row["code"], row["name"]


def _record(
    mod,
    data: bytes,
    *,
    split: str,
    case_id: str,
    mapping_state: str,
    names: list[str],
    codes: list[str],
) -> dict[str, object]:
    digest = mod.sha256_bytes(data)
    return {
        "item_id": f"nrg-{digest}",
        "split": split,
        "document_sha256": digest,
        "case_ids": [case_id],
        "mapping_state": mapping_state,
        "detail_names": names,
        "detail_codes": codes,
        "evidence": {
            "reviewer_a": [{"quote": "source evidence", "page": 1}],
            "reviewer_b": [{"quote": "source evidence", "page": 1}],
        },
        "resolution_type": "exact_two_reviewer_consensus",
        "reviewer_a_id": "agent-a",
        "reviewer_b_id": "agent-b",
        "adjudicator_id": "",
        "usage_policy": mod.USAGE_POLICY,
    }


def _write_bundle(tmp_path: Path, mod):
    code, name = _official_pair()
    source_dir = tmp_path / "private"
    source_dir.mkdir(parents=True)
    validation_data = b"private validation document"
    holdout_data = b"private holdout document"
    (source_dir / "validation.pdf").write_bytes(validation_data)
    (source_dir / "holdout.txt").write_bytes(holdout_data)
    records = [
        _record(
            mod,
            validation_data,
            split="gold_validation",
            case_id="case-validation",
            mapping_state="official_current",
            names=[name],
            codes=[code],
        ),
        _record(
            mod,
            holdout_data,
            split="gold_holdout",
            case_id="case-holdout",
            mapping_state="not_stated",
            names=[],
            codes=[],
        ),
    ]
    reference = {
        "schema_version": mod.REFERENCE_SCHEMA_VERSION,
        "generated_at_utc": "2026-08-25T00:00:00+00:00",
        "evaluation_basis": mod.AI_EVALUATION_BASIS,
        "is_human_gold": False,
        "is_gold_accuracy": False,
        "is_gold": False,
        "automatic_predictions_are_gold": False,
        "usage_policy": mod.USAGE_POLICY,
        "review_provenance": {
            "reviewer_a": {
                "reviewer_id": "agent-a",
                "reviewer_kind": "ai_agent",
                "provenance": "isolated source-document review A",
            },
            "reviewer_b": {
                "reviewer_id": "agent-b",
                "reviewer_kind": "ai_agent",
                "provenance": "isolated source-document review B",
            },
            "adjudicator": None,
        },
        "policy": {"tuning": mod.USAGE_POLICY},
        "summary": {
            "record_count": 2,
            "case_count": 2,
            "split_counts": {"gold_holdout": 1, "gold_validation": 1},
            "resolution_counts": {"exact_two_reviewer_consensus": 2},
            "disagreement_count": 0,
        },
        "source_records_sha256": "a" * 64,
        "records_sha256": mod.sha256_bytes(mod.canonical_json_bytes(records)),
        "records": records,
    }
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    reference_json = final_dir / "ncs_recruitment_final_reference.local.json"
    reference_csv = final_dir / "ncs_recruitment_final_reference.local.csv"
    integrity_path = final_dir / "final_integrity.local.json"
    reference_json.write_text(
        json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with reference_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=mod.FINAL_CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "item_id": record["item_id"],
                    "split": record["split"],
                    "document_sha256": record["document_sha256"],
                    "case_ids_json": json.dumps(record["case_ids"]),
                    "mapping_state": record["mapping_state"],
                    "detail_names_json": json.dumps(
                        record["detail_names"], ensure_ascii=False
                    ),
                    "detail_codes_json": json.dumps(record["detail_codes"]),
                    "evidence_json": json.dumps(
                        record["evidence"], ensure_ascii=False
                    ),
                    "resolution_type": record["resolution_type"],
                    "reviewer_a_id": record["reviewer_a_id"],
                    "reviewer_b_id": record["reviewer_b_id"],
                    "adjudicator_id": record["adjudicator_id"],
                    "evaluation_basis": reference["evaluation_basis"],
                    "is_human_gold": "false",
                    "is_gold_accuracy": "false",
                    "usage_policy": mod.USAGE_POLICY,
                }
            )
    integrity = {
        "schema_version": mod.REFERENCE_SCHEMA_VERSION,
        "generated_at_utc": "2026-08-25T00:00:00+00:00",
        "evaluation_basis": reference["evaluation_basis"],
        "is_human_gold": False,
        "is_gold_accuracy": False,
        "automatic_predictions_are_gold": False,
        "usage_policy": mod.USAGE_POLICY,
        "local_only": True,
        "records_sha256": reference["records_sha256"],
        "source_artifact_sha256": {"manifest": "b" * 64},
        "output_artifact_sha256": {
            "reference_json": mod.sha256_file(reference_json),
            "reference_csv": mod.sha256_file(reference_csv),
        },
    }
    integrity_path.write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "reference": reference,
        "reference_json": reference_json,
        "reference_csv": reference_csv,
        "integrity": integrity_path,
        "source_dir": source_dir,
        "validation_data": validation_data,
        "holdout_data": holdout_data,
        "code": code,
        "name": name,
    }


def _reseal_reference(mod, seeded, reference: dict[str, object]) -> None:
    seeded["reference_json"].write_text(
        json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    integrity = json.loads(seeded["integrity"].read_text(encoding="utf-8"))
    integrity["output_artifact_sha256"]["reference_json"] = mod.sha256_file(
        seeded["reference_json"]
    )
    seeded["integrity"].write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _exact_payload(name: str, code: str) -> dict[str, object]:
    return {
        "fields": {
            "ncs_detail_mapping_states": [
                {
                    "sourceName": name,
                    "mappingState": "official_current_exact",
                    "officialDetailNames": [name],
                    "officialDetailCodes": [code],
                }
            ]
        }
    }


def _not_stated_payload() -> dict[str, object]:
    return {
        "fields": {
            "ncs_detail_mapping_states": [],
            "ncs_detail_candidates": [],
            "ncs_detail_absence_state": "declared_no_mapping",
        }
    }


def test_scores_validation_and_holdout_without_calling_it_human_gold(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    calls: list[bytes] = []

    def parse(_path: Path, data: bytes):
        calls.append(data)
        if data == seeded["validation_data"]:
            return _exact_payload(seeded["name"], seeded["code"])
        return _not_stated_payload()

    score, source_paths = mod.evaluate_bundle(
        seeded["reference_json"],
        seeded["reference_csv"],
        seeded["integrity"],
        seeded["source_dir"],
        parse,
    )

    assert len(calls) == 2
    assert set(source_paths) == {
        mod.sha256_bytes(seeded["validation_data"]),
        mod.sha256_bytes(seeded["holdout_data"]),
    }
    assert score["metrics_are_human_gold_accuracy"] is False
    assert score["metrics_interpretation"] == (
        "ai_adjudicated_reference_comparison_not_human_gold_accuracy"
    )
    assert score["summary"]["overall"]["document_exact_pct"] == 100.0
    assert score["summary"]["by_split"]["gold_validation"]["detail_name"] == {
        "precision_pct": 100.0,
        "recall_pct": 100.0,
        "f1_pct": 100.0,
        "true_positive": 1,
        "false_positive": 0,
        "false_negative": 0,
        "precision_denominator": 1,
        "recall_denominator": 1,
        "f1_denominator": 2,
        "undefined_metrics": [],
    }
    holdout_name = score["summary"]["by_split"]["gold_holdout"]["detail_name"]
    assert holdout_name["precision_pct"] is None
    assert holdout_name["recall_pct"] is None
    assert holdout_name["f1_pct"] is None
    assert holdout_name["undefined_metrics"] == [
        "precision_pct",
        "recall_pct",
        "f1_pct",
    ]


def test_mismatches_and_parse_failures_are_preserved_in_error_ledger(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_mismatch_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)

    def parse(_path: Path, data: bytes):
        if data == seeded["validation_data"]:
            return _not_stated_payload()
        raise RuntimeError("parser unavailable")

    score, _source_paths = mod.evaluate_bundle(
        seeded["reference_json"],
        seeded["reference_csv"],
        seeded["integrity"],
        seeded["source_dir"],
        parse,
    )

    assert score["summary"]["error_case_count"] == 2
    assert score["summary"]["overall"]["detail_name"]["false_negative"] == 1
    assert score["summary"]["overall"]["mapping_state_accuracy_pct"] == 0.0
    parse_error = next(
        row for row in score["error_cases"] if row["parse_status"] == "error"
    )
    assert parse_error["predicted_mapping_state"] == "unreadable"
    assert parse_error["error_types"] == ["parse_error", "mapping_state_mismatch"]
    assert "parser unavailable" in parse_error["error_message"]


def test_reference_tampering_fails_before_injected_parser_runs(tmp_path: Path) -> None:
    mod = load_module(f"score_goldset_tamper_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = deepcopy(seeded["reference"])
    reference["automatic_predictions_are_gold"] = True
    _reseal_reference(mod, seeded, reference)
    calls = 0

    def parse(_path: Path, _data: bytes):
        nonlocal calls
        calls += 1
        return _not_stated_payload()

    with pytest.raises(mod.GoldsetScoringError, match="automatic predictions"):
        mod.evaluate_bundle(
            seeded["reference_json"],
            seeded["reference_csv"],
            seeded["integrity"],
            seeded["source_dir"],
            parse,
        )
    assert calls == 0


def test_json_csv_integrity_provenance_and_split_contracts_fail_closed(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_contract_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = deepcopy(seeded["reference"])
    reference["review_provenance"]["reviewer_b"]["reviewer_id"] = "agent-a"
    _reseal_reference(mod, seeded, reference)
    with pytest.raises(mod.GoldsetScoringError, match="independent identities"):
        mod.validate_reference_bundle(
            seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
        )


def test_record_resolution_and_reviewer_provenance_fail_closed(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_resolution_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = deepcopy(seeded["reference"])
    reference["records"][0]["reviewer_a_id"] = "different-agent"
    reference["records_sha256"] = mod.sha256_bytes(
        mod.canonical_json_bytes(reference["records"])
    )
    _reseal_reference(mod, seeded, reference)
    integrity = json.loads(seeded["integrity"].read_text(encoding="utf-8"))
    integrity["records_sha256"] = reference["records_sha256"]
    seeded["integrity"].write_text(json.dumps(integrity), encoding="utf-8")
    with pytest.raises(mod.GoldsetScoringError, match="reviewer identities"):
        mod.validate_reference_bundle(
            seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
        )

    seeded = _write_bundle(tmp_path / "resolution-case", mod)
    reference = deepcopy(seeded["reference"])
    reference["summary"]["disagreement_count"] = 1
    _reseal_reference(mod, seeded, reference)
    with pytest.raises(mod.GoldsetScoringError, match="disagreement count"):
        mod.validate_reference_bundle(
            seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
        )

    seeded = _write_bundle(tmp_path / "csv-case", mod)
    with seeded["reference_csv"].open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    rows[0]["mapping_state"] = "not_stated"
    with seeded["reference_csv"].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=mod.FINAL_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    integrity = json.loads(seeded["integrity"].read_text(encoding="utf-8"))
    integrity["output_artifact_sha256"]["reference_csv"] = mod.sha256_file(
        seeded["reference_csv"]
    )
    seeded["integrity"].write_text(json.dumps(integrity), encoding="utf-8")
    with pytest.raises(mod.GoldsetScoringError, match="JSON/CSV scalar mismatch"):
        mod.validate_reference_bundle(
            seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
        )


def test_source_hash_coverage_duplicates_and_post_index_changes_fail_closed(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_source_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = mod.validate_reference_bundle(
        seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
    )
    required = {row["document_sha256"] for row in reference["records"]}
    duplicate = seeded["source_dir"] / "duplicate.bin"
    duplicate.write_bytes(seeded["validation_data"])
    with pytest.raises(mod.GoldsetScoringError, match="duplicate private source"):
        mod.index_source_documents(seeded["source_dir"], required)

    duplicate.unlink()
    paths = mod.index_source_documents(seeded["source_dir"], required)
    validation_digest = mod.sha256_bytes(seeded["validation_data"])
    paths[validation_digest].write_bytes(b"changed after indexing")
    with pytest.raises(mod.GoldsetScoringError, match="changed after indexing"):
        mod.score_reference(reference, paths, lambda _path, _data: _not_stated_payload())


def test_writes_private_reports_only_below_tmp_and_seals_output_hashes(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_write_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    score, source_paths = mod.evaluate_bundle(
        seeded["reference_json"],
        seeded["reference_csv"],
        seeded["integrity"],
        seeded["source_dir"],
        lambda _path, data: (
            _exact_payload(seeded["name"], seeded["code"])
            if data == seeded["validation_data"]
            else _not_stated_payload()
        ),
    )
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    mod.ROOT = fake_root
    reference_paths = {
        "reference_json": seeded["reference_json"],
        "reference_csv": seeded["reference_csv"],
        "reference_integrity": seeded["integrity"],
    }
    paths = mod.write_score_report(
        score,
        fake_root / "tmp" / "score",
        reference_paths=reference_paths,
        source_paths=source_paths,
    )
    assert set(paths) == {"score_json", "error_csv", "score_markdown", "integrity"}
    assert all(path.is_file() for path in paths.values())
    markdown = paths["score_markdown"].read_text(encoding="utf-8")
    assert "not be described as human-gold accuracy" in markdown
    integrity = json.loads(paths["integrity"].read_text(encoding="utf-8"))
    assert integrity["automatic_predictions_are_reference_labels"] is False
    assert integrity["metrics_are_human_gold_accuracy"] is False
    assert set(integrity["output_artifact_sha256"]) == {
        "score_json",
        "error_csv",
        "score_markdown",
    }
    with pytest.raises(mod.GoldsetScoringError, match="tmp/"):
        mod.write_score_report(
            score,
            fake_root / "tracked",
            reference_paths=reference_paths,
            source_paths=source_paths,
        )


def test_parse_client_rejects_non_loopback_endpoints_without_network(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_http_{tmp_path.name}")
    with pytest.raises(mod.GoldsetScoringError, match="loopback"):
        mod.LocalParseReviewClient("https://example.com")
    with pytest.raises(mod.GoldsetScoringError, match="path/query"):
        mod.LocalParseReviewClient("http://127.0.0.1:8000/not-local-root")
