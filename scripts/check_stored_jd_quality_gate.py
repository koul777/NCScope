from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS = {
    "current_official_detail_recognition_pct": 90.0,
    "detail_mapping_state_coverage_pct": 100.0,
    "documents_all_current_official_details_exact_pct": 80.0,
    "ability_mapping_state_coverage_pct": 100.0,
    "ability_official_scope_candidate_pct": 95.0,
    "ability_official_code_candidate_pct": 80.0,
    "ksa_available_pct": 100.0,
}

HOLDOUT_POLICY_THRESHOLDS = {
    "detail_code_pct": 90.0,
    "detail_document_exact_pct": 80.0,
    "ability_scope_pct": 95.0,
    "ability_code_pct": 80.0,
    "ksa_pct": 100.0,
}


LEGACY_DIAGNOSTIC_METRICS = (
    "parse_success_pct",
    "detail_exact_pct",
    "official_validation_detail_exact_pct",
    "documents_all_details_exact_pct",
    "documents_all_official_details_exact_pct",
    "ability_scoped_pct",
    "ability_exact_of_scoped_pct",
    "ability_end_to_end_pct",
)


def evaluate_quality_gate(
    summary: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
    expected_files: int = 206,
    expected_unique_contents: int = 198,
    holdout_score: dict[str, Any] | None = None,
    expected_holdout_records: int = 33,
) -> dict[str, Any]:
    active = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[dict[str, Any]] = []
    metric_validity = summary.get("metric_validity")
    if not isinstance(metric_validity, dict):
        failures.append(
            {
                "metric": "metric_validity",
                "actual": None,
                "expected": True,
                "reason": "missing_metric_validity",
            }
        )
    elif metric_validity.get("valid") is not True:
        invalid_reasons = metric_validity.get("invalid_reasons")
        failures.append(
            {
                "metric": "metric_validity",
                "actual": metric_validity.get("valid"),
                "expected": True,
                "reason": "invalid_derived_metrics",
                "invalid_reasons": (
                    invalid_reasons if isinstance(invalid_reasons, list) else []
                ),
            }
        )
    for metric, expected in (
        ("files", expected_files),
        ("unique_contents", expected_unique_contents),
    ):
        raw_count = summary.get(metric)
        try:
            actual_count = int(raw_count)
        except (TypeError, ValueError):
            failures.append(
                {
                    "metric": metric,
                    "actual": raw_count,
                    "expected": expected,
                    "reason": "invalid_or_missing_corpus_count",
                }
            )
            continue
        if actual_count != expected:
            failures.append(
                {
                    "metric": metric,
                    "actual": actual_count,
                    "expected": expected,
                    "reason": "corpus_count_mismatch",
                }
            )

    observed: dict[str, float | None] = {}
    for metric, minimum in active.items():
        raw_actual = summary.get(metric)
        if metric not in summary or raw_actual is None or raw_actual == "":
            observed[metric] = None
            failures.append(
                {
                    "metric": metric,
                    "actual": None,
                    "minimum": float(minimum),
                    "reason": "missing_required_metric",
                }
            )
            continue
        if isinstance(raw_actual, bool):
            observed[metric] = None
            failures.append(
                {
                    "metric": metric,
                    "actual": raw_actual,
                    "minimum": float(minimum),
                    "reason": "invalid_percentage",
                }
            )
            continue
        try:
            actual = float(raw_actual)
        except (TypeError, ValueError):
            observed[metric] = None
            failures.append(
                {
                    "metric": metric,
                    "actual": raw_actual,
                    "minimum": float(minimum),
                    "reason": "invalid_percentage",
                }
            )
            continue
        if not math.isfinite(actual) or not 0.0 <= actual <= 100.0:
            observed[metric] = None
            failures.append(
                {
                    "metric": metric,
                    "actual": raw_actual,
                    "minimum": float(minimum),
                    "reason": "invalid_percentage",
                }
            )
            continue
        observed[metric] = actual
        if actual < float(minimum):
            failures.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "minimum": float(minimum),
                    "reason": "below_minimum",
                }
            )
    operational_passed = not failures
    release_failures: list[str] = []
    if holdout_score is None:
        release_failures.append("missing_agent_reviewed_holdout_score")
    else:
        required_holdout_values = {
            "evaluation_basis": "agent_source_evidence_review_holdout_not_human_gold",
            "is_human_reviewed": False,
            "is_gold_accuracy": False,
            "metric_validity": True,
            "passed": True,
            "release_acceptance": True,
            "release_acceptance_scope": "agent_reviewed_holdout_quality_gate",
        }
        for key, expected in required_holdout_values.items():
            if holdout_score.get(key) != expected:
                release_failures.append(f"invalid_holdout_{key}")
        try:
            holdout_record_count = int(holdout_score.get("record_count") or 0)
        except (TypeError, ValueError):
            holdout_record_count = -1
        if holdout_record_count != expected_holdout_records:
            release_failures.append("holdout_record_count_mismatch")
        reference_hash = str(holdout_score.get("reference_records_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", reference_hash):
            release_failures.append("invalid_holdout_reference_hash")
        selection_hash = str(
            holdout_score.get("selection_manifest_records_sha256") or ""
        )
        if not re.fullmatch(r"[0-9a-f]{64}", selection_hash):
            release_failures.append("invalid_holdout_selection_manifest_hash")
        if holdout_score.get("thresholds") != HOLDOUT_POLICY_THRESHOLDS:
            release_failures.append("holdout_threshold_policy_mismatch")
        checks = holdout_score.get("checks")
        if (
            not isinstance(checks, dict)
            or not checks
            or any(value is not True for value in checks.values())
        ):
            release_failures.append("holdout_checks_not_all_true")
        if holdout_score.get("integrity_failures") != []:
            release_failures.append("holdout_integrity_failures")
    release_accepted = operational_passed and not release_failures
    return {
        "passed": operational_passed,
        "acceptance_scope": "operational_quality_gate_not_gold_accuracy",
        "release_acceptance": release_accepted,
        "release_acceptance_reason": (
            "operational_and_agent_reviewed_holdout_gates_passed"
            if release_accepted
            else "requires_valid_agent_reviewed_holdout_scorer"
        ),
        "release_failures": release_failures,
        "holdout_score": holdout_score,
        "expected_files": expected_files,
        "expected_unique_contents": expected_unique_contents,
        "thresholds": active,
        "observed": observed,
        "legacy_diagnostics": {
            metric: summary.get(metric) for metric in LEGACY_DIAGNOSTIC_METRICS
        },
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail closed when the stored-JD NCS extraction target is missed."
    )
    parser.add_argument("summary_json")
    parser.add_argument("--output")
    parser.add_argument("--holdout-score-json")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--expected-files", type=int, default=206)
    parser.add_argument("--expected-unique-contents", type=int, default=198)
    parser.add_argument("--expected-holdout-records", type=int, default=33)
    parser.add_argument("--min-current-detail-recognition", type=float, default=90.0)
    parser.add_argument("--min-detail-mapping-state", type=float, default=100.0)
    parser.add_argument("--min-current-detail-doc", type=float, default=80.0)
    parser.add_argument("--min-ability-mapping-state", type=float, default=100.0)
    parser.add_argument("--min-ability-official-scope", type=float, default=95.0)
    parser.add_argument("--min-ability-official-code", type=float, default=80.0)
    parser.add_argument("--min-ksa", type=float, default=100.0)
    args = parser.parse_args()

    summary = json.loads(Path(args.summary_json).read_text(encoding="utf-8"))
    holdout_score = (
        json.loads(Path(args.holdout_score_json).read_text(encoding="utf-8"))
        if args.holdout_score_json
        else None
    )
    result = evaluate_quality_gate(
        summary,
        thresholds={
            "current_official_detail_recognition_pct": (
                args.min_current_detail_recognition
            ),
            "detail_mapping_state_coverage_pct": args.min_detail_mapping_state,
            "documents_all_current_official_details_exact_pct": (
                args.min_current_detail_doc
            ),
            "ability_mapping_state_coverage_pct": args.min_ability_mapping_state,
            "ability_official_scope_candidate_pct": args.min_ability_official_scope,
            "ability_official_code_candidate_pct": args.min_ability_official_code,
            "ksa_available_pct": args.min_ksa,
        },
        expected_files=args.expected_files,
        expected_unique_contents=args.expected_unique_contents,
        holdout_score=holdout_score,
        expected_holdout_records=args.expected_holdout_records,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return 0 if result["release_acceptance"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
