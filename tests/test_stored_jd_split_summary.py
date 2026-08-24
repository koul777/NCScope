from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "summarize_stored_jd_splits.py"
SPEC = importlib.util.spec_from_file_location("summarize_stored_jd_splits", SCRIPT_PATH)
assert SPEC and SPEC.loader
split_summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(split_summary)
BENCHMARK_PATH = SCRIPTS_DIR / "benchmark_stored_jd_corpus.py"
BENCHMARK_SPEC = importlib.util.spec_from_file_location(
    "benchmark_stored_jd_corpus_for_split_test",
    BENCHMARK_PATH,
)
assert BENCHMARK_SPEC and BENCHMARK_SPEC.loader
benchmark = importlib.util.module_from_spec(BENCHMARK_SPEC)
BENCHMARK_SPEC.loader.exec_module(benchmark)


def _row(digest: str, *, exact: int) -> dict[str, str]:
    catalog_ambiguous = 1 - exact
    official_ability = 2 if exact else 1
    official_scope = 2 if exact else 0
    official_codes = 2 if exact else 0
    return {
        "sha256": digest,
        "status": "mcp_exact",
        "detail_count": "1",
        "official_validation_detail_count": "1",
        "self_developed_detail_count": "0",
        "detail_exact_count": str(exact),
        "detail_catalog_exact_count": str(exact),
        "detail_catalog_unmapped_count": "0",
        "detail_catalog_ambiguous_count": str(catalog_ambiguous),
        "ability_unit_count": "2",
        "ability_scoped_count": "2",
        "ability_exact_count": "1",
        "ability_catalog_exact_count": str(official_ability),
        "ability_catalog_unmapped_count": str(2 - official_ability),
        "ability_official_source_scoped_count": str(official_scope),
        "ability_official_scope_candidate_count": str(official_scope),
        "ability_official_converged_scope_count": "0",
        "ability_official_code_candidate_count": str(official_codes),
        "ksa_probe_codes": f"unit-{digest}",
        "ksa_available_codes": f"unit-{digest}",
    }


def test_split_summary_deduplicates_by_hash_and_keeps_holdout_separate() -> None:
    benchmark = [_row("a", exact=1), _row("a", exact=1), _row("b", exact=0)]
    manifest = [
        {"sha256": "a", "split": "development"},
        {"sha256": "b", "split": "holdout"},
    ]

    result = split_summary.build_split_summary(benchmark, manifest)

    assert result["leakage_check"] is True
    assert result["overall_unique"]["unique_contents"] == 2
    assert result["development"]["official_validation_detail_exact_pct"] == 100.0
    assert result["holdout"]["official_validation_detail_exact_pct"] == 0.0
    assert result["overall_unique"]["current_official_detail_recognition_pct"] == 50.0
    assert result["development"]["current_official_detail_recognition_pct"] == 100.0
    assert result["holdout"]["current_official_detail_recognition_pct"] == 0.0
    assert result["overall_unique"]["detail_mapping_state_coverage_pct"] == 100.0
    assert (
        result["development"]["documents_all_current_official_details_exact_pct"]
        == 100.0
    )
    assert (
        result["holdout"]["documents_all_current_official_details_exact_pct"]
        == 0.0
    )
    assert result["overall_unique"]["ability_mapping_state_coverage_pct"] == 100.0
    assert result["overall_unique"]["ability_official_scope_candidate_pct"] == 66.67
    assert result["development"]["ability_official_scope_candidate_pct"] == 100.0
    assert result["holdout"]["ability_official_scope_candidate_pct"] == 0.0
    assert result["overall_unique"]["ability_official_code_candidate_pct"] == 66.67
    assert result["development"]["ability_official_code_candidate_pct"] == 100.0
    assert result["holdout"]["ability_official_code_candidate_pct"] == 0.0
    assert result["overall_unique"]["ksa_available_pct"] == 100.0
    assert result["metric_validity"]["valid"] is True
    assert result["evaluation_basis"].endswith("not gold accuracy")
    assert result["release_acceptance"] is False
    assert result["release_acceptance_reason"] == (
        "requires agent-reviewed holdout scorer"
    )


def test_ability_candidate_sets_deduplicate_and_keep_scope_and_code_distinct() -> None:
    states = [
        {
            "sourceName": "source unit",
            "mappingState": "official_exact_source_scoped",
            "catalogExact": True,
        },
        {
            "sourceName": "derived unit",
            "mappingState": "official_exact_derived_scope_review_required",
        },
        {
            "sourceName": "conflict unit",
            "mappingState": "official_exact_scope_conflict",
            "catalogExact": True,
        },
        {
            "sourceName": "ambiguous unit",
            "mappingState": "official_exact_detail_ambiguous",
            "catalogExact": True,
        },
    ]
    convergence = [
        {
            "evidence": [
                {"sourceAbilityUnitName": "source unit"},
                {"sourceAbilityUnitName": "derived unit"},
                {"sourceAbilityUnitName": " derived unit "},
                {"sourceAbilityUnitName": "outside row"},
            ]
        }
    ]

    keys = benchmark._ability_candidate_key_sets(states, convergence)

    assert keys["scope_candidates"] == {
        "sourceunit",
        "derivedunit",
        "conflictunit",
    }
    assert keys["safe_convergence"] == {"derivedunit"}
    assert keys["code_candidates"] == {"sourceunit", "derivedunit"}


