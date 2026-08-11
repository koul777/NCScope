from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

import app.main as app_main
import app.repository as repository
from app.db import Base
from app.services.question_quality_orchestrator import is_history_duplicate
from scripts.simulate_question_regeneration import FOCUSES, _candidate


DEFAULT_REPORT_DIR = ROOT / "reports" / "question_quality_simulation"


def _session_factory(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def run_simulation(*, cycles: int = 30, reconnect_every: int = 5) -> dict[str, Any]:
    cycle_count = max(3, int(cycles))
    reconnect_interval = max(1, int(reconnect_every))
    temp_root = ROOT / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    original_session_local = repository.SessionLocal
    history: list[str] = []
    cases: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reconnect_count = 0

    with tempfile.TemporaryDirectory(prefix="question-review-lifecycle-", dir=temp_root) as temp_dir:
        db_path = Path(temp_dir) / "quality-lifecycle.db"
        engine, factory = _session_factory(db_path)
        repository.SessionLocal = factory
        try:
            with TestClient(app_main.app) as client:
                for cycle in range(1, cycle_count + 1):
                    if cycle > 1 and (cycle - 1) % reconnect_interval == 0:
                        engine.dispose()
                        engine, factory = _session_factory(db_path)
                        repository.SessionLocal = factory
                        reconnect_count += 1

                    method = app_main.QUALITY_INTERVIEW_METHODS[(cycle - 1) % len(app_main.QUALITY_INTERVIEW_METHODS)]
                    focus_type = tuple(FOCUSES)[(cycle - 1) % len(FOCUSES)]
                    focus = FOCUSES[focus_type]
                    strategy, plan, ksa = _candidate(method, focus_type, focus, shallow=True)
                    result = app_main._run_runtime_question_quality_orchestration(
                        strategy,
                        question_plan=plan,
                        ncs_ksa=ksa,
                        avoid_questions=history[-500:],
                        generation_offset=cycle - 1,
                    )
                    questions = [
                        row
                        for row in (result.get("interview_questions") or [])
                        if isinstance(row, dict)
                    ]
                    orchestration = result.get("question_quality_orchestration") or {}
                    report = result.get("question_quality_report") or {}
                    cycle_issues: list[str] = []
                    if len(questions) != 1:
                        cycle_issues.append("question_count")
                    question = questions[0] if questions else {}
                    question_text = str(question.get("question") or "").strip()
                    if not question_text:
                        cycle_issues.append("empty_question")
                    if question_text and is_history_duplicate(question_text, history):
                        cycle_issues.append("history_duplicate")
                    if orchestration.get("status") != "passed":
                        cycle_issues.append("runtime_orchestration")
                    if report.get("passed") is not True:
                        cycle_issues.append("full_quality_gate")

                    app_main._register_question_quality_evidence(
                        result,
                        source_endpoint="simulation://question-review-lifecycle",
                        ncs_matches=[
                            {
                                "ncsClCd": str(question.get("ncsClCd") or "SIM-REGEN-001"),
                                "compeUnitName": str(question.get("competency") or "문서관리"),
                            }
                        ],
                    )
                    control = result.get("quality_control") or {}
                    run_id = str(control.get("run_id") or "")
                    review_token = str(control.get("review_token") or "")
                    question_hash = str(question.get("question_hash") or "")
                    if not run_id or not review_token or not question_hash:
                        cycle_issues.append("quality_evidence_registration")

                    first_verdict = "needs_edit" if cycle % 2 else "approve"
                    second_verdict = "approve" if first_verdict == "needs_edit" else "needs_edit"
                    first_issue = "missing_ksa_evidence" if first_verdict == "needs_edit" else ""
                    second_issue = "method_task_mismatch" if second_verdict == "needs_edit" else ""
                    responses: list[int] = []
                    rejected_responses: list[int] = []
                    response_errors: list[dict[str, Any]] = []
                    invalid_payload_base = {
                        "review_token": review_token,
                        "question_text": question_text,
                        "verdict": "needs_edit",
                        "issue_codes": ["missing_ksa_evidence"],
                        "reviewer_ref": "operational-lifecycle-simulator",
                    }
                    bad_hash = client.post(
                        f"/api/quality/runs/{run_id}/review",
                        json={**invalid_payload_base, "question_hash": "0" * 64},
                    )
                    rejected_responses.append(bad_hash.status_code)
                    if bad_hash.status_code != 422:
                        cycle_issues.append(f"foreign_question_hash_not_rejected_{bad_hash.status_code}")
                    bad_text = client.post(
                        f"/api/quality/runs/{run_id}/review",
                        json={
                            **invalid_payload_base,
                            "question_hash": question_hash,
                            "question_text": f"{question_text} [tampered]",
                        },
                    )
                    rejected_responses.append(bad_text.status_code)
                    if bad_text.status_code != 422:
                        cycle_issues.append(f"tampered_question_text_not_rejected_{bad_text.status_code}")
                    before_valid_review = client.get(
                        f"/api/quality/runs/{run_id}",
                        headers={"X-Review-Token": review_token},
                    )
                    before_data = (
                        before_valid_review.json().get("data") or {}
                        if before_valid_review.status_code == 200
                        else {}
                    )
                    if before_valid_review.status_code != 200 or before_data.get("reviews"):
                        cycle_issues.append("invalid_review_mutated_state")
                    current_review_id = None
                    first_review_payload: dict[str, Any] | None = None
                    for review_index, (verdict, issue_code) in enumerate((
                        (first_verdict, first_issue),
                        (second_verdict, second_issue),
                    )):
                        valid_review_payload = {
                            "review_token": review_token,
                            "question_hash": question_hash,
                            "question_text": question_text,
                            "verdict": verdict,
                            "issue_codes": [issue_code] if issue_code else [],
                            "reviewer_ref": "operational-lifecycle-simulator",
                            "expected_review_id": current_review_id or 0,
                        }
                        if review_index == 0:
                            first_review_payload = dict(valid_review_payload)
                        response = client.post(
                            f"/api/quality/runs/{run_id}/review",
                            json=valid_review_payload,
                        )
                        responses.append(response.status_code)
                        if response.status_code == 200:
                            current_review_id = (response.json().get("data") or {}).get("id")
                        if response.status_code != 200:
                            cycle_issues.append(f"review_{verdict}_{response.status_code}")
                            try:
                                error_body: Any = response.json()
                            except Exception:
                                error_body = response.text[:500]
                            response_errors.append(
                                {"operation": f"review_{verdict}", "status": response.status_code, "body": error_body}
                            )
                        if review_index == 0 and response.status_code == 200:
                            retry = client.post(
                                f"/api/quality/runs/{run_id}/review",
                                json=valid_review_payload,
                            )
                            responses.append(retry.status_code)
                            retry_data = (retry.json().get("data") or {}) if retry.status_code == 200 else {}
                            first_data = response.json().get("data") or {}
                            if (
                                retry.status_code != 200
                                or retry_data.get("id") != first_data.get("id")
                                or retry_data.get("idempotent") is not True
                            ):
                                cycle_issues.append("exact_review_retry_not_idempotent")

                    if first_review_payload is not None:
                        stale_review = client.post(
                            f"/api/quality/runs/{run_id}/review",
                            json=first_review_payload,
                        )
                        rejected_responses.append(stale_review.status_code)
                        if stale_review.status_code != 409:
                            cycle_issues.append(
                                f"stale_review_retry_not_rejected_{stale_review.status_code}"
                            )

                    rollback_payload = {
                        "review_token": review_token,
                        "reviewer_ref": "operational-lifecycle-simulator",
                        "note": "결정 변경 후 직전 상태 복원 검증",
                        "expected_review_id": current_review_id,
                    }
                    rollback = client.post(
                        f"/api/quality/runs/{run_id}/questions/{question_hash}/rollback-review",
                        json=rollback_payload,
                    )
                    responses.append(rollback.status_code)
                    if rollback.status_code != 200:
                        cycle_issues.append(f"rollback_{rollback.status_code}")
                        try:
                            error_body = rollback.json()
                        except Exception:
                            error_body = rollback.text[:500]
                        response_errors.append(
                            {"operation": "rollback", "status": rollback.status_code, "body": error_body}
                        )
                    elif rollback.status_code == 200:
                        rollback_retry = client.post(
                            f"/api/quality/runs/{run_id}/questions/{question_hash}/rollback-review",
                            json=rollback_payload,
                        )
                        responses.append(rollback_retry.status_code)
                        rollback_data = rollback.json().get("data") or {}
                        rollback_retry_data = (
                            rollback_retry.json().get("data") or {}
                            if rollback_retry.status_code == 200
                            else {}
                        )
                        if (
                            rollback_retry.status_code != 200
                            or rollback_retry_data.get("rollback_event_id")
                            != rollback_data.get("rollback_event_id")
                            or rollback_retry_data.get("idempotent") is not True
                        ):
                            cycle_issues.append("exact_rollback_retry_not_idempotent")

                    fetched = client.get(
                        f"/api/quality/runs/{run_id}",
                        headers={"X-Review-Token": review_token},
                    )
                    responses.append(fetched.status_code)
                    run_data = (fetched.json().get("data") or {}) if fetched.status_code == 200 else {}
                    reviews = [row for row in (run_data.get("reviews") or []) if isinstance(row, dict)]
                    active = [row for row in reviews if row.get("active") is True]
                    if len(reviews) != 3:
                        cycle_issues.append("review_history_count")
                    if len(active) != 1 or str(active[0].get("verdict") or "") != first_verdict:
                        cycle_issues.append("rollback_restore_state")
                    expected_decision = "needs_edit" if first_verdict == "needs_edit" else "approved"
                    if str(run_data.get("final_decision") or "") != expected_decision:
                        cycle_issues.append("run_decision_consistency")

                    if cycle == 1:
                        wrong_token = client.get(
                            f"/api/quality/runs/{run_id}",
                            headers={"X-Review-Token": "wrong-token"},
                        )
                        if wrong_token.status_code != 401:
                            cycle_issues.append("wrong_token_not_rejected")

                    if question_text:
                        history.append(question_text)
                        history[:] = history[-500:]
                    case = {
                        "cycle": cycle,
                        "method": method,
                        "focus_type": focus_type,
                        "question_hash": hashlib.sha256(question_text.encode("utf-8")).hexdigest() if question_text else "",
                        "first_verdict": first_verdict,
                        "restored_verdict": str(active[0].get("verdict") or "") if len(active) == 1 else "",
                        "final_decision": str(run_data.get("final_decision") or ""),
                        "http_statuses": responses,
                        "rejected_statuses": rejected_responses,
                        "response_errors": response_errors,
                        "issues": list(dict.fromkeys(cycle_issues)),
                    }
                    cases.append(case)
                    if cycle_issues:
                        failures.append(
                            {
                                **case,
                                "question": question_text,
                                "question_length": len(question_text),
                                "run_id": run_id,
                                "registered_question_hash": question_hash,
                            }
                        )

            metrics = repository.question_quality_metrics()
            expected_reviews = cycle_count * 3
            if int(metrics.get("runs") or 0) != cycle_count:
                failures.append({"cycle": 0, "issues": ["run_metric_count"], "metrics": metrics})
            if int(metrics.get("reviews") or 0) != expected_reviews:
                failures.append({"cycle": 0, "issues": ["review_metric_count"], "metrics": metrics})
        finally:
            engine.dispose()
            repository.SessionLocal = original_session_local

    return {
        "schema_version": "question_review_lifecycle_simulation_v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "passed" if not failures else "failed",
        "cycles": cycle_count,
        "reconnect_every": reconnect_interval,
        "reconnect_count": reconnect_count,
        "unique_question_count": len(set(history)),
        "failure_count": len(failures),
        "invariants": {
            "generation_remains_unique": len(set(history)) == cycle_count,
            "all_quality_gates_pass": not any(
                any(issue in {"runtime_orchestration", "full_quality_gate"} for issue in case.get("issues", []))
                for case in failures
            ),
            "review_change_and_rollback_succeed": not any(
                any(str(issue).startswith(("review_", "rollback_")) for issue in case.get("issues", []))
                for case in failures
            ),
            "one_active_decision_per_run": not any(
                "rollback_restore_state" in case.get("issues", []) for case in failures
            ),
            "restart_preserves_state": not any(
                "run_decision_consistency" in case.get("issues", []) for case in failures
            ),
            "wrong_token_rejected": not any(
                "wrong_token_not_rejected" in case.get("issues", []) for case in failures
            ),
            "foreign_or_tampered_question_rejected_without_mutation": not any(
                any(
                    str(issue).startswith(
                        (
                            "foreign_question_hash_not_rejected_",
                            "tampered_question_text_not_rejected_",
                            "invalid_review_mutated_state",
                        )
                    )
                    for issue in case.get("issues", [])
                )
                for case in failures
            ),
            "exact_review_retry_is_idempotent": not any(
                "exact_review_retry_not_idempotent" in case.get("issues", []) for case in failures
            ),
            "stale_review_retry_is_rejected": not any(
                any(
                    str(issue).startswith("stale_review_retry_not_rejected_")
                    for issue in case.get("issues", [])
                )
                for case in failures
            ),
            "exact_rollback_retry_is_idempotent": not any(
                "exact_rollback_retry_not_idempotent" in case.get("issues", []) for case in failures
            ),
        },
        "cases": cases,
        "failures": failures,
    }


def write_report(result: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}-{uuid4().hex[:8]}"
    json_path = report_dir / f"review-lifecycle-simulation-{stamp}.json"
    markdown_path = report_dir / f"review-lifecycle-simulation-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 면접 문항 검토·재생성 운영 수명주기 시뮬레이션",
        "",
        f"- 상태: **{result['status']}**",
        f"- 생성·검토 회차: {result['cycles']}회",
        f"- DB 재연결: {result['reconnect_count']}회 (매 {result['reconnect_every']}회)",
        f"- 고유 문항: {result['unique_question_count']}개",
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
            "| 회차 | 면접기법 | KSA | 최초결정 | 되돌림 후 | 런 상태 | 거부 HTTP | 정상 HTTP | 오류 |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for case in result["cases"]:
        lines.append(
            f"| {case['cycle']} | {case['method']} | {case['focus_type']} | {case['first_verdict']} | "
            f"{case['restored_verdict']} | {case['final_decision']} | "
            f"{','.join(str(status) for status in case.get('rejected_statuses', []))} | "
            f"{','.join(str(status) for status in case['http_statuses'])} | "
            f"{','.join(case['issues']) or '-'} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate repeated generate-review-change-rollback-regenerate flows.")
    parser.add_argument("--cycles", type=int, default=30)
    parser.add_argument("--reconnect-every", type=int, default=5)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_simulation(cycles=args.cycles, reconnect_every=args.reconnect_every)
    json_path, markdown_path = write_report(result, args.report_dir)
    print(
        json.dumps(
            {
                "status": result["status"],
                "cycles": result["cycles"],
                "reconnect_count": result["reconnect_count"],
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
