from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _require_release_evidence(
    holdout: dict[str, Any],
    gate: dict[str, Any],
    benchmark: dict[str, Any],
) -> None:
    if holdout.get("release_acceptance") is not True:
        raise ValueError("holdout score did not pass release acceptance")
    if holdout.get("metric_validity") is not True:
        raise ValueError("holdout score metrics are invalid")
    if holdout.get("integrity_failures") != []:
        raise ValueError("holdout score has integrity failures")
    if gate.get("release_acceptance") is not True:
        raise ValueError("quality gate did not pass release acceptance")
    if gate.get("release_failures") != []:
        raise ValueError("quality gate has release failures")
    if gate.get("holdout_score") != holdout:
        raise ValueError("quality gate does not embed the supplied holdout score")
    metric_validity = benchmark.get("metric_validity")
    if not isinstance(metric_validity, dict) or metric_validity.get("valid") is not True:
        raise ValueError("benchmark metrics are invalid")


def _verify_freeze(
    freeze: dict[str, Any],
    addendum: dict[str, Any],
    *,
    freeze_path: Path,
) -> None:
    if addendum.get("original_freeze_sha256") != _sha256(freeze_path):
        raise ValueError("freeze hash does not match its addendum")
    if freeze.get("comparison_performed") is not False:
        raise ValueError("freeze must predate the first observed comparison")
    if (
        addendum.get("semantic_extraction_logic_changed") is not False
        or addendum.get("extraction_artifacts_changed") is not False
    ):
        raise ValueError(
            "post-freeze semantic extraction change invalidates a new blind claim"
        )
    if addendum.get("thresholds_changed") is not False:
        raise ValueError("addendum changed frozen thresholds")
    if addendum.get("metric_computation_changed") is not False:
        raise ValueError("addendum changed frozen metric computation")

    hashes = freeze.get("sha256")
    if not isinstance(hashes, dict):
        raise ValueError("freeze sha256 map is missing")
    for relative, expected in hashes.items():
        if relative == "scripts/score_stored_jd_holdout.py":
            if expected != addendum.get("initial_scorer_sha256"):
                raise ValueError("initial scorer hash does not match the addendum")
            continue
        if relative == "app/services/ncs_mcp_client.py":
            if expected != addendum.get("initial_ncs_mcp_client_sha256"):
                raise ValueError("initial NCS MCP client hash does not match the addendum")
            current_path = ROOT / relative
            if _sha256(current_path) != addendum.get("final_ncs_mcp_client_sha256"):
                raise ValueError("current NCS MCP client hash does not match the addendum")
            continue
        path = ROOT / str(relative)
        if not path.is_file():
            raise ValueError(f"frozen artifact is missing: {relative}")
        if _sha256(path) != expected:
            raise ValueError(f"frozen artifact hash mismatch: {relative}")

    scorer_path = ROOT / "scripts" / "score_stored_jd_holdout.py"
    if _sha256(scorer_path) != addendum.get("final_scorer_sha256"):
        raise ValueError("current scorer hash does not match the addendum")


def _accuracy_metrics(holdout: dict[str, Any]) -> dict[str, float]:
    metrics = holdout["metrics"]
    return {
        "detail_label_precision_pct": metrics["detail_labels"]["precision_pct"],
        "detail_label_recall_pct": metrics["detail_labels"]["recall_pct"],
        "detail_document_exact_pct": metrics["detail_labels"]["document_exact_pct"],
        "detail_code_precision_pct": metrics["detail_codes"]["precision_pct"],
        "detail_code_recall_pct": metrics["detail_codes"]["recall_pct"],
        "ability_scope_precision_pct": metrics["ability_pairs"]["precision_pct"],
        "ability_scope_recall_pct": metrics["ability_pairs"]["recall_pct"],
        "ability_code_precision_pct": metrics["ability_codes"]["precision_pct"],
        "ability_code_recall_pct": metrics["ability_codes"]["recall_pct"],
        "ksa_recall_pct": metrics["ksa_codes"]["recall_pct"],
        "document_state_accuracy_pct": metrics["document_state"]["accuracy_pct"],
    }


def _operational_diagnostics(benchmark: dict[str, Any]) -> dict[str, Any]:
    return {
        "provenance": "operational diagnostics, not independent accuracy",
        "files": benchmark["files"],
        "unique_contents": benchmark["unique_contents"],
        "parse_success_pct": benchmark["parse_success_pct"],
        "operational_candidate_coverage_current_official_detail_pct": benchmark[
            "current_official_detail_recognition_pct"
        ],
        "operational_candidate_coverage_current_official_detail_document_exact_pct": benchmark[
            "documents_all_current_official_details_exact_pct"
        ],
        "operational_candidate_coverage_ability_scope_pct": benchmark[
            "ability_official_scope_candidate_pct"
        ],
        "operational_candidate_coverage_ability_code_pct": benchmark[
            "ability_official_code_candidate_pct"
        ],
        "operational_candidate_coverage_ksa_pct": benchmark["ksa_available_pct"],
        "mapping_state_coverage_pct": min(
            benchmark["detail_mapping_state_coverage_pct"],
            benchmark["ability_mapping_state_coverage_pct"],
        ),
    }


