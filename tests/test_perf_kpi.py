from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import perf_kpi  # noqa: E402
from perf_kpi import KPI_RULES, KpiRule, compare_kpi, compare_snapshots, evaluate_diff_policy  # noqa: E402


def _rule(**overrides):
    values = {"key": "x.metric", "label": "metric", "direction": "higher", "tier": "guard"}
    values.update(overrides)
    return KpiRule(**values)


def test_kpi_rules_have_unique_keys_known_tiers_and_directions():
    keys = [rule.key for rule in KPI_RULES]
    assert len(keys) == len(set(keys))
    known_stages = set(perf_kpi.STAGES) | {"holdout"}
    for rule in KPI_RULES:
        assert rule.key.split(".", 1)[0] in known_stages
        assert rule.tier in {"guard", "target", "latency"}
        assert rule.direction in {"higher", "lower"}
        if rule.tier != "latency":
            assert rule.tolerance == 0.0


@pytest.mark.parametrize(
    ("direction", "baseline", "candidate", "verdict"),
    [
        ("higher", 80.0, 81.0, "improved"),
        ("higher", 80.0, 80.0, "same"),
        ("higher", 80.0, 79.99, "regressed"),
        ("lower", 3.0, 2.0, "improved"),
        ("lower", 3.0, 4.0, "regressed"),
    ],
)
def test_compare_kpi_verdicts_follow_direction(direction, baseline, candidate, verdict):
    assert compare_kpi(_rule(direction=direction), baseline, candidate)["verdict"] == verdict


def test_latency_tolerance_is_relative_to_baseline():
    rule = _rule(direction="lower", tier="latency", tolerance=0.25)
    assert compare_kpi(rule, 100.0, 124.0)["verdict"] == "within_tolerance"
    assert compare_kpi(rule, 100.0, 126.0)["verdict"] == "regressed"


def test_limits_fail_even_without_a_baseline_and_missing_kpis_fail_closed():
    floor = _rule(limit=90)
    assert compare_kpi(floor, None, 89.0)["verdict"] == "below_limit"
    assert compare_kpi(floor, 95.0, None)["verdict"] == "missing"
    assert compare_kpi(floor, None, None)["verdict"] == "not_measured"
    ceiling = _rule(direction="lower", limit=0)
    assert compare_kpi(ceiling, 0, 1)["verdict"] == "below_limit"


def test_allowance_waives_a_regression_but_never_a_release_limit():
    baseline = {"label": "base", "kpis": {"stored_jd.detail_exact_pct": 77.0, "tests.failed": 0}}
    candidate = {"label": "cand", "kpis": {"stored_jd.detail_exact_pct": 76.0, "tests.failed": 2}}
    result = compare_snapshots(
        baseline,
        candidate,
        allowances={"stored_jd.detail_exact_pct": "legacy label now reported", "tests.failed": "flaky"},
    )
    failed = {row["key"]: row["verdict"] for row in result["failures"]}
    assert failed["tests.failed"] == "below_limit"
    assert "stored_jd.detail_exact_pct" not in failed
    assert [row["key"] for row in result["allowed"]] == ["stored_jd.detail_exact_pct"]
    assert result["passed"] is False


def test_snapshot_that_drops_a_measured_stage_fails():
    baseline = {"kpis": {"holdout.detail_codes_precision_pct": 96.2}}
    result = compare_snapshots(baseline, {"kpis": {}})
    assert not result["passed"]
    assert result["failures"][0]["verdict"] == "missing"


def test_diff_policy_rejects_frozen_assets_removed_tests_and_new_skips():
    test_diff = "\n".join(
        [
            "--- a/tests/test_parser.py",
            "+++ b/tests/test_parser.py",
            "-def test_keeps_legacy_label():",
            "-    assert parse('x') == 'x'",
            "+@pytest.mark.skip(reason='slow')",
            "+def test_new_behavior():",
            "+    assert True",
        ]
    )
    result = evaluate_diff_policy(
        changed_paths=["app/services/parser.py", "tests/fixtures/stored_jd_final_blind_reference.json"],
        numstat=[(10, 2, "app/services/parser.py")],
        test_diff=test_diff,
    )
    codes = {violation["code"] for violation in result["violations"]}
    assert codes == {"protected_path_changed", "test_removed_or_renamed", "skip_marker_added"}
    assert result["review_required"][0]["code"] == "assertions_removed"
    assert result["tests_added"] == ["test_new_behavior"]