def test_split_scope_and_code_candidate_formulas_match_benchmark_definition() -> None:
    row = _row("formula", exact=1)
    row.update(
        {
            "ability_unit_count": "4",
            "ability_catalog_exact_count": "4",
            "ability_catalog_unmapped_count": "0",
            "ability_official_source_scoped_count": "1",
            "ability_official_derived_scope_count": "1",
            "ability_official_scope_conflict_count": "1",
            "ability_official_ambiguous_count": "1",
            "ability_official_converged_scope_count": "1",
            "ability_official_scope_candidate_count": "",
            "ability_official_code_candidate_count": "",
            "ability_mapping_states": json.dumps(
                [
                    {
                        "sourceName": "source unit",
                        "mappingState": "official_exact_source_scoped",
                    },
                    {
                        "sourceName": "derived unit",
                        "mappingState": (
                            "official_exact_derived_scope_review_required"
                        ),
                    },
                    {
                        "sourceName": "conflict unit",
                        "mappingState": "official_exact_scope_conflict",
                    },
                    {
                        "sourceName": "ambiguous unit",
                        "mappingState": "official_exact_detail_ambiguous",
                    },
                ]
            ),
            "detail_convergence_suggestions": json.dumps(
                [
                    {
                        "evidence": [
                            {"sourceAbilityUnitName": "derived unit"}
                        ]
                    }
                ]
            ),
        }
    )

    summary = split_summary.summarize([row])

    assert summary["ability_mapping_state_coverage_pct"] == 100.0
    assert summary["ability_official_scope_candidate_count"] == 3
    assert summary["ability_official_scope_candidate_pct"] == 75.0
    assert summary["ability_official_code_candidate_count"] == 2
    assert summary["ability_official_code_candidate_pct"] == 50.0
    assert summary["metric_validity"]["valid"] is True


def test_legacy_scope_fallback_uses_normalized_source_name_union() -> None:
    row = _row("legacy-overlap", exact=1)
    row.update(
        {
            "ability_unit_count": "1",
            "ability_catalog_exact_count": "1",
            "ability_catalog_unmapped_count": "0",
            "ability_official_source_scoped_count": "1",
            "ability_official_scope_conflict_count": "1",
            "ability_official_scope_candidate_count": "",
            "ability_official_code_candidate_count": "1",
            "ability_mapping_states": json.dumps(
                [
                    {
                        "sourceName": "Same Unit",
                        "mappingState": "official_exact_source_scoped",
                    },
                    {
                        "sourceName": " same-unit ",
                        "mappingState": "official_exact_scope_conflict",
                    },
                ]
            ),
        }
    )

    split = split_summary.summarize([row])
    benchmark_result = benchmark._summary([row], unique_hashes=1)

    assert split["ability_official_scope_candidate_count"] == 1
    assert split["ability_official_scope_candidate_pct"] == 100.0
    assert split["metric_validity"]["valid"] is True
    assert benchmark_result["ability_official_scope_candidate_count"] == 1
    assert benchmark_result["metric_validity"]["valid"] is True


def test_legacy_row_without_valid_mapping_state_evidence_is_invalid() -> None:
    for evidence, expected_reason in (
        (None, "missing ability_mapping_states"),
        ("{not-json", "invalid ability_mapping_states JSON"),
    ):
        row = _row("legacy-invalid-states", exact=1)
        row["ability_official_scope_candidate_count"] = ""
        if evidence is None:
            row.pop("ability_mapping_states", None)
        else:
            row["ability_mapping_states"] = evidence

        summary = split_summary.summarize([row])

        assert summary["ability_official_scope_candidate_count"] == 0
        assert summary["metric_validity"]["valid"] is False
        assert any(
            expected_reason in reason
            for reason in summary["metric_validity"]["invalid_reasons"]
        )


def test_split_cli_exits_one_when_metric_evidence_is_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    row = _row("legacy-missing-states", exact=1)
    row["ability_official_scope_candidate_count"] = ""
    benchmark_csv = tmp_path / "benchmark.csv"
    manifest_csv = tmp_path / "manifest.csv"
    with benchmark_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    with manifest_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sha256", "split"])
        writer.writeheader()
        writer.writerow(
            {"sha256": "legacy-missing-states", "split": "development"}
        )
    monkeypatch.setattr(
        sys,
        "argv",
        ["summarize_stored_jd_splits.py", str(benchmark_csv), str(manifest_csv)],
    )

    assert split_summary.main() == 1


def test_benchmark_summary_uses_deduplicated_row_candidate_counts() -> None:
    row = {
        "status": "ability_ambiguous",
        "detail_count": 0,
        "ability_unit_count": 4,
        "ability_catalog_exact_count": 4,
        "ability_catalog_unmapped_count": 0,
        "ability_official_source_scoped_count": 1,
        "ability_official_derived_scope_count": 1,
        "ability_official_converged_scope_count": 1,
        "ability_official_scope_conflict_count": 1,
        "ability_official_ambiguous_count": 1,
        "ability_official_scope_candidate_count": 3,
        "ability_official_code_candidate_count": 2,
        "suffix": ".pdf",
    }

    summary = benchmark._summary([row], unique_hashes=1)

    assert summary["ability_mapping_state_coverage_pct"] == 100.0
    assert summary["ability_official_scope_candidate_count"] == 3
    assert summary["ability_official_scope_candidate_pct"] == 75.0
    assert summary["ability_official_code_candidate_count"] == 2
    assert summary["ability_official_code_candidate_pct"] == 50.0
