"""Performance KPI snapshot and guardrail checks for agent-team improvement rounds.

``snapshot`` measures one checkout and writes ``snapshot.json``. ``compare``
applies ``KPI_RULES`` to a baseline and a candidate snapshot. ``diff-policy``
rejects edits to frozen evaluation assets, removed tests, and new skip markers.

Holdout scoring keeps only aggregate metrics. Per-document holdout failures stay
in the scorer's own report directory so worker briefs never include them.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
DEFAULT_MCP_URL = "http://127.0.0.1:8778/mcp"
CORPUS_RELATIVE = Path("tmp/alio_jd_200_mcp")
HOLDOUT_MANIFEST_RELATIVE = Path("tmp/stored_jd_final_blind/final_blind_manifest.json")
HOLDOUT_REFERENCE = Path("tests/fixtures/stored_jd_final_blind_reference.json")
EXPECTED_HOLDOUT_RECORDS = 36
STAGES = ("tests", "lint", "audit", "stored_jd", "method_matrix")


@dataclass(frozen=True)
class KpiRule:
    key: str
    label: str
    direction: str  # "higher" or "lower"
    tier: str  # "guard", "target", or "latency"
    tolerance: float = 0.0  # absolute for guard/target, relative fraction for latency
    limit: float | None = None  # hard floor (higher) or ceiling (lower)


# Guard KPIs may not get worse at all. Target KPIs are the improvement goals and
# may not get worse either. Latency KPIs allow relative noise because worker
# rounds share one machine.
KPI_RULES: tuple[KpiRule, ...] = (
    KpiRule("tests.failed", "테스트 실패", "lower", "guard", limit=0),
    KpiRule("tests.errors", "테스트 오류", "lower", "guard", limit=0),
    KpiRule("tests.passed", "테스트 통과 수", "higher", "guard"),
    KpiRule("lint.f821_violations", "ruff F821 위반", "lower", "guard", limit=0),
    KpiRule("audit.detail_connections_passed", "세분류-능력단위 연결 감사", "higher", "guard", limit=1),
    KpiRule("stored_jd.parse_success_pct", "저장 JD 파싱 성공률", "higher", "guard", limit=100),
    KpiRule(
        "stored_jd.current_official_detail_recognition_pct",
        "현행 공식 세분류 인식률",
        "higher",
        "guard",
        limit=90,
    ),
    KpiRule(
        "stored_jd.detail_mapping_state_coverage_pct",
        "세분류 매핑 상태 커버리지",
        "higher",
        "guard",
        limit=100,
    ),
    KpiRule(
        "stored_jd.documents_all_current_official_details_exact_pct",
        "현행 공식 세분류 문서 완전일치율",
        "higher",
        "guard",
        limit=80,
    ),
    KpiRule("stored_jd.ability_mapping_state_coverage_pct", "능력단위 매핑 상태 커버리지", "higher", "guard"),
    KpiRule("stored_jd.ksa_available_pct", "KSA 조회 가능률", "higher", "guard"),
    KpiRule("holdout.gate_passed", "저장 JD 품질 게이트", "higher", "guard", limit=1),
    KpiRule("holdout.detail_codes_precision_pct", "홀드아웃 세분류 코드 정밀도", "higher", "guard"),
    KpiRule("holdout.detail_codes_recall_pct", "홀드아웃 세분류 코드 재현율", "higher", "guard"),
    KpiRule("holdout.detail_codes_document_exact_pct", "홀드아웃 세분류 코드 문서 완전일치", "higher", "guard"),
    KpiRule("holdout.detail_labels_precision_pct", "홀드아웃 세분류명 정밀도", "higher", "guard"),
    KpiRule("holdout.detail_labels_recall_pct", "홀드아웃 세분류명 재현율", "higher", "guard"),
    KpiRule("holdout.ability_pairs_precision_pct", "홀드아웃 능력단위 범위 정밀도", "higher", "guard"),
    KpiRule("holdout.ability_pairs_recall_pct", "홀드아웃 능력단위 범위 재현율", "higher", "guard"),
    KpiRule("holdout.ability_codes_precision_pct", "홀드아웃 능력단위 코드 정밀도", "higher", "guard"),
    KpiRule("holdout.ability_codes_recall_pct", "홀드아웃 능력단위 코드 재현율", "higher", "guard"),
    KpiRule("holdout.ksa_codes_recall_pct", "홀드아웃 KSA 재현율", "higher", "guard"),
    KpiRule("holdout.document_state_accuracy_pct", "홀드아웃 문서 상태 정확도", "higher", "guard"),
    KpiRule("method_matrix.average_quality_score", "면접유형 매트릭스 평균 점수", "higher", "guard"),
    KpiRule("method_matrix.idempotence_failure_count", "재검수 멱등성 실패", "lower", "guard", limit=0),
    KpiRule("method_matrix.exact_surface_duplicate_count", "문항 표면 중복", "lower", "guard", limit=0),
    KpiRule("stored_jd.detail_exact_pct", "세분류 후보 공식 일치율", "higher", "target"),
    KpiRule("stored_jd.official_validation_detail_exact_pct", "공식 검증 대상 세분류 일치율", "higher", "target"),
    KpiRule(
        "stored_jd.documents_all_official_details_exact_pct",
        "공식 검증 대상 문서 완전일치율",
        "higher",
        "target",
    ),
    KpiRule("stored_jd.ability_exact_of_scoped_pct", "범위 내 능력단위 정확 일치율", "higher", "target"),
    KpiRule("stored_jd.ability_end_to_end_pct", "능력단위 종단 일치율", "higher", "target"),
    KpiRule(
        "stored_jd.strict_pipeline_ready_of_detail_docs_pct",
        "세분류부터 KSA까지 한 번에 끝난 문서 비율",
        "higher",
        "target",
    ),
    KpiRule("stored_jd.ability_official_scope_candidate_pct", "공식 능력단위 범위 후보율", "higher", "target"),
    KpiRule("stored_jd.ability_official_code_candidate_pct", "공식 능력단위 코드 후보율", "higher", "target"),
    # The 2026-10-23 baseline is 467/504 (experience-interview wording), so this
    # is an improvement target that may not drop, not a 1.0 release floor yet.
    KpiRule("method_matrix.ready_rate", "면접유형 매트릭스 통과율", "higher", "target"),
    KpiRule("stored_jd.parse_ms_p50", "문서 파싱 p50(ms)", "lower", "latency", tolerance=0.25),
    KpiRule("stored_jd.parse_ms_p95", "문서 파싱 p95(ms)", "lower", "latency", tolerance=0.25),
    KpiRule("stored_jd.wall_seconds", "저장 JD 벤치마크 전체 시간(s)", "lower", "latency", tolerance=0.25),
    KpiRule("method_matrix.ms_per_question", "템플릿 문항 생성·검수(ms/문항)", "lower", "latency", tolerance=0.25),
    KpiRule("tests.seconds", "전체 테스트 시간(s)", "lower", "latency", tolerance=0.25),
)

STORED_JD_INFO_KEYS = (
    "files",
    "unique_contents",
    "documents_with_detail",
    "documents_without_extracted_detail",
    "detail_candidates",
    "detail_catalog_unmapped_candidates",
    "ability_units",
    "ability_units_unmatched",
    "ability_units_ambiguous",
    "ability_official_scope_conflicts",
    "declared_no_mapping_documents",
)

# Evaluation assets, measurement tools, and team rules stay frozen during an
# improvement round. Changing them would move the goalposts the round is
# judged against.
PROTECTED_PATTERNS: tuple[str, ...] = (
    "tests/fixtures/stored_jd_*",
    "tests/fixtures/*holdout*",
    "tests/fixtures/*blind*",
    "tests/test_perf_kpi.py",
    "reports/*",
    "scripts/perf_kpi.py",
    "scripts/benchmark_stored_jd_corpus.py",
    "scripts/benchmark_question_method_matrix.py",
    "scripts/score_stored_jd_holdout.py",
    "scripts/check_stored_jd_quality_gate.py",
    "scripts/audit_ncs_detail_connections.py",
    "app/data/ncs_detail_catalog.json",
    "app/data/ncs_unit_catalog.json",
    "AGENTS.md",
    ".cursor/*",
    ".claude/*",
    ".github/*",
    "docs/PERFORMANCE_KPI_GUARDRAILS.md",
    ".env*",
    "vercel.json",
    "Dockerfile",
    "pytest.ini",
    "requirements*.txt",
    "package.json",
    "package-lock.json",
)
DEFAULT_MAX_CHANGED_LINES = 800

# Quality-gate code may change only with guardian review: loosening a check is
# the cheapest way to raise a pass rate without improving any question.
GATE_MODULES: tuple[str, ...] = (
    "app/services/question_realism.py",
    "app/services/question_quality_orchestrator.py",
    "app/services/question_precision_grounding.py",
    "app/services/question_evaluation_alignment.py",
    "app/services/ai_question_quality_review.py",
    "app/services/ncs_match_gate.py",
)
_GATE_FUNCTION = re.compile(
    r"def\s+(_\w+_ok|_contains_blind_hiring_cue|_quality_check_statuses|_question_quality_report\w*"
    r"|_attach_question_quality_report|_run_runtime_question_quality_orchestration|evaluate_\w+)\s*\("
)
# Tags and paths that only evaluation tooling uses. Production code that keys
# on them would score the benchmark instead of improving the product (seen on
# 2026-10-23: a gate special-cased question_source == "matrix_template").
BENCHMARK_ONLY_MARKERS: tuple[str, ...] = (
    "matrix_template",
    "alio_jd_200_mcp",
    "stored_jd_dev_corpus",
    "stored_jd_final_blind",
    "final_blind_manifest",
    "guardian_only",
)

_TEST_DEF = re.compile(r"^[-+]\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
_SKIP_MARKER = re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail)\s*\(")
_ASSERT_LINE = re.compile(r"^[-+]\s*assert\b")


def _now() -> datetime:
    return datetime.now().astimezone()


def _no_window_flags() -> int:
    # The user's Windows hosts must not flash console windows for child tools.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _main_checkout() -> Path:
    """Return the primary checkout that holds untracked local corpora."""

    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            creationflags=_no_window_flags(),
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ROOT
    return Path(common).parent if common else ROOT


def _local_data_path(relative: Path) -> Path:
    for base in (ROOT, _main_checkout()):
        candidate = base / relative
        if candidate.exists():
            return candidate
    return ROOT / relative


def _default_ncs_db() -> Path:
    configured = str(os.getenv("NCS_DB_PATH") or "").strip()
    if configured:
        return Path(configured)
    return _main_checkout().parent / "NCS_MCP" / "data" / "processed" / "ncs.db"


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Linear-interpolated percentile; ``None`` for an empty sample."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * min(1.0, max(0.0, fraction))
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _run(
    command: Sequence[str],
    *,
    log_path: Path,
    timeout: int,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, float]:
    env = dict(os.environ)
    env.update(env_overrides or {})
    env.setdefault("PYTHONIOENCODING", "utf-8")
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_no_window_flags(),
        )
        returncode = completed.returncode
        output = f"$ {' '.join(command)}\n{completed.stdout}\n{completed.stderr}"
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        output = f"$ {' '.join(command)}\nTimed out after {timeout}s\n{exc.stdout or ''}\n{exc.stderr or ''}"
    elapsed = round(time.perf_counter() - started, 3)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8")
    return returncode, elapsed


def parse_junit(path: Path) -> dict[str, float]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(suite.get(key) or 0)
        totals["time"] += float(suite.get("time") or 0.0)
    return {
        "passed": totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"],
        "failed": totals["failures"],
        "errors": totals["errors"],
        "skipped": totals["skipped"],
        "seconds": round(totals["time"], 3),
    }


def failed_test_ids(path: Path, limit: int = 50) -> list[str]:
    root = ET.parse(path).getroot()
    failed: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(f"{case.get('classname', '')}::{case.get('name', '')}")
            if len(failed) >= limit:
                break
    return failed


def _stage_tests(run_dir: Path, timeout: int) -> dict[str, Any]:
    junit = run_dir / "pytest.xml"
    returncode, elapsed = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={run_dir / 'pytest-tmp'}",
            f"--junitxml={junit}",
        ],
        log_path=run_dir / "tests.log",
        timeout=timeout,
        # Same offline contract as CI: tests must never reach a live MCP or OpenAI.
        env_overrides={"NCS_MCP_URL": "http://mcp.example/mcp", "OPENAI_NET_CHECK_ENABLED": "false"},
    )
    if not junit.is_file():
        return {"status": "error", "returncode": returncode, "wall_seconds": elapsed}
    metrics = parse_junit(junit)
    return {
        "status": "pass" if returncode == 0 else "fail",
        "returncode": returncode,
        "wall_seconds": elapsed,
        "kpis": metrics,
        "failed_tests": failed_test_ids(junit),
    }


def _stage_lint(run_dir: Path, timeout: int) -> dict[str, Any]:
    output = run_dir / "ruff.json"
    returncode, elapsed = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F821",
            "--output-format",
            "json",
            "--output-file",
            str(output),
            "app",
            "scripts",
            "tests",
        ],
        log_path=run_dir / "lint.log",
        timeout=timeout,
    )
    if not output.is_file():
        return {"status": "unavailable", "returncode": returncode, "wall_seconds": elapsed}
    violations = json.loads(output.read_text(encoding="utf-8") or "[]")
    return {
        "status": "pass" if not violations else "fail",
        "returncode": returncode,
        "wall_seconds": elapsed,
        "kpis": {"f821_violations": len(violations)},
    }


def _stage_audit(run_dir: Path, timeout: int) -> dict[str, Any]:
    returncode, elapsed = _run(
        [sys.executable, "scripts/audit_ncs_detail_connections.py", "--output", str(run_dir / "detail_audit.json")],
        log_path=run_dir / "audit.log",
        timeout=timeout,
    )
    return {
        "status": "pass" if returncode == 0 else "fail",
        "returncode": returncode,
        "wall_seconds": elapsed,
        "kpis": {"detail_connections_passed": 1 if returncode == 0 else 0},
    }


def _newest(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime)
    return matches[-1] if matches else None


def holdout_sha256s(manifest_path: Path) -> set[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        str(record.get("sha256") or "").strip()
        for record in manifest.get("records") or []
        if str(record.get("sha256") or "").strip()
    }


def summarize_holdout(score: dict[str, Any]) -> dict[str, float]:
    metrics = score.get("metrics") or {}
    kpis: dict[str, float] = {}
    for group in ("detail_codes", "detail_labels", "ability_pairs", "ability_codes", "ksa_codes"):
        values = metrics.get(group) or {}
        for field in ("precision_pct", "recall_pct", "document_exact_pct"):
            if field in values:
                kpis[f"{group}_{field}"] = float(values[field])
    state = metrics.get("document_state") or {}
    if "accuracy_pct" in state:
        kpis["document_state_accuracy_pct"] = float(state["accuracy_pct"])
    return kpis


def write_dev_rows(benchmark_csv: Path, excluded_sha256s: set[str], output: Path) -> int:
    """Copy per-document benchmark rows minus the holdout for worker analysis."""

    with benchmark_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = [row for row in reader if str(row.get("sha256") or "").strip() not in excluded_sha256s]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _stage_stored_jd(
    run_dir: Path,
    timeout: int,
    *,
    corpus_dir: Path,
    holdout_manifest: Path,
    mcp_url: str,
    skip_holdout: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not corpus_dir.is_dir():
        return {"status": "unavailable", "reason": f"corpus not found: {corpus_dir}"}, None
    report_dir = run_dir / "stored_jd"
    returncode, elapsed = _run(
        [
            sys.executable,
            "scripts/benchmark_stored_jd_corpus.py",
            "--input-dir",
            str(corpus_dir),
            "--report-dir",
            str(report_dir),
        ],
        log_path=run_dir / "stored_jd.log",
        timeout=timeout,
        env_overrides={"NCS_MCP_URL": mcp_url},
    )
    summary_path = _newest(report_dir, "stored_jd_benchmark_*.json")
    csv_path = _newest(report_dir, "stored_jd_benchmark_*.csv")
    if returncode != 0 or summary_path is None or csv_path is None:
        return {"status": "error", "returncode": returncode, "wall_seconds": elapsed}, None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    parse_ms = [int(row["parse_ms"]) for row in rows if str(row.get("parse_ms") or "").isdigit()]
    kpis: dict[str, float | None] = {}
    for rule in KPI_RULES:
        stage_name, _, field = rule.key.partition(".")
        if stage_name == "stored_jd" and isinstance(summary.get(field), (int, float)):
            kpis[field] = float(summary[field])
    kpis["parse_ms_p50"] = percentile(parse_ms, 0.50)
    kpis["parse_ms_p95"] = percentile(parse_ms, 0.95)
    kpis["wall_seconds"] = elapsed
    stage: dict[str, Any] = {
        "status": "pass" if summary.get("parse_errors", 1) == 0 else "fail",
        "returncode": returncode,
        "wall_seconds": elapsed,
        "kpis": kpis,
        "info": {key: summary.get(key) for key in STORED_JD_INFO_KEYS},
        "status_counts": summary.get("status_counts") or {},
        "metric_validity": summary.get("metric_validity") or {},
        "benchmark_csv": str(csv_path),
        "benchmark_json": str(summary_path),
    }
    if not holdout_manifest.is_file():
        stage["dev_rows_note"] = "holdout manifest unavailable; dev rows not written"
        return stage, {"status": "unavailable", "reason": f"manifest not found: {holdout_manifest}"}
    excluded = holdout_sha256s(holdout_manifest)
    stage["dev_rows_csv"] = str(run_dir / "stored_jd_dev_rows.csv")
    stage["dev_row_count"] = write_dev_rows(csv_path, excluded, run_dir / "stored_jd_dev_rows.csv")
    if skip_holdout:
        return stage, {"status": "skipped"}
    return stage, _stage_holdout(run_dir, timeout, summary_path, csv_path, holdout_manifest)


def _stage_holdout(
    run_dir: Path,
    timeout: int,
    summary_path: Path,
    csv_path: Path,
    holdout_manifest: Path,
) -> dict[str, Any]:
    # The scorer's report holds per-document holdout failures. It stays in a
    # guardian-only directory and only aggregates enter snapshot.json.
    score_dir = run_dir / "guardian_only" / "holdout"
    returncode, elapsed = _run(
        [
            sys.executable,
            "scripts/score_stored_jd_holdout.py",
            str(HOLDOUT_REFERENCE),
            str(csv_path),
            "--selection-manifest-json",
            str(holdout_manifest),
            "--require-selection-manifest",
            "--report-dir",
            str(score_dir),
        ],
        log_path=run_dir / "guardian_only" / "holdout.log",
        timeout=timeout,
    )
    score_path = _newest(score_dir, "*.json")
    if score_path is None:
        return {"status": "error", "returncode": returncode, "wall_seconds": elapsed}
    score = json.loads(score_path.read_text(encoding="utf-8"))
    gate_path = run_dir / "guardian_only" / "quality_gate.json"
    gate_returncode, gate_elapsed = _run(
        [
            sys.executable,
            "scripts/check_stored_jd_quality_gate.py",
            str(summary_path),
            "--holdout-score-json",
            str(score_path),
            "--expected-holdout-records",
            str(EXPECTED_HOLDOUT_RECORDS),
            "--output",
            str(gate_path),
        ],
        log_path=run_dir / "guardian_only" / "quality_gate.log",
        timeout=timeout,
    )
    kpis = summarize_holdout(score)
    kpis["gate_passed"] = 1 if gate_returncode == 0 else 0
    return {
        "status": "pass" if gate_returncode == 0 and score.get("passed") else "fail",
        "returncode": returncode,
        "wall_seconds": round(elapsed + gate_elapsed, 3),
        "kpis": kpis,
        "record_count": score.get("record_count"),
        "metric_validity": score.get("metric_validity"),
        "integrity_failure_count": len(score.get("integrity_failures") or []),
        "failure_count": len(score.get("failures") or []),
    }


def _stage_method_matrix(run_dir: Path, timeout: int, *, ncs_db: Path) -> dict[str, Any]:
    if not ncs_db.is_file():
        return {"status": "unavailable", "reason": f"NCS DB not found: {ncs_db}"}
    output = run_dir / "method_matrix.json"
    returncode, elapsed = _run(
        [
            sys.executable,
            "scripts/benchmark_question_method_matrix.py",
            "--db",
            str(ncs_db),
            "--output",
            str(output),
        ],
        log_path=run_dir / "method_matrix.log",
        timeout=timeout,
    )
    if not output.is_file():
        return {"status": "error", "returncode": returncode, "wall_seconds": elapsed}
    report = json.loads(output.read_text(encoding="utf-8"))
    summary = report.get("summary") or {}
    question_count = int(summary.get("question_count") or 0)
    return {
        "status": "pass" if summary.get("passed") else "fail",
        "returncode": returncode,
        "wall_seconds": elapsed,
        "kpis": {
            "ready_rate": float(summary.get("ready_rate") or 0.0),
            "average_quality_score": float(summary.get("average_quality_score") or 0.0),
            "idempotence_failure_count": int(summary.get("idempotence_failure_count") or 0),
            "exact_surface_duplicate_count": int(summary.get("exact_surface_duplicate_count") or 0),
            "ms_per_question": round(elapsed * 1000 / question_count, 3) if question_count else None,
        },
        "question_count": question_count,
        "issue_counts": report.get("issue_counts") or {},
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        creationflags=_no_window_flags(),
    ).stdout


def flatten_kpis(stages: dict[str, Any]) -> dict[str, float | None]:
    flat: dict[str, float | None] = {}
    for stage_name, stage in stages.items():
        for key, value in (stage.get("kpis") or {}).items():
            flat[f"{stage_name}.{key}"] = value
    return flat


def take_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    label = re.sub(r"[^0-9A-Za-z_.-]+", "-", args.label).strip("-") or "snapshot"
    run_dir = Path(args.out_dir) / f"{label}-{_now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    selected = [stage for stage in STAGES if stage not in set(args.skip or [])]
    stages: dict[str, Any] = {}
    started = time.perf_counter()
    for name in selected:
        print(f"[perf_kpi] {name} starting", flush=True)
        if name == "tests":
            stages["tests"] = _stage_tests(run_dir, args.test_timeout)
        elif name == "lint":
            stages["lint"] = _stage_lint(run_dir, 300)
        elif name == "audit":
            stages["audit"] = _stage_audit(run_dir, 300)
        elif name == "stored_jd":
            stored, holdout = _stage_stored_jd(
                run_dir,
                args.benchmark_timeout,
                corpus_dir=Path(args.corpus_dir) if args.corpus_dir else _local_data_path(CORPUS_RELATIVE),
                holdout_manifest=(
                    Path(args.holdout_manifest)
                    if args.holdout_manifest
                    else _local_data_path(HOLDOUT_MANIFEST_RELATIVE)
                ),
                mcp_url=args.mcp_url,
                skip_holdout=args.skip_holdout,
            )
            stages["stored_jd"] = stored
            if holdout is not None:
                stages["holdout"] = holdout
        elif name == "method_matrix":
            stages["method_matrix"] = _stage_method_matrix(
                run_dir,
                args.benchmark_timeout,
                ncs_db=Path(args.ncs_db) if args.ncs_db else _default_ncs_db(),
            )
        print(f"[perf_kpi] {name} {stages.get(name, {}).get('status', 'done')}", flush=True)
    try:
        head = _git("rev-parse", "HEAD").strip()
        dirty = bool(_git("status", "--porcelain", "--untracked-files=no").strip())
    except (OSError, subprocess.CalledProcessError):
        head, dirty = "", True
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "label": label,
        "created_at": _now().isoformat(timespec="seconds"),
        "checkout": str(ROOT),
        "git_head": head,
        "git_dirty": dirty,
        "python": sys.version.split()[0],
        "stages": stages,
        "kpis": flatten_kpis(stages),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "run_dir": str(run_dir),
    }
    (run_dir / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return snapshot


def compare_kpi(rule: KpiRule, baseline: float | None, candidate: float | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "key": rule.key,
        "label": rule.label,
        "tier": rule.tier,
        "direction": rule.direction,
        "baseline": baseline,
        "candidate": candidate,
    }
    if candidate is None:
        # A KPI the baseline measured must still be measured; silently losing a
        # stage would hide a regression.
        result["verdict"] = "missing" if baseline is not None else "not_measured"
        return result
    sign = 1.0 if rule.direction == "higher" else -1.0
    if rule.limit is not None and sign * (candidate - rule.limit) < 0:
        result["verdict"] = "below_limit"
        result["limit"] = rule.limit
        return result
    if baseline is None:
        result["verdict"] = "new"
        return result
    delta = round(candidate - baseline, 6)
    result["delta"] = delta
    if rule.tier == "latency":
        allowed = abs(baseline) * rule.tolerance
    else:
        allowed = rule.tolerance
    improvement = sign * delta
    if improvement > 0:
        result["verdict"] = "improved"
    elif improvement == 0:
        result["verdict"] = "same"
    elif -improvement <= allowed:
        result["verdict"] = "within_tolerance"
    else:
        result["verdict"] = "regressed"
    return result


FAILING_VERDICTS = {"missing", "below_limit", "regressed"}


def compare_snapshots(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    allowances: dict[str, str] | None = None,
) -> dict[str, Any]:
    allowances = dict(allowances or {})
    base_kpis = baseline.get("kpis") or {}
    cand_kpis = candidate.get("kpis") or {}
    rows = [compare_kpi(rule, base_kpis.get(rule.key), cand_kpis.get(rule.key)) for rule in KPI_RULES]
    failures: list[dict[str, Any]] = []
    allowed: list[dict[str, Any]] = []
    for row in rows:
        if row["verdict"] not in FAILING_VERDICTS:
            continue
        # Absolute limits protect release thresholds and are never waivable.
        if row["key"] in allowances and row["verdict"] != "below_limit":
            row["allowance"] = allowances[row["key"]]
            allowed.append(row)
        else:
            failures.append(row)
    improved = [row for row in rows if row["verdict"] == "improved"]
    return {
        "schema_version": SCHEMA_VERSION,
        "baseline": {"label": baseline.get("label"), "git_head": baseline.get("git_head")},
        "candidate": {"label": candidate.get("label"), "git_head": candidate.get("git_head")},
        "passed": not failures,
        "failures": failures,
        "allowed": allowed,
        "improved": improved,
        "target_improved": [row["key"] for row in improved if row["tier"] in {"target", "latency"}],
        "rows": rows,
    }


def render_comparison(result: dict[str, Any]) -> str:
    lines = [
        f"# KPI 비교: {result['baseline']['label']} 대비 {result['candidate']['label']}",
        "",
        f"- 판정: {'통과' if result['passed'] else '불합격'}",
        f"- 개선 KPI: {len(result['improved'])}개, 실패 KPI: {len(result['failures'])}개, "
        f"예외 승인: {len(result['allowed'])}개",
        "",
        "| 구분 | KPI | 기준선 | 후보 | 판정 |",
        "|---|---|---:|---:|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['tier']} | {row['label']} (`{row['key']}`) | {_fmt(row['baseline'])} | "
            f"{_fmt(row['candidate'])} | {row['verdict']} |"
        )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def matches_protected(path: str, patterns: Iterable[str] = PROTECTED_PATTERNS) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def evaluate_diff_policy(
    *,
    changed_paths: Sequence[str],
    numstat: Sequence[tuple[int, int, str]],
    test_diff: str,
    production_diff: str = "",
    max_changed_lines: int = DEFAULT_MAX_CHANGED_LINES,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    for path in changed_paths:
        if matches_protected(path):
            violations.append({"code": "protected_path_changed", "path": path})

    removed_tests: set[str] = set()
    added_tests: set[str] = set()
    removed_asserts = 0
    added_asserts = 0
    for line in test_diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        match = _TEST_DEF.match(line)
        if match:
            (removed_tests if line.startswith("-") else added_tests).add(match.group(1))
        if line.startswith("+") and _SKIP_MARKER.search(line):
            violations.append({"code": "skip_marker_added", "line": line[1:].strip()[:160]})
        if _ASSERT_LINE.match(line):
            if line.startswith("-"):
                removed_asserts += 1
            else:
                added_asserts += 1
    for name in sorted(removed_tests - added_tests):
        violations.append({"code": "test_removed_or_renamed", "test": name})
    if removed_asserts:
        review.append(
            {
                "code": "assertions_removed",
                "removed": removed_asserts,
                "added": added_asserts,
                "note": "guardian must confirm each removed assertion was replaced by an equal or stricter one",
            }
        )

    normalized_paths = {path.replace("\\", "/") for path in changed_paths}
    gate_touches = sorted(normalized_paths & set(GATE_MODULES))
    current_file = ""
    for line in production_diff.splitlines():
        if line.startswith("+++ "):
            current_file = line[4:].strip().removeprefix("b/")
            continue
        if line.startswith("+") and current_file.startswith("app/"):
            for marker in BENCHMARK_ONLY_MARKERS:
                if marker in line:
                    violations.append(
                        {
                            "code": "benchmark_marker_in_production",
                            "path": current_file,
                            "marker": marker,
                            "line": line[1:].strip()[:160],
                        }
                    )
        if line.startswith("@@"):
            match = _GATE_FUNCTION.search(line.rsplit("@@", 1)[-1])
            if match and match.group(1) not in gate_touches:
                gate_touches.append(match.group(1))
    if gate_touches:
        review.append(
            {
                "code": "quality_gate_touched",
                "touched": gate_touches,
                "note": "guardian must confirm no check became more permissive for the same input",
            }
        )

    production_lines = sum(
        added + deleted for added, deleted, path in numstat if not path.replace("\\", "/").startswith("tests/")
    )
    if production_lines > max_changed_lines:
        violations.append(
            {"code": "change_too_large", "changed_lines": production_lines, "limit": max_changed_lines}
        )
    return {
        "passed": not violations,
        "violations": violations,
        "review_required": review,
        "changed_paths": list(changed_paths),
        "production_changed_lines": production_lines,
        "tests_added": sorted(added_tests - removed_tests),
    }


def collect_diff(base: str) -> tuple[list[str], list[tuple[int, int, str]], str, str]:
    # Compare the working tree to the base so uncommitted edits are covered too.
    names = [line for line in _git("diff", "--name-only", base).splitlines() if line.strip()]
    untracked = [
        line for line in _git("ls-files", "--others", "--exclude-standard").splitlines() if line.strip()
    ]
    numstat: list[tuple[int, int, str]] = []
    for line in _git("diff", "--numstat", base).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            numstat.append((int(parts[0]), int(parts[1]), parts[2]))
    for path in untracked:
        try:
            line_count = len((ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            line_count = 0
        numstat.append((line_count, 0, path))
    test_diff = _git("diff", "-U0", base, "--", "tests")
    for path in untracked:
        if path.replace("\\", "/").startswith("tests/") and path.endswith(".py"):
            text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
            test_diff += "\n" + "\n".join(f"+{line}" for line in text.splitlines())
    production_diff = _git("diff", "-U0", base, "--", "app", "scripts")
    return sorted(set(names) | set(untracked)), numstat, test_diff, production_diff


def _parse_allowances(values: Sequence[str] | None) -> dict[str, str]:
    allowances: dict[str, str] = {}
    for value in values or []:
        key, _, reason = value.partition("=")
        if not key.strip() or not reason.strip():
            raise SystemExit(f"--allow needs KEY=reason, got: {value!r}")
        allowances[key.strip()] = reason.strip()
    return allowances


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Measure this checkout and write snapshot.json")
    snap.add_argument("--label", required=True)
    snap.add_argument("--out-dir", default=str(ROOT / "reports" / "perf_kpi"))
    snap.add_argument("--skip", action="append", choices=STAGES)
    snap.add_argument("--skip-holdout", action="store_true")
    snap.add_argument("--corpus-dir")
    snap.add_argument("--holdout-manifest")
    snap.add_argument("--ncs-db")
    snap.add_argument("--mcp-url", default=os.getenv("PERF_KPI_MCP_URL") or DEFAULT_MCP_URL)
    snap.add_argument("--test-timeout", type=int, default=1800)
    snap.add_argument("--benchmark-timeout", type=int, default=1800)

    cmp_parser = sub.add_parser("compare", help="Apply KPI guardrails to baseline and candidate snapshots")
    cmp_parser.add_argument("baseline")
    cmp_parser.add_argument("candidate")
    cmp_parser.add_argument("--allow", action="append", help="KEY=reason; waives one non-limit regression")
    cmp_parser.add_argument("--output")

    diff_parser = sub.add_parser("diff-policy", help="Check the working tree diff against frozen-asset rules")
    diff_parser.add_argument("--base", required=True)
    diff_parser.add_argument("--max-changed-lines", type=int, default=DEFAULT_MAX_CHANGED_LINES)
    diff_parser.add_argument("--output")

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        snapshot = take_snapshot(args)
        print(json.dumps({"run_dir": snapshot["run_dir"], "kpis": snapshot["kpis"]}, ensure_ascii=False, indent=2))
        # Known quality gaps mark a stage "fail"; only a measurement error is
        # fatal here. ``compare`` decides whether a candidate is acceptable.
        errored = [name for name, stage in snapshot["stages"].items() if stage.get("status") == "error"]
        return 1 if errored else 0
    if args.command == "compare":
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        result = compare_snapshots(baseline, candidate, allowances=_parse_allowances(args.allow))
        rendered = render_comparison(result)
        print(rendered)
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            Path(args.output).with_suffix(".md").write_text(rendered, encoding="utf-8")
        return 0 if result["passed"] else 1
    changed_paths, numstat, test_diff, production_diff = collect_diff(args.base)
    result = evaluate_diff_policy(
        changed_paths=changed_paths,
        numstat=numstat,
        test_diff=test_diff,
        production_diff=production_diff,
        max_changed_lines=args.max_changed_lines,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
