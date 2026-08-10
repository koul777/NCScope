from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "reports" / "question_quality_loop"


@dataclass(frozen=True)
class Stage:
    name: str
    command: tuple[str, ...]
    timeout_seconds: int


@dataclass
class StageResult:
    name: str
    command: list[str]
    started_at: str
    elapsed_seconds: float
    returncode: int
    status: str
    log_path: str


def _now() -> datetime:
    return datetime.now().astimezone()


def _stamp() -> str:
    return _now().strftime("%Y%m%d_%H%M%S")


def build_stages(args: argparse.Namespace) -> list[Stage]:
    python = sys.executable
    stages = [
        Stage(
            name="quality-regression-tests",
            command=(
                python,
                "-m",
                "pytest",
                "-q",
                "--basetemp=.tmp/pytest-question-quality-loop",
                "tests/test_interview_customization.py",
                "tests/test_question_generation.py",
                "tests/test_question_quality_runner.py",
                "tests/test_ncs_official_interview_samples.py",
                "tests/test_question_quality_ops.py",
                "tests/test_question_quality_persistence.py",
                "tests/test_question_quality_eval_runner.py",
                "tests/test_question_quality_orchestrator.py",
                "tests/test_ax_readiness.py",
            ),
            timeout_seconds=max(60, int(args.test_timeout_seconds)),
        )
    ]
    if not args.skip_feedback_eval:
        stages.append(
            Stage(
                name="feedback-eval-regression",
                command=(
                    python,
                    "scripts/check_question_quality_eval_cases.py",
                    "--report-dir",
                    str(ROOT / "reports" / "question_quality_loop"),
                ),
                timeout_seconds=max(60, int(args.test_timeout_seconds)),
            )
        )
    if not args.skip_official:
        stages.append(
            Stage(
                name="official-ncs-sample-profile",
                command=(
                    python,
                    "scripts/benchmark_ncs_official_interview_samples.py",
                    "--collection",
                    args.official_collection,
                    "--limit",
                    str(max(1, int(args.official_limit))),
                    "--out-dir",
                    str(ROOT / ".tmp" / "ncs_official_interview"),
                    "--report-dir",
                    str(ROOT / "reports"),
                ),
                timeout_seconds=max(120, int(args.official_timeout_seconds)),
            )
        )
    if not args.skip_alio:
        benchmark_mode = "model" if args.model_eval else "template"
        stages.append(
            Stage(
                name=f"alio-{benchmark_mode}-quality-benchmark",
                command=(
                    python,
                    "scripts/evaluate_alio_question_quality.py",
                    "--benchmark-mode",
                    benchmark_mode,
                    "--limit",
                    str(max(1, int(args.alio_limit))),
                    "--questions-per-doc",
                    str(max(1, int(args.questions_per_doc))),
                    "--follow-up-count",
                    "3",
                    "--report-dir",
                    str(ROOT / "reports"),
                ),
                timeout_seconds=max(120, int(args.alio_timeout_seconds)),
            )
        )
    return stages