def _update_ledger(
    ledger_path: Path,
    *,
    freeze_sha256: str,
    comparison_id: str,
    source_artifacts: dict[str, str],
    metrics_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if ledger_path.exists():
        ledger = _load(ledger_path)
    else:
        ledger = {"schema_version": 1, "entries": []}
    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise ValueError("comparison ledger entries must be a list")

    same_freeze = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("freeze_sha256") == freeze_sha256
    ]
    if len(same_freeze) > 1:
        raise ValueError("comparison ledger contains duplicate freeze entries")
    if same_freeze:
        entry = same_freeze[0]
        if entry.get("comparison_id") != comparison_id:
            raise ValueError("a different comparison already exists for this freeze")
        if entry.get("source_artifacts") != source_artifacts:
            raise ValueError("comparison source artifacts drifted")
        if entry.get("metrics_sha256") != metrics_sha256:
            raise ValueError("comparison metrics drifted")
        return ledger, entry

    entry = {
        "freeze_sha256": freeze_sha256,
        "comparison_id": comparison_id,
        "comparison_sequence": 1,
        "first_compared_at": datetime.now(timezone.utc).isoformat(),
        "source_artifacts": source_artifacts,
        "metrics_sha256": metrics_sha256,
    }
    entries.append(entry)
    return ledger, entry


def build_result(
    *,
    holdout_path: Path,
    gate_path: Path,
    benchmark_summary_path: Path,
    benchmark_csv_path: Path,
    freeze_path: Path,
    addendum_path: Path,
    ledger_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    holdout = _load(holdout_path)
    gate = _load(gate_path)
    benchmark = _load(benchmark_summary_path)
    freeze = _load(freeze_path)
    addendum = _load(addendum_path)
    _require_release_evidence(holdout, gate, benchmark)
    _verify_freeze(freeze, addendum, freeze_path=freeze_path)

    source_artifacts = {
        "holdout_score_sha256": _sha256(holdout_path),
        "quality_gate_sha256": _sha256(gate_path),
        "benchmark_csv_sha256": _sha256(benchmark_csv_path),
        "benchmark_summary_sha256": _sha256(benchmark_summary_path),
    }
    for value in source_artifacts.values():
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("invalid source artifact hash")

    accuracy = _accuracy_metrics(holdout)
    freeze_sha256 = _sha256(freeze_path)
    comparison_id = _canonical_sha256(
        {
            "freeze_sha256": freeze_sha256,
            "reference_records_sha256": holdout["reference_records_sha256"],
            "selection_manifest_records_sha256": holdout[
                "selection_manifest_records_sha256"
            ],
            "source_artifacts": source_artifacts,
        }
    )
    ledger, entry = _update_ledger(
        ledger_path,
        freeze_sha256=freeze_sha256,
        comparison_id=comparison_id,
        source_artifacts=source_artifacts,
        metrics_sha256=_canonical_sha256(accuracy),
    )
    result = {
        "schema_version": 2,
        "evaluated_at": entry["first_compared_at"],
        "evaluation_basis": holdout["evaluation_basis"],
        "is_human_reviewed": holdout["is_human_reviewed"],
        "is_gold_accuracy": holdout["is_gold_accuracy"],
        "comparison_count": entry["comparison_sequence"],
        "comparison_id": comparison_id,
        "reference_id": holdout["reference_id"],
        "reference_records_sha256": holdout["reference_records_sha256"],
        "selection_manifest_records_sha256": holdout[
            "selection_manifest_records_sha256"
        ],
        "record_count": holdout["record_count"],
        "metric_validity": holdout["metric_validity"],
        "integrity_failures": holdout["integrity_failures"],
        "release_acceptance": gate["release_acceptance"],
        "accuracy_evidence": {
            "provenance": "frozen source-only agent-reviewed holdout, not human gold",
            "metrics": accuracy,
        },
        "operational_diagnostics": _operational_diagnostics(benchmark),
        "source_artifacts": source_artifacts,
    }
    return result, ledger


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a hash-verified final blind evaluation result and ledger."
    )
    parser.add_argument("--holdout-score", required=True)
    parser.add_argument("--quality-gate", required=True)
    parser.add_argument("--benchmark-summary", required=True)
    parser.add_argument("--benchmark-csv", required=True)
    parser.add_argument("--freeze", required=True)
    parser.add_argument("--addendum", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    ledger_path = Path(args.ledger)
    result, ledger = build_result(
        holdout_path=Path(args.holdout_score),
        gate_path=Path(args.quality_gate),
        benchmark_summary_path=Path(args.benchmark_summary),
        benchmark_csv_path=Path(args.benchmark_csv),
        freeze_path=Path(args.freeze),
        addendum_path=Path(args.addendum),
        ledger_path=ledger_path,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_result = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    rendered_ledger = json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
    output_path.write_text(rendered_result, encoding="utf-8")
    ledger_path.write_text(rendered_ledger, encoding="utf-8")
    print(rendered_result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