def test_diff_policy_limits_production_change_size_but_not_tests():
    numstat = [(700, 200, "app/main.py"), (5000, 0, "tests/test_big.py")]
    result = evaluate_diff_policy(changed_paths=["app/main.py"], numstat=numstat, test_diff="")
    assert result["production_changed_lines"] == 900
    assert [violation["code"] for violation in result["violations"]] == ["change_too_large"]


def test_quality_gate_changes_require_guardian_review_without_blocking():
    production_diff = "\n".join(
        [
            "@@ -10513,3 +10513,3 @@ def _natural_question_wording_ok(q: dict[str, Any], question: str) -> bool:",
            "-    return not bad",
            "+    return True",
            "@@ -20,1 +20,1 @@ def _render_template(method: str) -> str:",
        ]
    )
    result = evaluate_diff_policy(
        changed_paths=["app/main.py", "app/services/question_realism.py"],
        numstat=[(2, 2, "app/main.py")],
        test_diff="",
        production_diff=production_diff,
    )
    assert result["passed"]
    gate = [item for item in result["review_required"] if item["code"] == "quality_gate_touched"]
    assert gate[0]["touched"] == ["app/services/question_realism.py", "_natural_question_wording_ok"]


def test_template_only_change_does_not_ask_for_gate_review():
    result = evaluate_diff_policy(
        changed_paths=["app/main.py"],
        numstat=[(3, 1, "app/main.py")],
        test_diff="",
        production_diff="@@ -20,1 +20,1 @@ def _question_for_method(method: str) -> str:",
    )
    assert result["review_required"] == []


def test_protected_patterns_cover_measurement_tools_and_windows_paths():
    assert perf_kpi.matches_protected("scripts\\perf_kpi.py")
    assert perf_kpi.matches_protected("tests/fixtures/stored_jd_holdout_reference.json")
    assert perf_kpi.matches_protected(".cursor/rules/ncscope-team.mdc")
    assert not perf_kpi.matches_protected("app/services/kordoc_parser.py")


def test_parse_junit_counts_passed_tests_and_failed_ids(tmp_path):
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        '<testsuites><testsuite tests="5" failures="1" errors="1" skipped="1" time="2.5">'
        '<testcase classname="tests.test_a" name="test_ok"/>'
        '<testcase classname="tests.test_a" name="test_bad"><failure message="x"/></testcase>'
        '<testcase classname="tests.test_b" name="test_err"><error message="y"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert perf_kpi.parse_junit(junit) == {"passed": 2, "failed": 1, "errors": 1, "skipped": 1, "seconds": 2.5}
    assert perf_kpi.failed_test_ids(junit) == ["tests.test_a::test_bad", "tests.test_b::test_err"]


def test_holdout_summary_keeps_aggregates_only():
    score = {
        "metrics": {
            "detail_codes": {"precision_pct": 96.2, "recall_pct": 100.0, "document_exact_pct": 90.48, "true_positive": 76},
            "document_state": {"accuracy_pct": 97.22},
        },
        "failures": [{"sha256": "abc", "expected": "x"}],
    }
    summary = perf_kpi.summarize_holdout(score)
    assert summary == {
        "detail_codes_precision_pct": 96.2,
        "detail_codes_recall_pct": 100.0,
        "detail_codes_document_exact_pct": 90.48,
        "document_state_accuracy_pct": 97.22,
    }


def test_dev_rows_exclude_holdout_documents(tmp_path):
    source = tmp_path / "bench.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sha256", "status"])
        writer.writeheader()
        writer.writerows([{"sha256": "keep", "status": "mcp_exact"}, {"sha256": "hold", "status": "no_detail"}])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"records": [{"sha256": "hold"}]}), encoding="utf-8")
    output = tmp_path / "dev.csv"
    count = perf_kpi.write_dev_rows(source, perf_kpi.holdout_sha256s(manifest), output)
    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert count == 1
    assert [row["sha256"] for row in rows] == ["keep"]


def test_percentile_interpolates_and_handles_empty_input():
    assert perf_kpi.percentile([], 0.5) is None
    assert perf_kpi.percentile([100, 200, 300, 400], 0.5) == 250.0
    assert perf_kpi.percentile([5], 0.95) == 5.0
