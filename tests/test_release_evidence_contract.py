from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "reports" / "ncscope_1_4_12_release_evidence.json"
ADDENDUM_PATH = (
    ROOT / "tests" / "fixtures" / "stored_jd_final_blind_freeze_addendum.json"
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _git_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_release_evidence_is_sanitized_and_bound_to_tracked_contracts() -> None:
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    addendum = json.loads(ADDENDUM_PATH.read_text(encoding="utf-8"))

    assert set(evidence) == {
        "schema_version",
        "release_version",
        "generated_at",
        "evidence_scope",
        "human_gold_accuracy_available",
        "stored_jd_parser",
        "frozen_agent_reviewed_reference",
        "gold_reference_workflow",
        "scoring_runtime_attestation",
        "configured_ncs_mcp_contract",
        "release_run_source_raw_sha256",
        "source_contract_git_text_sha256",
        "verification",
    }
    serialized = json.dumps(evidence, ensure_ascii=False)
    assert "C:\\\\" not in serialized
    assert "tmp/" not in serialized
    assert "tmp\\\\" not in serialized

    assert evidence["schema_version"] == "ncscope_release_evidence_v2"
    assert evidence["release_version"] == "1.4.12"
    assert evidence["human_gold_accuracy_available"] is False
    assert datetime.fromisoformat(evidence["generated_at"]) <= datetime.fromisoformat(
        addendum["updated_at"]
    )
    assert "private source documents" in evidence["evidence_scope"]

    parser = evidence["stored_jd_parser"]
    assert (parser["files"], parser["parse_success"], parser["parse_errors"]) == (
        206,
        206,
        0,
    )
    assert parser["benchmark_csv_sha256"] == addendum[
        "final_operational_benchmark_csv_sha256"
    ]
    assert parser["benchmark_summary_sha256"] == addendum[
        "final_operational_benchmark_summary_sha256"
    ]
    assert parser["ksa_probe_unit_codes"] == parser["ksa_available_unit_codes"] == 555
    drift = parser["operational_change_from_previous_run"]
    assert drift["non_timing_changed_record_count"] == addendum[
        "final_operational_non_timing_change_from_20260825_162035_count"
    ]
    assert drift["changed_document_sha256"] == []
    assert drift["frozen_set_overlap"] is False

    frozen = evidence["frozen_agent_reviewed_reference"]
    assert frozen["is_human_gold_accuracy"] is False
    assert frozen["record_count"] == 36
    assert frozen["release_acceptance"] is True
    assert frozen["score_sha256"] == addendum[
        "current_non_independent_recheck_score_sha256"
    ]
    assert frozen["metrics"] == {
        "detail_name_f1_pct": 98.97,
        "detail_code_f1_pct": 98.06,
        "detail_code_document_exact_pct": 90.48,
        "document_state_accuracy_pct": 97.22,
        "ksa_code_f1_pct": 100.0,
    }

    selected = evidence["configured_ncs_mcp_contract"]["selected_probe"]
    active = evidence["configured_ncs_mcp_contract"]["all_active_catalog"]
    for row, prefix in ((selected, "current_selected"), (active, "current_all_active")):
        assert row["passed"] is True
        assert row["input_digests_match"] is True
        assert row["complete_ksa_type_set_pct"] == 100.0
        assert row["runtime_seconds"] <= row["max_runtime_seconds"]
        assert row["report_sha256"] == addendum[
            f"{prefix}_ksa_contract_report_sha256"
        ]
        assert row["unit_codes"] == addendum[f"{prefix}_ksa_contract_unit_count"]
        assert row["verified_rows"] == addendum[f"{prefix}_ksa_contract_row_count"]

    source_paths = {
        "app_main": ROOT / "app" / "main.py",
        "benchmark_alio": ROOT / "scripts" / "benchmark_alio_jd.py",
        "kordoc_parser": ROOT / "app" / "services" / "kordoc_parser.py",
        "ncs_mcp_client": ROOT / "app" / "services" / "ncs_mcp_client.py",
        "ksa_audit_script": ROOT / "scripts" / "audit_stored_jd_ksa_contract.py",
    }
    source_hashes = evidence["source_contract_git_text_sha256"]
    raw_source_hashes = evidence["release_run_source_raw_sha256"]
    assert raw_source_hashes["scope"].startswith("Windows release-run")
    assert set(raw_source_hashes["digests"]) == set(source_paths)
    assert source_hashes["canonicalization"].startswith("UTF-8")
    for name, path in source_paths.items():
        digest = source_hashes[name]
        assert HEX64.fullmatch(digest)
        assert _git_text_sha256(path) == digest
        raw_digest = raw_source_hashes["digests"][name]
        assert HEX64.fullmatch(raw_digest)

    gold = evidence["gold_reference_workflow"]
    assert gold["workflow_version"] == "v2"
    assert gold["split_policy"] == "posting_document_connected_component"
    assert gold["current_source_packets_human_gold_eligible"] is False
    assert gold["historical_published_metrics_split"].startswith("v1_")
    gold_paths = {
        "split_contract": ROOT / "scripts" / "ncs_recruitment_split.py",
        "prepare_goldset": ROOT / "scripts" / "prepare_ncs_recruitment_goldset.py",
        "prepare_source_packets": (
            ROOT / "scripts" / "prepare_ncs_recruitment_source_packets.py"
        ),
        "finalize_goldset": ROOT / "scripts" / "finalize_ncs_recruitment_goldset.py",
        "score_goldset": ROOT / "scripts" / "score_ncs_recruitment_goldset.py",
        "official_detail_catalog": ROOT / "app" / "data" / "ncs_detail_catalog.json",
    }
    assert set(gold["source_contract_git_text_sha256"]) == set(gold_paths)
    for name, path in gold_paths.items():
        assert gold["source_contract_git_text_sha256"][name] == _git_text_sha256(path)

    attestation = evidence["scoring_runtime_attestation"]
    assert attestation == {
        "schema_version": "ncscope_evaluation_runtime_attestation_v2",
        "fail_closed": True,
        "verified_on_every_response": True,
        "startup_and_completion_source_stability_checked": True,
        "parser_execution_identity_verified": True,
        "server_source_keys": [
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
        ],
    }

    verification = evidence["verification"]
    assert verification["ruff"] == "passed"
    assert verification["pytest"]["status"] == "passed"
    assert verification["pytest"]["passed"] >= 5000
    assert verification["pytest"]["skipped"] >= 0
    assert verification["pytest"]["duration_seconds"] > 0
    assert verification["npm_audit_high_or_critical"] == 0