def _safe_command(command: Sequence[str]) -> str:
    redacted: list[str] = []
    hide_next = False
    for value in command:
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        if value in {"--openai-api-key", "--api-key", "--token"}:
            redacted.append(value)
            hide_next = True
            continue
        redacted.append(value)
    return subprocess.list2cmdline(redacted)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def run_stage(stage: Stage, cycle_dir: Path) -> StageResult:
    started = _now()
    started_perf = time.perf_counter()
    log_path = cycle_dir / f"{stage.name}.log"
    run_env = os.environ.copy()
    if stage.name == "quality-regression-tests":
        run_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    try:
        completed = subprocess.run(
            list(stage.command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=stage.timeout_seconds,
            env=run_env,
            check=False,
        )
        output = "\n".join(
            part.rstrip()
            for part in (completed.stdout, completed.stderr)
            if part and part.strip()
        )
        log_path.write_text(output + ("\n" if output else ""), encoding="utf-8")
        returncode = int(completed.returncode)
        status = "passed" if returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        log_path.write_text(
            f"Timed out after {stage.timeout_seconds}s\n{stdout}\n{stderr}".strip() + "\n",
            encoding="utf-8",
        )
        returncode = 124
        status = "timed_out"
    elapsed = round(time.perf_counter() - started_perf, 2)
    return StageResult(
        name=stage.name,
        command=list(stage.command),
        started_at=started.isoformat(),
        elapsed_seconds=elapsed,
        returncode=returncode,
        status=status,
        log_path=_display_path(log_path),
    )


def write_cycle_report(cycle_dir: Path, cycle_number: int, results: list[StageResult]) -> Path:
    passed = sum(result.status == "passed" for result in results)
    report_path = cycle_dir / "summary.md"
    lines = [
        f"# Question quality cycle {cycle_number}",
        "",
        f"- Finished at: {_now().isoformat()}",
        f"- Passed stages: {passed}/{len(results)}",
        "- Promotion rule: all configured stages must pass before a generator change is accepted.",
        "",
        "| stage | status | seconds | exit | log |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for result in results:
        lines.append(
            f"| {result.name} | {result.status} | {result.elapsed_seconds:.2f} | "
            f"{result.returncode} | `{result.log_path}` |"
        )
    lines.extend(["", "## Commands", ""])
    for result in results:
        lines.append(f"- `{_safe_command(result.command)}`")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def write_state(report_dir: Path, payload: dict[str, object]) -> None:
    state_path = report_dir / "state.json"
    temp_path = report_dir / "state.json.tmp"
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(state_path)


def run_cycle(args: argparse.Namespace, cycle_number: int) -> tuple[bool, Path]:
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    cycle_dir = report_dir / f"cycle-{_stamp()}"
    cycle_dir.mkdir(parents=True, exist_ok=False)
    stages = build_stages(args)
    results: list[StageResult] = []
    for stage in stages:
        print(f"[{stage.name}] starting: {_safe_command(stage.command)}", flush=True)
        result = run_stage(stage, cycle_dir)
        results.append(result)
        print(f"[{stage.name}] {result.status} ({result.elapsed_seconds:.2f}s)", flush=True)
    report_path = write_cycle_report(cycle_dir, cycle_number, results)
    success = bool(results and all(result.status == "passed" for result in results))
    write_state(
        report_dir,
        {
            "cycle": cycle_number,
            "finished_at": _now().isoformat(),
            "success": success,
            "report": str(report_path.relative_to(ROOT)),
            "results": [asdict(result) for result in results],
        },
    )
    return success, report_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the repeatable NCS interview-question quality improvement evidence loop."
    )
    parser.add_argument("--cycles", type=int, default=1, help="Number of cycles. Use 0 to run until interrupted.")
    parser.add_argument("--interval-minutes", type=float, default=1440.0)
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--official-collection", choices=("interview-model", "evaluation-sample", "all"), default="all")
    parser.add_argument("--official-limit", type=int, default=4)
    parser.add_argument("--alio-limit", type=int, default=4)
    parser.add_argument("--questions-per-doc", type=int, default=7)
    parser.add_argument("--model-eval", action="store_true", help="Use the configured OpenAI model; may incur API cost.")
    parser.add_argument("--skip-official", action="store_true")
    parser.add_argument("--skip-alio", action="store_true")
    parser.add_argument("--skip-feedback-eval", action="store_true")
    parser.add_argument("--test-timeout-seconds", type=int, default=300)
    parser.add_argument("--official-timeout-seconds", type=int, default=1800)
    parser.add_argument("--alio-timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    requested_cycles = max(0, int(args.cycles))
    cycle_number = 0
    all_passed = True
    try:
        while requested_cycles == 0 or cycle_number < requested_cycles:
            cycle_number += 1
            success, report_path = run_cycle(args, cycle_number)
            all_passed = all_passed and success
            print(f"cycle report: {report_path}", flush=True)
            if requested_cycles != 0 and cycle_number >= requested_cycles:
                break
            time.sleep(max(1.0, float(args.interval_minutes) * 60.0))
    except KeyboardInterrupt:
        print("quality loop interrupted", file=sys.stderr)
        return 130
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
