from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.question_quality_orchestrator import (  # noqa: E402
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
)


def _request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> tuple[int, dict[str, Any], str]:
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers=request_headers,
        method=method.upper(),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return int(response.status), parsed if isinstance(parsed, dict) else {}, str(response.headers.get("Content-Type") or "")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw[:1000]}
        return int(exc.code), parsed if isinstance(parsed, dict) else {}, str(exc.headers.get("Content-Type") or "")


def _first_question(response: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    strategy = response.get("strategy") if isinstance(response.get("strategy"), dict) else {}
    questions = strategy.get("interview_questions") if isinstance(strategy.get("interview_questions"), list) else []
    question = questions[0] if questions and isinstance(questions[0], dict) else {}
    return strategy, question


def _generation_payload(*, generation_offset: int, avoid_questions: list[str]) -> dict[str, Any]:
    return {
        "notice_text": (
            "스포츠시설 경영기획 담당자는 이용객 자료와 운영 실적을 분석하고 계절별 수요 변동, "
            "안전 요구, 예산 제약을 반영한 운영계획을 수립해야 합니다."
        ),
        "duty_text": "스포츠시설 경영환경 분석, 소비자 패턴 분석, 운영계획 수립, 결과 지표 확인",
        "evaluation_text": "분석 절차와 도구, 판단 근거, 분석 산출물, 품질 검증, 결과 지표",
        "selected_ncs": [
            {
                "ncsClCd": "1204020201_22v3",
                "compeUnitName": "스포츠시설 경영기획",
                "ncsSubdCdnm": "스포츠시설운영관리",
                "compeUnitDef": "스포츠시설의 경영환경과 소비자 특성을 분석하여 실행 가능한 운영계획을 수립하는 능력",
            }
        ],
        "question_plan": {
            "items": [
                {
                    "detail": "스포츠시설운영관리",
                    "enabled": True,
                    "main_count": 1,
                    "follow_up_count": 3,
                }
            ]
        },
        "interview_methods": ["경험면접"],
        "generation_offset": generation_offset,
        "avoid_questions": avoid_questions,
    }


def run_live_smoke(base_url: str, timeout: float = 120.0) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    evidence: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []

    def call(
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allowed_error_statuses: set[int] | None = None,
    ) -> tuple[int, dict[str, Any], str]:
        status, body, content_type = _request_json(
            base_url,
            method,
            path,
            payload,
            headers,
            timeout,
        )
        if status >= 400 and status not in (allowed_error_statuses or set()):
            failures.append({"operation": f"{method} {path}", "status": status, "body": body})
        return status, body, content_type

    health_status, health, health_content_type = call("GET", "/health")
    checks["health_ok"] = health_status == 200 and health.get("status") == "ok"
    ncs_mcp = health.get("ncs_mcp") if isinstance(health.get("ncs_mcp"), dict) else {}
    checks["ncs_mcp_ready"] = bool(ncs_mcp.get("reachable") and ncs_mcp.get("ksaAvailable"))
    checks["json_charset_utf8"] = "application/json" in health_content_type.lower() and "charset=utf-8" in health_content_type.lower()

    first_status, first_body, first_content_type = call(
        "POST",
        "/api/questions/generate-from-text",
        _generation_payload(generation_offset=0, avoid_questions=[]),
    )
    first_strategy, first_question = _first_question(first_body)
    first_text = str(first_question.get("question") or "").strip()
    first_orchestration = (
        first_strategy.get("question_quality_orchestration")
        if isinstance(first_strategy.get("question_quality_orchestration"), dict)
        else {}
    )
    quality_control = first_strategy.get("quality_control") if isinstance(first_strategy.get("quality_control"), dict) else {}
    run_id = str(quality_control.get("run_id") or "").strip()
    review_token = str(quality_control.get("review_token") or "").strip()
    question_hash = str(first_question.get("question_hash") or "").strip()
    report = first_strategy.get("question_quality_report") if isinstance(first_strategy.get("question_quality_report"), dict) else {}

    checks["first_generation_ok"] = first_status == 200 and bool(first_text)
    checks["first_quality_gate_passed"] = report.get("passed") is True
    checks["korean_round_trip"] = bool(re.search(r"[가-힣]", first_text))
    checks["generation_charset_utf8"] = "charset=utf-8" in first_content_type.lower()
    checks["quality_evidence_registered"] = bool(run_id and review_token and re.fullmatch(r"[0-9a-f]{64}", question_hash))

    first_review_data: dict[str, Any] = {}
    retry_review_data: dict[str, Any] = {}
    changed_data: dict[str, Any] = {}
    post_rollback_review_data: dict[str, Any] = {}
    second_rollback_data: dict[str, Any] = {}
    reviews: list[dict[str, Any]] = []
    active_reviews: list[dict[str, Any]] = []
    if checks["quality_evidence_registered"]:
        review_payload = {
            "review_token": review_token,
            "question_hash": question_hash,
            "question_text": first_text,
            "verdict": "needs_edit",
            "issue_codes": ["method_task_mismatch"],
            "reviewer_ref": "live-http-operational-smoke",
            "note": "동일 HTTP 요청 재전송 멱등성 확인",
            "expected_review_id": 0,
        }
        review_path = f"/api/quality/runs/{run_id}/review"
        first_review_status, first_review, _ = call("POST", review_path, review_payload)
        retry_status, retry_review, _ = call("POST", review_path, review_payload)
        first_review_data = first_review.get("data") if isinstance(first_review.get("data"), dict) else {}
        retry_review_data = retry_review.get("data") if isinstance(retry_review.get("data"), dict) else {}
        checks["first_review_saved"] = first_review_status == 200 and bool(first_review_data.get("id"))
        checks["exact_retry_idempotent"] = bool(
            retry_status == 200
            and retry_review_data.get("id") == first_review_data.get("id")
            and retry_review_data.get("idempotent") is True
        )

        changed_payload = {
            **review_payload,
            "note": "검토자가 내용을 보완하여 새 결정으로 저장",
            "expected_review_id": first_review_data.get("id"),
        }
        changed_status, changed_review, _ = call("POST", review_path, changed_payload)
        changed_data = changed_review.get("data") if isinstance(changed_review.get("data"), dict) else {}
        checks["changed_review_creates_history"] = bool(
            changed_status == 200
            and changed_data.get("id")
            and changed_data.get("id") != first_review_data.get("id")
            and changed_data.get("idempotent") is False
        )
        stale_review_status, stale_review, _ = call(
            "POST",
            review_path,
            review_payload,
            allowed_error_statuses={409},
        )
        checks["stale_review_retry_rejected"] = bool(
            stale_review_status == 409
            and "active review changed" in str(stale_review.get("detail") or "")
        )

        rollback_path = f"/api/quality/runs/{run_id}/questions/{question_hash}/rollback-review"
        rollback_payload = {
            "review_token": review_token,
            "reviewer_ref": "live-http-operational-smoke",
            "note": "직전 상태 복원 검증",
            "expected_review_id": changed_data.get("id"),
        }
        rollback_status, rollback, _ = call(
            "POST",
            rollback_path,
            rollback_payload,
        )
        rollback_retry_status, rollback_retry, _ = call("POST", rollback_path, rollback_payload)
        rollback_data = rollback.get("data") if isinstance(rollback.get("data"), dict) else {}
        rollback_retry_data = (
            rollback_retry.get("data") if isinstance(rollback_retry.get("data"), dict) else {}
        )
        checks["rollback_saved"] = rollback_status == 200 and bool(rollback_data.get("rollback_event_id"))
        checks["exact_rollback_retry_idempotent"] = bool(
            rollback_retry_status == 200
            and rollback_retry_data.get("rollback_event_id") == rollback_data.get("rollback_event_id")
            and rollback_retry_data.get("idempotent") is True
        )

        post_rollback_payload = {
            **review_payload,
            "verdict": "reject",
            "issue_codes": ["wrong_ncs_alignment"],
            "note": "첫 롤백 뒤 새 결정을 저장해 실제 선행 결정 포인터 검증",
            "expected_review_id": first_review_data.get("id"),
        }
        post_rollback_status, post_rollback_review, _ = call(
            "POST",
            review_path,
            post_rollback_payload,
        )
        post_rollback_review_data = (
            post_rollback_review.get("data")
            if isinstance(post_rollback_review.get("data"), dict)
            else {}
        )
        checks["review_after_rollback_saved"] = bool(
            post_rollback_status == 200
            and post_rollback_review_data.get("id")
            and post_rollback_review_data.get("id") != first_review_data.get("id")
            and post_rollback_review_data.get("idempotent") is False
        )

        stale_rollback_status, stale_rollback, _ = call(
            "POST",
            rollback_path,
            rollback_payload,
            allowed_error_statuses={409},
        )
        checks["stale_rollback_rejected"] = bool(
            stale_rollback_status == 409
            and "active review changed" in str(stale_rollback.get("detail") or "")
        )

        second_rollback_payload = {
            **rollback_payload,
            "note": "새 결정 취소 후 실제 선행 결정 복원 검증",
            "expected_review_id": post_rollback_review_data.get("id"),
        }
        second_rollback_status, second_rollback, _ = call(
            "POST",
            rollback_path,
            second_rollback_payload,
        )
        second_rollback_data = (
            second_rollback.get("data")
            if isinstance(second_rollback.get("data"), dict)
            else {}
        )
        checks["second_rollback_saved"] = bool(
            second_rollback_status == 200
            and second_rollback_data.get("rollback_event_id")
            and second_rollback_data.get("rolled_back_review_id")
            == post_rollback_review_data.get("id")
            and second_rollback_data.get("restored_review_id")
            == first_review_data.get("id")
        )

        run_status, run_body, _ = call(
            "GET",
            f"/api/quality/runs/{run_id}",
            headers={"X-Review-Token": review_token},
        )
        run_data = run_body.get("data") if isinstance(run_body.get("data"), dict) else {}
        reviews = [row for row in (run_data.get("reviews") or []) if isinstance(row, dict)]
        active_reviews = [row for row in reviews if row.get("active") is True]
        checks["retry_did_not_duplicate_history"] = run_status == 200 and len(reviews) == 5
        checks["single_active_review_after_rollback"] = len(active_reviews) == 1
        checks["multi_rollback_restored_true_predecessor"] = bool(
            len(active_reviews) == 1
            and active_reviews[0].get("id") == first_review_data.get("id")
            and active_reviews[0].get("verdict") == review_payload["verdict"]
        )
    else:
        checks.update(
            {
                "first_review_saved": False,
                "exact_retry_idempotent": False,
                "changed_review_creates_history": False,
                "stale_review_retry_rejected": False,
                "rollback_saved": False,
                "exact_rollback_retry_idempotent": False,
                "review_after_rollback_saved": False,
                "stale_rollback_rejected": False,
                "second_rollback_saved": False,
                "retry_did_not_duplicate_history": False,
                "single_active_review_after_rollback": False,
                "multi_rollback_restored_true_predecessor": False,
            }
        )

    second_status, second_body, _ = call(
        "POST",
        "/api/questions/generate-from-text",
        _generation_payload(generation_offset=1, avoid_questions=[first_text]),
    )
    second_strategy, second_question = _first_question(second_body)
    second_text = str(second_question.get("question") or "").strip()
    second_orchestration = (
        second_strategy.get("question_quality_orchestration")
        if isinstance(second_strategy.get("question_quality_orchestration"), dict)
        else {}
    )
    second_report = second_strategy.get("question_quality_report") if isinstance(second_strategy.get("question_quality_report"), dict) else {}
    second_control = second_strategy.get("quality_control") if isinstance(second_strategy.get("quality_control"), dict) else {}
    checks["second_generation_ok"] = second_status == 200 and bool(second_text)
    checks["second_question_changed"] = bool(first_text and second_text and first_text != second_text)
    checks["second_quality_gate_passed"] = second_report.get("passed") is True
    checks["second_run_rotated"] = bool(run_id and second_control.get("run_id") and second_control.get("run_id") != run_id)
    checks["runtime_policy_current"] = bool(
        first_orchestration.get("policy") == RUNTIME_QUESTION_ORCHESTRATION_POLICY
        and second_orchestration.get("policy") == RUNTIME_QUESTION_ORCHESTRATION_POLICY
    )

    evidence.update(
        {
            "base_url": base_url,
            "first_run_id": run_id,
            "second_run_id": str(second_control.get("run_id") or ""),
            "first_question_hash": question_hash,
            "second_question_hash": str(second_question.get("question_hash") or ""),
            "review_history_count": len(reviews),
            "active_review_count": len(active_reviews),
            "first_review_id": first_review_data.get("id"),
            "retry_review_id": retry_review_data.get("id"),
            "changed_review_id": changed_data.get("id"),
            "post_rollback_review_id": post_rollback_review_data.get("id"),
            "rollback_event_id": rollback_data.get("rollback_event_id") if checks["quality_evidence_registered"] else None,
            "rollback_retry_event_id": (
                rollback_retry_data.get("rollback_event_id") if checks["quality_evidence_registered"] else None
            ),
            "second_rollback_event_id": second_rollback_data.get("rollback_event_id"),
            "second_rollback_restored_review_id": second_rollback_data.get("restored_review_id"),
            "first_question": first_text,
            "second_question": second_text,
            "expected_runtime_policy": RUNTIME_QUESTION_ORCHESTRATION_POLICY,
            "first_runtime_policy": str(first_orchestration.get("policy") or ""),
            "second_runtime_policy": str(second_orchestration.get("policy") or ""),
        }
    )
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "schema_version": "question_review_live_http_smoke_v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "passed": not failed_checks and not failures,
        "checks": checks,
        "failed_checks": failed_checks,
        "failures": failures,
        "evidence": evidence,
    }


