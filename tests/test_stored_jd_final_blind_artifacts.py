from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
RELEASE_TAG = "v1.4.12"
RELEASE_COMMIT = "57352c7e988767105b1fd68ac5aad73cb299a2e8"


def _git_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _release_git_text_sha256(relative: str) -> str:
    content = subprocess.run(
        ["git", "show", f"{RELEASE_COMMIT}:{relative}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _release_tag_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", f"{RELEASE_TAG}^{{}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_final_blind_freeze_preserves_pre_change_result_and_discloses_drift() -> None:
    freeze = json.loads(
        (FIXTURES / "stored_jd_final_blind_freeze.json").read_text(encoding="utf-8")
    )
    addendum = json.loads(
        (FIXTURES / "stored_jd_final_blind_freeze_addendum.json").read_text(
            encoding="utf-8"
        )
    )
    assert _release_tag_commit() == RELEASE_COMMIT
    for immutable_relative in (
        "tests/fixtures/stored_jd_final_blind_freeze.json",
        "tests/fixtures/stored_jd_final_blind_freeze_addendum.json",
        "tests/fixtures/stored_jd_final_blind_reference.json",
        "tests/fixtures/stored_jd_final_blind_result.json",
        "tests/fixtures/stored_jd_final_blind_comparison_ledger.json",
    ):
        assert _git_text_sha256(ROOT / immutable_relative) == (
            _release_git_text_sha256(immutable_relative)
        )

    git_text_hashes = addendum["git_text_sha256"]
    assert addendum["git_text_hash_canonicalization"].startswith("UTF-8")
    for relative in (
        "app/main.py",
        "tests/fixtures/stored_jd_final_blind_reference.json",
        "scripts/benchmark_stored_jd_corpus.py",
        "scripts/benchmark_alio_jd.py",
    ):
        if relative in freeze["sha256"]:
            assert re.fullmatch(r"[0-9a-f]{64}", freeze["sha256"][relative])
        assert _release_git_text_sha256(relative) == git_text_hashes[relative]
    parser = "app/services/kordoc_parser.py"
    assert freeze["sha256"][parser] == addendum["initial_kordoc_parser_sha256"]
    assert _release_git_text_sha256(parser) == git_text_hashes[parser]
    assert addendum["coordinate_provenance_change_affected_frozen_scoring_count"] == 0
    ncs_client = "app/services/ncs_mcp_client.py"
    assert freeze["sha256"][ncs_client] == addendum[
        "initial_ncs_mcp_client_sha256"
    ]
    assert _release_git_text_sha256(ncs_client) == git_text_hashes[ncs_client]
    detail_catalog = "app/data/ncs_detail_catalog.json"
    unit_catalog = "app/data/ncs_unit_catalog.json"
    assert freeze["sha256"][detail_catalog] == addendum[
        "initial_detail_catalog_sha256"
    ]
    assert freeze["sha256"][unit_catalog] == addendum[
        "initial_unit_catalog_sha256"
    ]
    assert _release_git_text_sha256(detail_catalog) == addendum[
        "final_detail_catalog_sha256"
    ]
    assert _release_git_text_sha256(unit_catalog) == addendum[
        "final_unit_catalog_sha256"
    ]
    scorer = "scripts/score_stored_jd_holdout.py"
    assert _release_git_text_sha256(scorer) == git_text_hashes[scorer]
    assert addendum["previous_final_ncs_mcp_client_sha256"] == (
        "d4225e751f420fb76304bfe819f1ad8d9268dbd4caa14a238f0b977114597652"
    )
    assert addendum["extraction_artifacts_changed"] is True
    assert addendum["thresholds_changed"] is False
    assert addendum["metric_computation_changed"] is False
    assert addendum["semantic_extraction_logic_changed"] is True
    assert addendum["post_change_blind_claim_allowed"] is False
    assert addendum["pre_change_blind_result_retained"] is True
    assert addendum["current_recheck_is_independent"] is False
    assert addendum["affected_frozen_record_count"] == 1
    assert addendum["active_catalog_filter"] == "usage_yn=Y"
    assert addendum["inactive_detail_count_removed"] == 15
    assert addendum["inactive_unit_count_removed"] == 153
    assert addendum["active_catalog_filter_affected_frozen_record_count"] == 0
    assert addendum["affected_frozen_record_sha256"] == [
        "3712a58f2e8bdc31d3276df674dd81f0a9735af1cda6f8a1a800f333c7e293ce"
    ]
    assert addendum["current_non_independent_recheck_changed_frozen_record_count"] == (
        addendum["affected_frozen_record_count"]
    )
    assert addendum[
        "current_non_independent_recheck_changed_frozen_record_sha256"
    ] == addendum["affected_frozen_record_sha256"]
    for field in (
        "current_non_independent_recheck_baseline_benchmark_csv_sha256",
        "current_non_independent_recheck_benchmark_csv_sha256",
        "current_non_independent_recheck_score_sha256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", addendum[field])
    result = "tests/fixtures/stored_jd_final_blind_result.json"
    assert _release_git_text_sha256(result) == git_text_hashes[result]
    assert set(addendum["final_raw_sha256"]) == set(git_text_hashes)
    for relative, expected in addendum["final_raw_sha256"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", expected)
        assert (ROOT / relative).is_file()
    assert addendum["final_raw_sha256_scope"].startswith(
        "Windows audit-run worktree bytes"
    )
    assert addendum["final_audit_script_sha256"] == addendum["final_raw_sha256"][
        "scripts/audit_stored_jd_ksa_contract.py"
    ]
    assert addendum["final_kordoc_parser_sha256"] == addendum["final_raw_sha256"][
        "app/services/kordoc_parser.py"
    ]
    assert addendum["final_ncs_mcp_client_sha256"] == addendum["final_raw_sha256"][
        "app/services/ncs_mcp_client.py"
    ]


def test_final_blind_evidence_timestamps_and_operational_drift_are_ordered() -> None:
    freeze = json.loads(
        (FIXTURES / "stored_jd_final_blind_freeze.json").read_text(encoding="utf-8")
    )
    addendum = json.loads(
        (FIXTURES / "stored_jd_final_blind_freeze_addendum.json").read_text(
            encoding="utf-8"
        )
    )
    result = json.loads(
        (FIXTURES / "stored_jd_final_blind_result.json").read_text(encoding="utf-8")
    )
    reference = json.loads(
        (FIXTURES / "stored_jd_final_blind_reference.json").read_text(
            encoding="utf-8"
        )
    )
    frozen_at = datetime.fromisoformat(freeze["frozen_at"])
    addendum_created = datetime.fromisoformat(addendum["created_at"])
    compared_at = datetime.fromisoformat(result["evaluated_at"])
    rechecked_at = datetime.fromisoformat(
        addendum["current_non_independent_recheck_at"]
    )
    updated_at = datetime.fromisoformat(addendum["updated_at"])

    assert frozen_at <= addendum_created <= compared_at <= rechecked_at <= updated_at
    assert addendum["current_benchmark_non_timing_change_from_previous_count"] == 1
    changed = addendum["current_benchmark_non_timing_changed_sha256"]
    assert len(changed) == 1
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in changed)
    assert addendum["current_benchmark_non_timing_change_overlaps_frozen_set"] is False
    assert addendum[
        "final_operational_non_timing_change_from_20260825_162035_count"
    ] == 0
    assert re.fullmatch(
        r"[0-9a-f]{64}", addendum["final_operational_benchmark_csv_sha256"]
    )
    assert re.fullmatch(
        r"[0-9a-f]{64}", addendum["final_operational_benchmark_summary_sha256"]
    )
    frozen_document_hashes = {
        record["sha256"] for record in reference["records"]
    }
    assert set(changed).isdisjoint(frozen_document_hashes)


def test_final_blind_result_is_valid_non_gold_and_meets_every_release_target() -> None:
    result = json.loads(
        (FIXTURES / "stored_jd_final_blind_result.json").read_text(encoding="utf-8")
    )
    metrics = result["accuracy_evidence"]["metrics"]

    assert result["comparison_count"] == 1
    assert result["metric_validity"] is True
    assert result["integrity_failures"] == []
    assert result["release_acceptance"] is True
    assert result["is_human_reviewed"] is False
    assert result["is_gold_accuracy"] is False
    assert result["accuracy_evidence"]["provenance"] == (
        "frozen source-only agent-reviewed holdout, not human gold"
    )
    assert result["operational_diagnostics"]["provenance"] == (
        "operational diagnostics, not independent accuracy"
    )
    assert "operational_corpus" not in result
    assert metrics["detail_code_precision_pct"] >= 90.0
    assert metrics["detail_code_recall_pct"] >= 90.0
    assert metrics["detail_document_exact_pct"] >= 80.0
    assert metrics["ability_scope_precision_pct"] >= 95.0
    assert metrics["ability_scope_recall_pct"] >= 95.0
    assert metrics["ability_code_precision_pct"] >= 80.0
    assert metrics["ability_code_recall_pct"] >= 80.0
    assert metrics["ksa_recall_pct"] == 100.0


def test_final_blind_result_matches_append_only_comparison_ledger() -> None:
    result = json.loads(
        (FIXTURES / "stored_jd_final_blind_result.json").read_text(encoding="utf-8")
    )
    ledger = json.loads(
        (FIXTURES / "stored_jd_final_blind_comparison_ledger.json").read_text(
            encoding="utf-8"
        )
    )
    freeze_path = FIXTURES / "stored_jd_final_blind_freeze.json"

    assert ledger["schema_version"] == 1
    assert len(ledger["entries"]) == 1
    entry = ledger["entries"][0]
    assert entry["freeze_sha256"] == _git_text_sha256(freeze_path)
    assert entry["comparison_sequence"] == result["comparison_count"] == 1
    assert entry["comparison_id"] == result["comparison_id"]
    assert entry["source_artifacts"] == result["source_artifacts"]
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in result["source_artifacts"].values()
    )
    payload = {
        "freeze_sha256": entry["freeze_sha256"],
        "reference_records_sha256": result["reference_records_sha256"],
        "selection_manifest_records_sha256": result[
            "selection_manifest_records_sha256"
        ],
        "source_artifacts": result["source_artifacts"],
    }
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(rendered).hexdigest() == result["comparison_id"]


def test_final_blind_comparison_ledger_rejects_drift(tmp_path: Path) -> None:
    import importlib.util

    script = ROOT / "scripts" / "build_stored_jd_final_blind_result.py"
    spec = importlib.util.spec_from_file_location("final_blind_result_builder", script)
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    ledger_path = tmp_path / "ledger.json"
    common = {
        "ledger_path": ledger_path,
        "freeze_sha256": "a" * 64,
        "comparison_id": "b" * 64,
        "source_artifacts": {"holdout_score_sha256": "c" * 64},
        "metrics_sha256": "d" * 64,
    }
    ledger, _entry = builder._update_ledger(**common)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    builder._update_ledger(**common)
    with pytest.raises(ValueError, match="different comparison"):
        builder._update_ledger(**{**common, "comparison_id": "e" * 64})


def test_final_blind_builder_rejects_post_freeze_semantic_change() -> None:
    import importlib.util

    script = ROOT / "scripts" / "build_stored_jd_final_blind_result.py"
    spec = importlib.util.spec_from_file_location("final_blind_result_builder", script)
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    freeze_path = FIXTURES / "stored_jd_final_blind_freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    addendum = json.loads(
        (FIXTURES / "stored_jd_final_blind_freeze_addendum.json").read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(ValueError, match="invalidates a new blind claim"):
        builder._verify_freeze(freeze, addendum, freeze_path=freeze_path)
