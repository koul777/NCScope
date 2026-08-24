from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RELEASE_THRESHOLDS = {
    "detail_code_pct": 90.0,
    "detail_document_exact_pct": 80.0,
    "ability_scope_pct": 95.0,
    "ability_code_pct": 80.0,
    "ksa_pct": 100.0,
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(char for char in text if unicodedata.category(char) != "Co")
    text = re.sub(r"[·ᆞㆍ․‧•∙⋅・]", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _norm_detail_label(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(
        r"^\s*[\[(（［]\s*(?:자체\s*개발|기관\s*자체\s*개발)\s*[\])）］]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return _norm(text)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def _json_list(row: dict[str, Any], key: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(str(row.get(key) or ""))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{row.get('sha256')}: invalid {key} JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{row.get('sha256')}: {key} must be a list of objects")
    return value


def _json_mapping(row: dict[str, Any], key: str) -> dict[str, list[str]]:
    try:
        value = json.loads(str(row.get(key) or ""))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{row.get('sha256')}: invalid {key} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{row.get('sha256')}: {key} must be an object")
    output: dict[str, list[str]] = {}
    for detail, units in value.items():
        if not isinstance(units, list):
            raise ValueError(f"{row.get('sha256')}: invalid {key} entry")
        output[str(detail)] = [str(unit) for unit in units]
    return output


def _semicolon_set(value: Any, *, normalize: bool = False) -> set[str]:
    output = {
        str(item).strip()
        for item in str(value or "").split(";")
        if str(item).strip()
    }
    return {_norm(item) for item in output if _norm(item)} if normalize else output


def _document_state(row: dict[str, Any]) -> str:
    if str(row.get("status") or "") == "parse_error":
        return "parse_error"
    if str(row.get("declared_no_mapping") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return "declared_no_mapping"
    if _semicolon_set(row.get("details"), normalize=True):
        return "explicit_detail"
    return "no_explicit_ncs_detail"


def _observed_sets(row: dict[str, Any]) -> dict[str, set[Any]]:
    detail_states = _json_list(row, "detail_mapping_states")
    ability_states = _json_list(row, "ability_mapping_states")
    mapping = _json_mapping(row, "ability_source_scope_mapping")
    detail_codes = {
        str(code).strip()
        for state in detail_states
        if state.get("mappingState") == "official_current_exact"
        for code in (state.get("officialDetailCodes") or [])
        if str(code).strip()
    }
    ability_codes = {
        str(code).strip()
        for state in ability_states
        for code in (state.get("resolvedUnitCodes") or [])
        if str(code).strip()
    }
    ability_pairs = {
        (_norm(detail), _norm(unit))
        for detail, units in mapping.items()
        for unit in units
        if _norm(detail) and _norm(unit)
    }
    return {
        "detail_labels": {
            _norm_detail_label(item)
            for item in _semicolon_set(row.get("details"))
            if _norm_detail_label(item)
        },
        "detail_codes": detail_codes,
        "ability_pairs": ability_pairs,
        "ability_codes": ability_codes,
        "ksa_codes": _semicolon_set(row.get("ksa_available_codes")),
    }


def _expected_sets(record: dict[str, Any]) -> dict[str, set[Any]]:
    mapping = record.get("expected_ability_units_by_detail") or {}
    return {
        "detail_labels": {
            _norm_detail_label(label)
            for label in (record.get("expected_detail_labels") or [])
            if _norm_detail_label(label)
        },
        "detail_codes": {
            str(item.get("code") or "").strip()
            for item in (record.get("expected_detail_codes") or [])
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        },
        "ability_pairs": {
            (_norm(detail), _norm(unit))
            for detail, units in mapping.items()
            for unit in units
            if _norm(detail) and _norm(unit)
        },
        "ability_codes": {
            str(item.get("code") or "").strip()
            for item in (record.get("expected_ability_unit_codes") or [])
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        },
    }


def _metric(accumulator: dict[str, Any]) -> dict[str, Any]:
    tp = int(accumulator["tp"])
    fp = int(accumulator["fp"])
    fn = int(accumulator["fn"])
    precision = _pct(tp, tp + fp)
    recall = _pct(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 2)
        if precision + recall
        else 0.0
    )
    documents = int(accumulator["documents"])
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision_pct": precision,
        "recall_pct": recall,
        "f1_pct": f1,
        "document_count": documents,
        "document_exact_count": int(accumulator["document_exact"]),
        "document_exact_pct": _pct(accumulator["document_exact"], documents),
    }


def _validate_reference(
    reference: dict[str, Any],
    *,
    selected_sha256s: set[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    records = reference.get("records")
    if not isinstance(records, list) or not records:
        return ["reference records missing"]
    if reference.get("reviewer_type") != "agent_source_evidence_review":
        failures.append("unexpected reviewer_type")
    if reference.get("is_human_reviewed") is not False:
        failures.append("reference must not claim human review")
    if reference.get("is_gold") is not False:
        failures.append("reference must not claim gold status")
    if reference.get("blind_completed_before_observed_comparison") is not True:
        failures.append("blind completion flag missing")
    if int(reference.get("record_count") or 0) != len(records):
        failures.append("reference record_count mismatch")
    if reference.get("records_sha256") != _canonical_sha256(records):
        failures.append("reference records_sha256 mismatch")
    digests = [str(record.get("sha256") or "") for record in records if isinstance(record, dict)]
    if len(digests) != len(set(digests)):
        failures.append("duplicate reference sha256")
    invalid_digest = any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests)
    if invalid_digest:
        failures.append("reference contains an invalid sha256")
    elif selected_sha256s is not None:
        if set(digests) != selected_sha256s:
            failures.append("reference sha256 set does not match selection manifest")
    elif any(int(digest[:8], 16) % 5 != 0 for digest in digests):
        failures.append("reference contains a non-holdout sha256")
    return failures


def _validate_selection_manifest(
    payload: dict[str, Any],
) -> tuple[set[str], list[str], str]:
    failures: list[str] = []
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        return set(), ["selection manifest records missing"], ""
    if payload.get("selection_method") != "seeded_sha256_by_suffix_without_parser_output":
        failures.append("unexpected selection manifest method")
    if int(payload.get("record_count") or 0) != len(records):
        failures.append("selection manifest record_count mismatch")
    declared_hash = str(payload.get("records_sha256") or "").strip().lower()
    if declared_hash != _canonical_sha256(records):
        failures.append("selection manifest records_sha256 mismatch")
    digests = [
        str(record.get("sha256") or "").strip().lower()
        for record in records
        if isinstance(record, dict)
    ]
    if len(digests) != len(records) or len(set(digests)) != len(digests):
        failures.append("selection manifest sha256 values are missing or duplicated")
    if any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests):
        failures.append("selection manifest contains an invalid sha256")
    return set(digests), failures, declared_hash


def score_rows(
    reference: dict[str, Any],
    benchmark_rows: list[dict[str, Any]],
    *,
    thresholds: dict[str, float],
    selected_sha256s: set[str] | None = None,
    selection_integrity_failures: list[str] | None = None,
    selection_records_sha256: str = "",
) -> dict[str, Any]:
    integrity_failures = [
        *_validate_reference(reference, selected_sha256s=selected_sha256s),
        *(selection_integrity_failures or []),
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in benchmark_rows:
        grouped.setdefault(str(row.get("sha256") or "").strip().lower(), []).append(row)
    records = reference.get("records") if isinstance(reference.get("records"), list) else []
    accumulators = {
        name: {"tp": 0, "fp": 0, "fn": 0, "documents": 0, "document_exact": 0}
        for name in ("detail_labels", "detail_codes", "ability_pairs", "ability_codes", "ksa_codes")
    }
    state_correct = 0
    failures: list[dict[str, Any]] = []
    for record in records:
        digest = str(record.get("sha256") or "").strip().lower()
        candidates = grouped.get(digest) or []
        if not candidates:
            integrity_failures.append(f"benchmark missing holdout {digest}")
            continue
        row = candidates[0]
        if any(
            str(candidate.get(key) or "") != str(row.get(key) or "")
            for candidate in candidates[1:]
            for key in (
                "status",
                "details",
                "detail_mapping_states",
                "ability_mapping_states",
                "ability_source_scope_mapping",
                "ksa_available_codes",
            )
        ):
            integrity_failures.append(f"duplicate benchmark disagreement {digest}")
            continue
        try:
            observed = _observed_sets(row)
        except ValueError as exc:
            integrity_failures.append(str(exc))
            continue
        expected = _expected_sets(record)
        expected_state = str(record.get("document_state") or "")
        observed_state = _document_state(row)
        if observed_state == expected_state:
            state_correct += 1
        record_failure: dict[str, Any] = {"sha256": digest}
        if observed_state != expected_state:
            record_failure["document_state"] = {
                "expected": expected_state,
                "observed": observed_state,
            }
        for name in ("detail_labels", "detail_codes", "ability_pairs", "ability_codes"):
            expected_set = expected[name]
            observed_set = observed[name]
            accumulator = accumulators[name]
            accumulator["tp"] += len(expected_set & observed_set)
            accumulator["fp"] += len(observed_set - expected_set)
            accumulator["fn"] += len(expected_set - observed_set)
            if expected_set:
                accumulator["documents"] += 1
                accumulator["document_exact"] += int(expected_set == observed_set)
            if expected_set != observed_set:
                record_failure[name] = {
                    "missing": sorted(expected_set - observed_set),
                    "unexpected": sorted(observed_set - expected_set),
                }
        expected_ksa = expected["ability_codes"]
        observed_ksa = observed["ksa_codes"] & expected_ksa
        ksa_accumulator = accumulators["ksa_codes"]
        ksa_accumulator["tp"] += len(expected_ksa & observed_ksa)
        ksa_accumulator["fn"] += len(expected_ksa - observed_ksa)
        if expected_ksa:
            ksa_accumulator["documents"] += 1
            ksa_accumulator["document_exact"] += int(expected_ksa == observed_ksa)
        if expected_ksa != observed_ksa:
            record_failure["ksa_codes"] = {
                "missing": sorted(expected_ksa - observed_ksa),
                "unexpected": [],
            }
        if len(record_failure) > 1:
            failures.append(record_failure)

    metrics = {name: _metric(value) for name, value in accumulators.items()}
    metrics["document_state"] = {
        "correct": state_correct,
        "total": len(records),
        "accuracy_pct": _pct(state_correct, len(records)),
    }
    denominator_checks = {
        "detail_codes": metrics["detail_codes"]["true_positive"]
        + metrics["detail_codes"]["false_negative"],
        "detail_labels_documents": metrics["detail_labels"]["document_count"],
        "ability_pairs": metrics["ability_pairs"]["true_positive"]
        + metrics["ability_pairs"]["false_negative"],
        "ability_codes": metrics["ability_codes"]["true_positive"]
        + metrics["ability_codes"]["false_negative"],
        "ksa_codes": metrics["ksa_codes"]["true_positive"]
        + metrics["ksa_codes"]["false_negative"],
    }
    for name, value in denominator_checks.items():
        if not value:
            integrity_failures.append(f"empty evaluation denominator: {name}")

    checks = {
        "detail_code_precision": metrics["detail_codes"]["precision_pct"]
        >= thresholds["detail_code_pct"],
        "detail_code_recall": metrics["detail_codes"]["recall_pct"]
        >= thresholds["detail_code_pct"],
        "detail_document_exact": metrics["detail_labels"]["document_exact_pct"]
        >= thresholds["detail_document_exact_pct"],
        "ability_scope_precision": metrics["ability_pairs"]["precision_pct"]
        >= thresholds["ability_scope_pct"],
        "ability_scope_recall": metrics["ability_pairs"]["recall_pct"]
        >= thresholds["ability_scope_pct"],
        "ability_code_precision": metrics["ability_codes"]["precision_pct"]
        >= thresholds["ability_code_pct"],
        "ability_code_recall": metrics["ability_codes"]["recall_pct"]
        >= thresholds["ability_code_pct"],
        "ksa_recall": metrics["ksa_codes"]["recall_pct"]
        >= thresholds["ksa_pct"],
    }
    metric_validity = not integrity_failures
    passed = metric_validity and all(checks.values())
    return {
        "schema_version": 1,
        "evaluation_basis": "agent_source_evidence_review_holdout_not_human_gold",
        "is_human_reviewed": False,
        "is_gold_accuracy": False,
        "reference_id": reference.get("reference_id"),
        "reference_records_sha256": reference.get("records_sha256"),
        "selection_manifest_records_sha256": selection_records_sha256,
        "record_count": len(records),
        "metric_validity": metric_validity,
        "integrity_failures": integrity_failures,
        "thresholds": thresholds,
        "metrics": metrics,
        "checks": checks,
        "passed": passed,
        "release_acceptance": passed,
        "release_acceptance_scope": "agent_reviewed_holdout_quality_gate",
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score a stored-JD benchmark against the blind agent-reviewed holdout."
    )
    parser.add_argument("reference_json")
    parser.add_argument("benchmark_csv")
    parser.add_argument(
        "--selection-manifest-json",
        help=(
            "Frozen source-only selection manifest. When supplied, its exact SHA256 "
            "set replaces the legacy modulo split integrity check."
        ),
    )
    parser.add_argument(
        "--require-selection-manifest",
        action="store_true",
        help="Fail closed unless --selection-manifest-json is supplied.",
    )
    parser.add_argument("--report-dir", default="tmp/stored_jd_holdout_score")
    parser.add_argument("--max-benchmark-age-hours", type=float, default=24.0)
    parser.add_argument(
        "--detail-code-pct",
        type=float,
        default=DEFAULT_RELEASE_THRESHOLDS["detail_code_pct"],
    )
    parser.add_argument(
        "--detail-document-exact-pct",
        type=float,
        default=DEFAULT_RELEASE_THRESHOLDS["detail_document_exact_pct"],
    )
    parser.add_argument(
        "--ability-scope-pct",
        type=float,
        default=DEFAULT_RELEASE_THRESHOLDS["ability_scope_pct"],
    )
    parser.add_argument(
        "--ability-code-pct",
        type=float,
        default=DEFAULT_RELEASE_THRESHOLDS["ability_code_pct"],
    )
    parser.add_argument(
        "--ksa-pct",
        type=float,
        default=DEFAULT_RELEASE_THRESHOLDS["ksa_pct"],
    )
    args = parser.parse_args()
    if args.require_selection_manifest and not args.selection_manifest_json:
        parser.error(
            "--require-selection-manifest requires --selection-manifest-json"
        )

    reference_path = Path(args.reference_json)
    benchmark_path = Path(args.benchmark_csv)
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    with benchmark_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    thresholds = {
        "detail_code_pct": args.detail_code_pct,
        "detail_document_exact_pct": args.detail_document_exact_pct,
        "ability_scope_pct": args.ability_scope_pct,
        "ability_code_pct": args.ability_code_pct,
        "ksa_pct": args.ksa_pct,
    }
    selected_sha256s: set[str] | None = None
    selection_integrity_failures: list[str] = []
    selection_records_sha256 = ""
    if args.selection_manifest_json:
        selection_payload = json.loads(
            Path(args.selection_manifest_json).read_text(encoding="utf-8")
        )
        (
            selected_sha256s,
            selection_integrity_failures,
            selection_records_sha256,
        ) = _validate_selection_manifest(selection_payload)
    result = score_rows(
        reference,
        rows,
        thresholds=thresholds,
        selected_sha256s=selected_sha256s,
        selection_integrity_failures=selection_integrity_failures,
        selection_records_sha256=selection_records_sha256,
    )
    age_hours = (
        datetime.now(timezone.utc).timestamp() - benchmark_path.stat().st_mtime
    ) / 3600.0
    result["benchmark_csv"] = str(benchmark_path)
    result["benchmark_age_hours"] = round(age_hours, 3)
    if age_hours < 0 or age_hours > args.max_benchmark_age_hours:
        result["metric_validity"] = False
        result["passed"] = False
        result["release_acceptance"] = False
        result["integrity_failures"].append(
            f"benchmark age {age_hours:.3f}h outside allowed range"
        )

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"stored_jd_holdout_score_{stamp}.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: value for key, value in result.items() if key != "failures"}, ensure_ascii=False, indent=2))
    print(f"report={json_path}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
