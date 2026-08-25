from __future__ import annotations

import csv
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import httpx
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
        "posting_ids": [],
        "split_group_sha256": "",
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
    source_index = source_dir / "source_index.local.csv"
    with source_index.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["case_id", "local_document_path", "document_sha256"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "case_id": "case-validation",
                    "local_document_path": str((source_dir / "validation.pdf").resolve()),
                    "document_sha256": mod.sha256_bytes(validation_data),
                },
                {
                    "case_id": "case-holdout",
                    "local_document_path": str((source_dir / "holdout.txt").resolve()),
                    "document_sha256": mod.sha256_bytes(holdout_data),
                },
            ]
        )
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
    holdout_modulus = 0
    for candidate_modulus in range(2, 100):
        assignments = mod.compute_split_groups(
            records,
            holdout_modulus=candidate_modulus,
        )
        validation_assignment = assignments[records[0]["document_sha256"]]
        holdout_assignment = assignments[records[1]["document_sha256"]]
        if (
            validation_assignment["split"] == "gold_validation"
            and holdout_assignment["split"] == "gold_holdout"
        ):
            holdout_modulus = candidate_modulus
            break
    assert holdout_modulus >= 2
    for record in records:
        record.update(
            mod.compute_split_groups(
                records,
                holdout_modulus=holdout_modulus,
            )[record["document_sha256"]]
        )
    reference = {
        "schema_version": mod.REFERENCE_SCHEMA_VERSION,
        "generated_at_utc": "2026-08-25T00:00:00+00:00",
        "evaluation_basis": mod.AI_EVALUATION_BASIS,
        "is_human_gold": False,
        "is_gold_accuracy": False,
        "is_gold": False,
        "human_gold_attestation": {
            "version": mod.HUMAN_ATTESTATION_VERSION,
            "attested": False,
            "statement": "",
        },
        "automatic_predictions_are_gold": False,
        "usage_policy": mod.USAGE_POLICY,
        "candidate_exclusion_provenance": {
            "seed_integrity_version": mod.SEED_INTEGRITY_VERSION,
            "applied": False,
            "version": mod.CANDIDATE_EXCLUSION_AUDIT_VERSION,
            "audit_sha256": None,
        },
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
            "holdout_modulus": holdout_modulus,
            "split_key": mod.SPLIT_KEY,
            "split_group_count": 2,
            "posting_id_cross_split_overlap_count": 0,
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
                    "posting_ids_json": json.dumps(record["posting_ids"]),
                    "split_group_sha256": record["split_group_sha256"],
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
        "human_gold_attestation": reference["human_gold_attestation"],
        "human_gold_attestation_sha256": mod.sha256_bytes(
            mod.canonical_json_bytes(reference["human_gold_attestation"])
        ),
        "candidate_exclusion_provenance": reference[
            "candidate_exclusion_provenance"
        ],
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
        "source_index": source_index,
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
    assert score["summary"]["official_current_core"]["eligible_document_count"] == 1
    assert score["summary"]["official_current_core"]["document_exact_pct"] == 100.0
    holdout_core = score["summary"]["official_current_core_by_split"][
        "gold_holdout"
    ]
    assert holdout_core["eligible_document_count"] == 0
    assert holdout_core["document_exact_pct"] is None
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


def test_candidate_exclusion_provenance_is_required_and_source_bound(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_candidate_provenance_{tmp_path.name}")
    missing = _write_bundle(tmp_path / "missing", mod)
    missing_reference = missing["reference"]
    missing_reference.pop("candidate_exclusion_provenance")
    missing["reference_json"].write_text(
        json.dumps(missing_reference, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    missing_integrity = json.loads(
        missing["integrity"].read_text(encoding="utf-8")
    )
    missing_integrity.pop("candidate_exclusion_provenance")
    missing_integrity["output_artifact_sha256"]["reference_json"] = (
        mod.sha256_file(missing["reference_json"])
    )
    missing["integrity"].write_text(
        json.dumps(missing_integrity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(mod.GoldsetScoringError, match="missing or invalid"):
        mod.validate_reference_bundle(
            missing["reference_json"],
            missing["reference_csv"],
            missing["integrity"],
        )

    unbound = _write_bundle(tmp_path / "unbound", mod)
    applied = {
        "seed_integrity_version": mod.SEED_INTEGRITY_VERSION,
        "applied": True,
        "version": mod.CANDIDATE_EXCLUSION_AUDIT_VERSION,
        "audit_sha256": "a" * 64,
    }
    unbound["reference"]["candidate_exclusion_provenance"] = applied
    unbound["reference_json"].write_text(
        json.dumps(unbound["reference"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    unbound_integrity = json.loads(
        unbound["integrity"].read_text(encoding="utf-8")
    )
    unbound_integrity["candidate_exclusion_provenance"] = applied
    unbound_integrity["output_artifact_sha256"]["reference_json"] = (
        mod.sha256_file(unbound["reference_json"])
    )
    unbound["integrity"].write_text(
        json.dumps(unbound_integrity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(mod.GoldsetScoringError, match="not bound"):
        mod.validate_reference_bundle(
            unbound["reference_json"],
            unbound["reference_csv"],
            unbound["integrity"],
        )


def test_document_exact_requires_correct_official_name_code_pairing(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_pairing_{tmp_path.name}")
    catalog = json.loads(
        (ROOT / "app" / "data" / "ncs_detail_catalog.json").read_text(
            encoding="utf-8-sig"
        )
    )
    active = [row for row in catalog["details"] if row.get("usage_yn") == "Y"][:2]
    assert len(active) == 2
    data = b"paired detail labels"
    source_path = tmp_path / "paired.txt"
    source_path.write_bytes(data)
    record = _record(
        mod,
        data,
        split="gold_validation",
        case_id="case-paired",
        mapping_state="official_current",
        names=[active[0]["name"], active[1]["name"]],
        codes=[active[0]["code"], active[1]["code"]],
    )

    def parse(_path: Path, _data: bytes):
        return {
            "fields": {
                "ncs_detail_mapping_states": [
                    {
                        "mappingState": "official_current_exact",
                        "officialDetailNames": [active[0]["name"], active[1]["name"]],
                        "officialDetailCodes": [active[1]["code"], active[0]["code"]],
                    }
                ]
            }
        }

    score = mod.score_reference(
        {"records": [record]},
        {mod.sha256_bytes(data): source_path},
        parse,
    )

    row = score["records"][0]
    assert row["name_exact"] is True
    assert row["code_exact"] is True
    assert row["pair_exact"] is False
    assert row["document_exact"] is False
    assert row["error_types"] == ["detail_pair_mismatch"]
    pair_metrics = score["summary"]["overall"]["detail_pair"]
    assert pair_metrics["true_positive"] == 0
    assert pair_metrics["false_positive"] == 2
    assert pair_metrics["false_negative"] == 2
    assert pair_metrics["f1_pct"] == 0.0


def test_legacy_v1_reference_is_rejected_even_when_resealed(tmp_path: Path) -> None:
    mod = load_module(f"score_goldset_legacy_v1_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = deepcopy(seeded["reference"])
    reference["schema_version"] = "ncs_recruitment_adjudicated_reference_v1"
    _reseal_reference(mod, seeded, reference)
    integrity = json.loads(seeded["integrity"].read_text(encoding="utf-8"))
    integrity["schema_version"] = "ncs_recruitment_adjudicated_reference_v1"
    seeded["integrity"].write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(mod.GoldsetScoringError, match="schema version"):
        mod.validate_reference_bundle(
            seeded["reference_json"],
            seeded["reference_csv"],
            seeded["integrity"],
        )


def test_validation_only_mode_never_reads_or_parses_holdout_source(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_validation_only_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    calls: list[bytes] = []
    real_sha256_file = mod.sha256_file

    def audited_sha256(path: Path) -> str:
        assert path.name != "holdout.txt"
        return real_sha256_file(path)

    mod.sha256_file = audited_sha256

    def parse(_path: Path, data: bytes):
        calls.append(data)
        return _exact_payload(seeded["name"], seeded["code"])

    score, source_paths = mod.evaluate_bundle(
        seeded["reference_json"],
        seeded["reference_csv"],
        seeded["integrity"],
        seeded["source_dir"],
        parse,
        include_splits={"gold_validation"},
        source_index_path=seeded["source_index"],
    )

    assert calls == [seeded["validation_data"]]
    assert set(source_paths) == {mod.sha256_bytes(seeded["validation_data"])}
    assert score["evaluated_splits"] == ["gold_validation"]
    assert score["summary"]["record_count"] == 1
    assert score["summary"]["by_split"]["gold_holdout"]["document_count"] == 0


def test_split_filter_rejects_unknown_or_empty_selection(tmp_path: Path) -> None:
    mod = load_module(f"score_goldset_bad_split_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)

    for selected in (set(), {"not-a-split"}):
        with pytest.raises(mod.GoldsetScoringError, match="known split"):
            mod.evaluate_bundle(
                seeded["reference_json"],
                seeded["reference_csv"],
                seeded["integrity"],
                seeded["source_dir"],
                lambda _path, _data: _not_stated_payload(),
                include_splits=selected,
                source_index_path=seeded["source_index"],
            )


def test_split_filter_requires_source_index_before_scanning_sources(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_missing_index_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)

    with pytest.raises(mod.GoldsetScoringError, match="requires a private source index"):
        mod.evaluate_bundle(
            seeded["reference_json"],
            seeded["reference_csv"],
            seeded["integrity"],
            seeded["source_dir"],
            lambda _path, _data: _not_stated_payload(),
            include_splits={"gold_validation"},
        )


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


def test_human_gold_reference_requires_persisted_attestation(tmp_path: Path) -> None:
    mod = load_module(f"score_goldset_attestation_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = deepcopy(seeded["reference"])
    reference["review_provenance"]["reviewer_a"]["reviewer_kind"] = "human"
    reference["review_provenance"]["reviewer_b"]["reviewer_kind"] = "human"
    reference["evaluation_basis"] = mod.HUMAN_EVALUATION_BASIS
    reference["is_human_gold"] = True
    reference["is_gold_accuracy"] = True
    reference["is_gold"] = True
    reference.pop("human_gold_attestation")
    _reseal_reference(mod, seeded, reference)

    with pytest.raises(mod.GoldsetScoringError, match="attestation"):
        mod.validate_reference_bundle(
            seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
        )


def test_json_csv_comparison_precedes_nfkc_label_normalization(tmp_path: Path) -> None:
    mod = load_module(f"score_goldset_nfkc_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = deepcopy(seeded["reference"])
    record = reference["records"][0]
    record["mapping_state"] = "self_developed"
    record["detail_names"] = ["사용자정의･직무"]
    record["detail_codes"] = []
    reference["records_sha256"] = mod.sha256_bytes(
        mod.canonical_json_bytes(reference["records"])
    )

    with seeded["reference_csv"].open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    rows[0]["mapping_state"] = "self_developed"
    rows[0]["detail_names_json"] = json.dumps(
        ["사용자정의･직무"], ensure_ascii=False
    )
    rows[0]["detail_codes_json"] = "[]"
    with seeded["reference_csv"].open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=mod.FINAL_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    _reseal_reference(mod, seeded, reference)
    integrity = json.loads(seeded["integrity"].read_text(encoding="utf-8"))
    integrity["records_sha256"] = reference["records_sha256"]
    integrity["output_artifact_sha256"]["reference_csv"] = mod.sha256_file(
        seeded["reference_csv"]
    )
    seeded["integrity"].write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    validated = mod.validate_reference_bundle(
        seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
    )

    assert validated["records"][0]["detail_names"] == ["사용자정의・직무"]


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
        require_runtime_attestation=False,
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
            require_runtime_attestation=False,
        )


def test_unattested_score_is_rejected_before_output_directory_creation(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_unattested_write_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    score, source_paths = mod.evaluate_bundle(
        seeded["reference_json"],
        seeded["reference_csv"],
        seeded["integrity"],
        seeded["source_dir"],
        lambda _path, _data: _not_stated_payload(),
    )
    output_dir = tmp_path / "tmp" / "unattested-score"
    with pytest.raises(mod.GoldsetScoringError, match="attested"):
        mod.write_score_report(
            score,
            output_dir,
            reference_paths={
                "reference_json": seeded["reference_json"],
                "reference_csv": seeded["reference_csv"],
                "reference_integrity": seeded["integrity"],
            },
            source_paths=source_paths,
        )
    assert not output_dir.exists()


def test_parse_client_rejects_non_loopback_endpoints_without_network(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_http_{tmp_path.name}")
    with pytest.raises(mod.GoldsetScoringError, match="loopback"):
        mod.LocalParseReviewClient("https://example.com")
    with pytest.raises(mod.GoldsetScoringError, match="path/query"):
        mod.LocalParseReviewClient("http://127.0.0.1:8000/not-local-root")


def test_private_bytes_are_not_uploaded_before_local_only_policy_preflight(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_preflight_{tmp_path.name}")
    request_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(request.content)
        return httpx.Response(404, json={"detail": "missing policy contract"})

    client = mod.LocalParseReviewClient("http://127.0.0.1:8000")
    client._client.close()
    client._client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    try:
        with pytest.raises(
            mod.GoldsetScoringInfrastructureError,
            match="local-only parser policy",
        ):
            client.parse(
                tmp_path / "private.pdf",
                b"PRIVATE-DOCUMENT-BYTES",
            )
    finally:
        client.close()

    assert request_bodies == [b""]


def test_runtime_attestation_v2_seals_parser_build_closure(tmp_path: Path) -> None:
    mod = load_module(f"score_goldset_runtime_v2_{tmp_path.name}")

    attestation = mod.local_runtime_attestation()

    assert attestation["schema_version"] == (
        "ncscope_evaluation_runtime_attestation_v2"
    )
    assert set(attestation["source_artifact_sha256"]) == {
        "app_main",
        "kordoc_parser",
        "ncs_mcp_client",
        "request_budget",
        "official_detail_catalog",
        "kordoc_local_runner",
        "kordoc_serverless_bridge",
        "package_json",
        "package_lock",
        "vercel_config",
    }
    assert attestation["parser_contract"]["package_version"] == "4.9.1"
    declared_bundle_digest = attestation.pop("runtime_bundle_sha256")
    assert declared_bundle_digest == mod.sha256_bytes(
        mod.canonical_json_bytes(attestation)
    )


def test_runtime_attestation_rejects_non_object_lock_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_module(f"score_goldset_non_object_lock_{tmp_path.name}")
    original_read_text = Path.read_text
    snapshot_path = (
        mod.ROOT / "app" / "data" / "node_package_lock_attestation.json"
    )

    def non_object_snapshot(path: Path, *args, **kwargs) -> str:
        if path == snapshot_path:
            return "[]"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", non_object_snapshot)

    with pytest.raises(
        mod.GoldsetScoringError,
        match="package lock attestation is invalid",
    ):
        mod.local_runtime_attestation()


def test_private_scorer_rejects_remote_bridge_execution(tmp_path: Path) -> None:
    mod = load_module(f"score_goldset_remote_mode_{tmp_path.name}")
    client = mod.LocalParseReviewClient("http://127.0.0.1:8000")
    try:
        with pytest.raises(
            mod.GoldsetScoringInfrastructureError,
            match="not allowed for scoring",
        ):
            client._validate_parser_executions(
                {
                    "parser_executions": [
                        {
                            "schema_version": "ncscope_parser_execution_v1",
                            "role": "selected",
                            "parser": "kordoc",
                            "mode": "authenticated_serverless_bridge",
                            "parser_version": "4.9.1",
                            "node_version": "24.0.0",
                            "build_identity": {"kind": "vercel_deployment"},
                            "runtime_bundle_sha256": (
                                client._expected_runtime_attestation[
                                    "runtime_bundle_sha256"
                                ]
                            ),
                        }
                    ]
                }
            )
    finally:
        client.close()


def test_parse_client_retries_rate_limit_with_bounded_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_module(f"score_goldset_retry_{tmp_path.name}")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/runtime-policy"):
            return httpx.Response(
                200,
                json={
                    "evaluation_runtime_attestation": (
                        client._expected_runtime_attestation
                    ),
                    "supported_parser_policies": ["local-only"],
                    "policy_header": "x-ncscope-parser-policy",
                },
            )
        assert request.headers["x-ncscope-parser-policy"] == "local-only"
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "999"})
        return httpx.Response(
            200,
            json={
                "fields": {"ncs_detail_mapping_states": []},
                "evaluation_runtime_attestation": (
                    client._expected_runtime_attestation
                ),
                "parser_executions": [
                    {
                        "schema_version": "ncscope_parser_execution_v1",
                        "role": "selected",
                        "parser": "kordoc",
                        "mode": "local_node_subprocess",
                        "parser_version": client._expected_runtime_attestation[
                            "parser_contract"
                        ]["package_version"],
                        "node_version": "24.0.0",
                        "build_identity": {"kind": "local_source_bundle"},
                        "runtime_bundle_sha256": client._expected_runtime_attestation[
                            "runtime_bundle_sha256"
                        ],
                    }
                ],
            },
        )

    sleeps: list[float] = []
    client = mod.LocalParseReviewClient(
        "http://127.0.0.1:8000", max_retries=2, max_retry_after_seconds=3
    )
    client._client.close()
    client._client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)
    try:
        payload = client.parse(tmp_path / "document.pdf", b"source")
    finally:
        client.close()

    assert payload["fields"]["ncs_detail_mapping_states"] == []
    assert attempts == 2
    assert sleeps == [3.0]


def test_parse_client_rejects_unattested_or_stale_loopback_runtime(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_attestation_{tmp_path.name}")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "fields": {"ncs_detail_mapping_states": []},
                "evaluation_runtime_attestation": {
                    "schema_version": "ncscope_evaluation_runtime_attestation_v1",
                    "app_version": "stale",
                    "source_artifact_sha256": {},
                },
            },
        )

    client = mod.LocalParseReviewClient("http://127.0.0.1:8000")
    client._client.close()
    client._client = httpx.Client(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    try:
        with pytest.raises(
            mod.GoldsetScoringInfrastructureError,
            match="runtime attestation",
        ):
            client.parse(tmp_path / "document.pdf", b"source")
    finally:
        client.close()


def test_parse_client_finalize_rejects_sources_changed_during_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_module(f"score_goldset_finalize_drift_{tmp_path.name}")
    client = mod.LocalParseReviewClient("http://127.0.0.1:8000")
    client.evaluation_configuration["server_runtime_attestation"] = dict(
        client._expected_runtime_attestation
    )
    changed = deepcopy(client._expected_runtime_attestation)
    changed["source_artifact_sha256"]["official_detail_catalog"] = "0" * 64
    monkeypatch.setattr(mod, "local_runtime_attestation", lambda: changed)
    try:
        with pytest.raises(
            mod.GoldsetScoringInfrastructureError,
            match="sources changed",
        ):
            client.finalize_runtime_attestation()
    finally:
        client.close()


def test_score_writer_rejects_missing_parser_execution_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = load_module(f"score_goldset_writer_provenance_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    score, source_paths = mod.evaluate_bundle(
        seeded["reference_json"],
        seeded["reference_csv"],
        seeded["integrity"],
        seeded["source_dir"],
        lambda _path, _data: _not_stated_payload(),
    )
    attestation = mod.local_runtime_attestation()
    scorer_sources = mod.local_scorer_source_artifact_sha256()
    score["evaluation_runtime"] = {
        "runtime_attested": True,
        "server_runtime_attestation": attestation,
        "scorer_source_artifact_sha256": scorer_sources,
        "allowed_parser_modes": [
            "builtin_plain_text",
            "local_node_subprocess",
        ],
        "parser_execution_identities": [],
    }
    monkeypatch.setattr(mod, "local_runtime_attestation", lambda: attestation)
    monkeypatch.setattr(
        mod,
        "local_scorer_source_artifact_sha256",
        lambda: scorer_sources,
    )
    fake_root = tmp_path / "writer-root"
    fake_root.mkdir()
    mod.ROOT = fake_root
    output_dir = fake_root / "tmp" / "score"

    with pytest.raises(mod.GoldsetScoringError, match="execution provenance"):
        mod.write_score_report(
            score,
            output_dir,
            reference_paths={
                "reference_json": seeded["reference_json"],
                "reference_csv": seeded["reference_csv"],
                "reference_integrity": seeded["integrity"],
            },
            source_paths=source_paths,
        )
    assert not output_dir.exists()

    base_execution = {
        "schema_version": "ncscope_parser_execution_v1",
        "role": "selected",
        "parser": "kordoc",
        "mode": "local_node_subprocess",
        "parser_version": attestation["parser_contract"]["package_version"],
        "build_identity": {"kind": "local_source_bundle"},
        "runtime_bundle_sha256": attestation["runtime_bundle_sha256"],
    }
    score["evaluation_runtime"]["parser_execution_identities"] = [
        {**base_execution, "node_version": "24.14.0"},
        {**base_execution, "node_version": "24.15.0"},
    ]
    with pytest.raises(mod.GoldsetScoringError, match="identity drifted"):
        mod.write_score_report(
            score,
            output_dir,
            reference_paths={
                "reference_json": seeded["reference_json"],
                "reference_csv": seeded["reference_csv"],
                "reference_integrity": seeded["integrity"],
            },
            source_paths=source_paths,
        )
    assert not output_dir.exists()


def test_infrastructure_error_aborts_score_instead_of_polluting_accuracy(
    tmp_path: Path,
) -> None:
    mod = load_module(f"score_goldset_infra_{tmp_path.name}")
    seeded = _write_bundle(tmp_path, mod)
    reference = mod.validate_reference_bundle(
        seeded["reference_json"], seeded["reference_csv"], seeded["integrity"]
    )
    required = {row["document_sha256"] for row in reference["records"]}
    source_paths = mod.index_source_documents(seeded["source_dir"], required)

    def unavailable(_path: Path, _data: bytes):
        raise mod.GoldsetScoringInfrastructureError("HTTP 429")

    with pytest.raises(mod.GoldsetScoringInfrastructureError, match="HTTP 429"):
        mod.score_reference(reference, source_paths, unavailable)
