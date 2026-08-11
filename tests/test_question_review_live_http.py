from __future__ import annotations

import json

from scripts.simulate_question_review_live_http import write_report


def _result() -> dict:
    return {
        "created_at": "2026-08-11T03:47:05+09:00",
        "passed": True,
        "checks": {"exact_retry_idempotent": True},
        "failed_checks": [],
        "failures": [],
        "evidence": {
            "base_url": "http://127.0.0.1:8015",
            "review_history_count": 3,
            "active_review_count": 1,
            "first_review_id": 11,
            "retry_review_id": 11,
            "changed_review_id": 12,
            "rollback_event_id": 13,
            "rollback_retry_event_id": 13,
            "first_question": "첫 문항",
            "second_question": "다른 문항",
        },
    }


def test_immediate_live_http_report_retries_never_overwrite_prior_evidence(tmp_path) -> None:
    first_json, first_markdown = write_report(_result(), tmp_path)
    second_json, second_markdown = write_report(_result(), tmp_path)

    assert first_json != second_json
    assert first_markdown != second_markdown
    assert first_json.exists() and second_json.exists()
    assert first_markdown.exists() and second_markdown.exists()
    assert json.loads(first_json.read_text(encoding="utf-8"))["passed"] is True
    assert json.loads(second_json.read_text(encoding="utf-8"))["passed"] is True


def test_live_http_report_does_not_persist_review_token(tmp_path) -> None:
    result = _result()
    result["evidence"]["review_token"] = "should-never-be-added-by-runner"

    json_path, markdown_path = write_report(result, tmp_path)

    # Defense in depth: the live runner currently keeps tokens out of evidence,
    # and the writer also redacts any future accidental inclusion.
    assert "should-never-be-added-by-runner" not in markdown_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["evidence"]["review_token"] == "[REDACTED]"