def write_report(result: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    # This smoke normally completes in well under one second.  A seconds-only
    # filename silently overwrote evidence when operators retried immediately
    # or two quality-loop workers shared the same report directory.
    stamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}-{uuid4().hex[:8]}"
    json_path = report_dir / f"live-http-review-smoke-{stamp}.json"
    markdown_path = report_dir / f"live-http-review-smoke-{stamp}.md"
    sensitive_keys = {"review_token", "openai_api_key", "authorization", "x_review_token"}

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): "[REDACTED]" if str(key).lower() in sensitive_keys else redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    safe_result = redact(result)
    json_path.write_text(json.dumps(safe_result, ensure_ascii=False, indent=2), encoding="utf-8")

    checks = safe_result.get("checks") if isinstance(safe_result.get("checks"), dict) else {}
    evidence = safe_result.get("evidence") if isinstance(safe_result.get("evidence"), dict) else {}
    lines = [
        "# Live HTTP question review smoke",
        "",
        f"- Created: `{safe_result.get('created_at', '')}`",
        f"- App: `{evidence.get('base_url', '')}`",
        f"- Result: `{'PASS' if safe_result.get('passed') else 'FAIL'}`",
        f"- Review history: `{evidence.get('review_history_count', 0)}` rows; active `{evidence.get('active_review_count', 0)}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] `{name}`" for name, passed in checks.items())
    lines.extend(
        [
            "",
            "## Operational evidence",
            "",
            f"- Exact retry reused review id: `{evidence.get('first_review_id') == evidence.get('retry_review_id')}`",
            f"- Exact rollback retry reused event id: `{evidence.get('rollback_event_id') == evidence.get('rollback_retry_event_id')}`",
            f"- Changed payload created a new review id: `{evidence.get('changed_review_id') != evidence.get('first_review_id')}`",
            f"- Review after rollback created a new review id: `{evidence.get('post_rollback_review_id') not in (None, evidence.get('first_review_id'))}`",
            f"- Second rollback restored the original predecessor: `{evidence.get('second_rollback_restored_review_id') == evidence.get('first_review_id')}`",
            f"- Regeneration changed the question: `{evidence.get('first_question') != evidence.get('second_question')}`",
            f"- First run: `{evidence.get('first_run_id', '')}`",
            f"- Second run: `{evidence.get('second_run_id', '')}`",
            f"- Runtime policy: `{evidence.get('first_runtime_policy', '')}` / `{evidence.get('second_runtime_policy', '')}`",
        ]
    )
    if safe_result.get("failed_checks") or safe_result.get("failures"):
        lines.extend(
            [
                "",
                "## Failures",
                "",
                f"- Failed checks: `{', '.join(safe_result.get('failed_checks') or []) or 'none'}`",
                f"- HTTP failures: `{len(safe_result.get('failures') or [])}`",
            ]
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise the live generate-review-retry-rollback-regenerate HTTP flow.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--report-dir", default="reports/question_quality_simulation")
    args = parser.parse_args()
    try:
        result = run_live_smoke(args.base_url, timeout=args.timeout)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        result = {
            "schema_version": "question_review_live_http_smoke_v1",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "passed": False,
            "checks": {},
            "failed_checks": ["unhandled_transport_error"],
            "failures": [{"operation": "live_http_smoke", "error": f"{type(exc).__name__}: {exc}"}],
            "evidence": {"base_url": args.base_url},
        }
    json_path, markdown_path = write_report(result, Path(args.report_dir))
    print(json.dumps({"passed": result.get("passed"), "json": str(json_path), "markdown": str(markdown_path), "failed_checks": result.get("failed_checks")}, ensure_ascii=False))
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
