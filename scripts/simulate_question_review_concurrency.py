from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

import app.repository as repository
from app.db import Base


DEFAULT_REPORT_DIR = ROOT / "reports" / "question_quality_simulation"


def _session_factory(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _run_payload(*, run_id: str, token: str, question: str) -> dict[str, Any]:
    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
    return {
        "id": run_id,
        "review_token": token,
        "source_endpoint": "simulation://question-review-concurrency",
        "ncs_codes": ["SIM-CONCURRENT-001"],
        "competency_names": ["문서관리"],
        "quality_policy_version": "concurrency-simulation-v1",
        "question_count": 1,
        "ready_count": 1,
        "review_required": True,
        "escalation_required": False,
        "exception_allowed": True,
        "trigger_codes": [],
        "evidence": {
            "question_items": [
                {
                    "index": 1,
                    "question_hash": question_hash,
                    "method": "상황면접",
                    "ncs_code": "SIM-CONCURRENT-001",
                }
            ]
        },
    }


def _concurrent_call(operations: list[Callable[[], dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[str]]:
    barrier = threading.Barrier(len(operations))

    def invoke(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        barrier.wait(timeout=10)
        return operation()

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [executor.submit(invoke, operation) for operation in operations]
        for future in as_completed(futures):
            try:
                results.append(future.result(timeout=30))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    return results, errors


def run_simulation(
    *,
    rounds: int = 50,
    parallel_reviews: int = 8,
    mixed_operations: int = 6,
    reconnect_every: int = 5,
) -> dict[str, Any]:
    round_count = max(1, int(rounds))
    review_workers = max(2, int(parallel_reviews))
    mixed_workers = max(2, int(mixed_operations))
    reconnect_interval = max(1, int(reconnect_every))
    temp_root = ROOT / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    original_session_local = repository.SessionLocal
    cases: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reconnect_count = 0

    with tempfile.TemporaryDirectory(prefix="question-review-concurrency-", dir=temp_root) as temp_dir:
        db_path = Path(temp_dir) / "quality-concurrency.db"
        engine, factory = _session_factory(db_path)
        repository.SessionLocal = factory
        try:
            for round_number in range(1, round_count + 1):
                if round_number > 1 and (round_number - 1) % reconnect_interval == 0:
                    engine.dispose()
                    engine, factory = _session_factory(db_path)
                    repository.SessionLocal = factory
                    reconnect_count += 1

                run_id = f"qqr-concurrency-{round_number}"
                token = f"qqt_concurrency_{round_number}"
                question = (
                    f"문서관리 상황 {round_number}에서 원자료와 검토 이력을 대조해 오류를 판별하고 "
                    "보고·수정·재검증 순서와 품질 확인 결과를 설명해 주세요."
                )
                question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
                repository.create_question_quality_run(
                    _run_payload(run_id=run_id, token=token, question=question)
                )

                def review_operation(index: int) -> Callable[[], dict[str, Any]]:
                    verdict = "needs_edit" if index % 2 else "approve"

                    def operation() -> dict[str, Any]:
                        return repository.record_question_quality_review(
                            {
                                "run_id": run_id,
                                "review_token": token,
                                "question_hash": question_hash,
                                "question_text": question,
                                "verdict": verdict,
                                "issue_codes": ["missing_ksa_evidence"] if verdict == "needs_edit" else [],
                                "reviewer_ref": f"parallel-review-{index}",
                            }
                        )

                    return operation

                initial_results, initial_errors = _concurrent_call(
                    [review_operation(index) for index in range(review_workers)]
                )

                mixed: list[Callable[[], dict[str, Any]]] = []
                mixed_review_count = 0
                mixed_rollback_count = 0
                for index in range(mixed_workers):
                    if index % 2:
                        mixed_rollback_count += 1

                        def rollback_operation(index: int = index) -> dict[str, Any]:
                            return repository.rollback_question_quality_review(
                                run_id=run_id,
                                review_token=token,
                                question_hash=question_hash,
                                reviewer_ref=f"parallel-rollback-{index}",
                                note="동시 검토·롤백 직렬화 검증",
                            )

                        mixed.append(rollback_operation)
                    else:
                        mixed_review_count += 1
                        mixed.append(review_operation(review_workers + index))

                mixed_results, mixed_errors = _concurrent_call(mixed)
                persisted = repository.get_question_quality_run(run_id) or {}
                reviews = [row for row in (persisted.get("reviews") or []) if isinstance(row, dict)]
                active = [row for row in reviews if row.get("active") is True]
                expected_review_rows = review_workers + mixed_review_count + mixed_rollback_count
                issues: list[str] = []
                if initial_errors or mixed_errors:
                    issues.append("operation_exception")
                if len(initial_results) != review_workers:
                    issues.append("initial_result_count")
                if len(mixed_results) != mixed_workers:
                    issues.append("mixed_result_count")
                if len(reviews) != expected_review_rows:
                    issues.append("review_history_count")
                if len(active) != 1:
                    issues.append("active_decision_count")
                expected_decision = ""
                if len(active) == 1:
                    expected_decision = "approved" if active[0].get("verdict") == "approve" else "needs_edit"
                    if persisted.get("final_decision") != expected_decision:
                        issues.append("run_decision_consistency")
                if sum(row.get("verdict") == "rollback" for row in reviews) != mixed_rollback_count:
                    issues.append("rollback_event_count")

                case = {
                    "round": round_number,
                    "review_workers": review_workers,
                    "mixed_workers": mixed_workers,
                    "review_rows": len(reviews),
                    "active_reviews": len(active),
                    "active_verdict": str(active[0].get("verdict") or "") if len(active) == 1 else "",
                    "final_decision": str(persisted.get("final_decision") or ""),
                    "expected_decision": expected_decision,
                    "initial_errors": initial_errors,
                    "mixed_errors": mixed_errors,
                    "issues": issues,
                }
                cases.append(case)
                if issues:
                    failures.append(case)

            metrics = repository.question_quality_metrics()
        finally:
            engine.dispose()
            repository.SessionLocal = original_session_local

    total_mutations = round_count * (review_workers + mixed_workers)
    return {
        "schema_version": "question_review_concurrency_simulation_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "passed" if not failures else "failed",
        "rounds": round_count,
        "parallel_reviews": review_workers,
        "mixed_operations": mixed_workers,
        "reconnect_every": reconnect_interval,
        "reconnect_count": reconnect_count,
        "total_mutations": total_mutations,
        "failure_count": len(failures),
        "invariants": {
            "all_mutations_return": not any("operation_exception" in row["issues"] for row in failures),
            "one_active_decision_per_run": not any("active_decision_count" in row["issues"] for row in failures),
            "run_decision_matches_active_review": not any(
                "run_decision_consistency" in row["issues"] for row in failures
            ),
            "rollback_history_is_complete": not any(
                issue in {"review_history_count", "rollback_event_count"}
                for row in failures
                for issue in row["issues"]
            ),
        },
        "metrics": metrics,
        "cases": cases,
        "failures": failures,
    }


def write_report(result: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}-{uuid4().hex[:8]}"
    json_path = report_dir / f"review-concurrency-simulation-{stamp}.json"
    markdown_path = report_dir / f"review-concurrency-simulation-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 면접 문항 동시 검토·롤백 시뮬레이션",
        "",
        f"- 상태: **{result['status']}**",
        f"- 독립 런: {result['rounds']}개",
        f"- 동시 변경 요청: {result['total_mutations']}건",
        f"- DB 재연결: {result['reconnect_count']}회",
        f"- 실패: {result['failure_count']}건",
        "",
        "## 운영 불변조건",
        "",
    ]
    for name, passed in result["invariants"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — `{name}`")
    lines.extend(
        [
            "",
            "## 회차별 상태",
            "",
            "| 회차 | 검토 이력 | 활성 결정 | 활성 판정 | 런 상태 | 오류 |",
            "| ---: | ---: | ---: | --- | --- | --- |",
        ]
    )
    for case in result["cases"]:
        lines.append(
            f"| {case['round']} | {case['review_rows']} | {case['active_reviews']} | "
            f"{case['active_verdict']} | {case['final_decision']} | {','.join(case['issues']) or '-'} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stress concurrent review and rollback serialization.")
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--parallel-reviews", type=int, default=8)
    parser.add_argument("--mixed-operations", type=int, default=6)
    parser.add_argument("--reconnect-every", type=int, default=5)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_simulation(
        rounds=args.rounds,
        parallel_reviews=args.parallel_reviews,
        mixed_operations=args.mixed_operations,
        reconnect_every=args.reconnect_every,
    )
    json_path, markdown_path = write_report(result, args.report_dir)
    print(
        json.dumps(
            {
                "status": result["status"],
                "rounds": result["rounds"],
                "total_mutations": result["total_mutations"],
                "failure_count": result["failure_count"],
                "json_report": str(json_path),
                "markdown_report": str(markdown_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
