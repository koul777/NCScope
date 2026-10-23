from __future__ import annotations

import json
import sqlite3

import pytest

from app.services import alio_interview_orchestrator as orchestrator
from scripts import run_alio_interview_orchestrator as cli


def inert_generator(_bundle):
    raise AssertionError("the retry policy test must not call a provider")


def _retry_run(
    error: str,
    *,
    attempts: int = 1,
    updated_at: str = "2026-01-01T00:00:00+00:00",
    failure_stage: str = "generation",
) -> dict[str, object]:
    return {
        "state": "failed",
        "failure_stage": failure_stage,
        "last_error": error,
        "attempt_count": attempts,
        "updated_at": updated_at,
    }


def _policy_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE interview_runs (
            id INTEGER PRIMARY KEY,
            posting_id TEXT NOT NULL,
            state TEXT NOT NULL,
            failure_stage TEXT NOT NULL,
            last_error TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            bundle_json TEXT NOT NULL,
            is_current INTEGER NOT NULL
        )
        """
    )
    return connection


def _insert_failure(
    connection: sqlite3.Connection,
    *,
    run_id: int,
    posting_id: str,
    error: str,
    attempts: int,
    updated_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO interview_runs(
            id,posting_id,state,failure_stage,last_error,attempt_count,
            updated_at,bundle_json,is_current
        ) VALUES(?,?,'failed','generation',?,?,?,?,1)
        """,
        (
            run_id,
            posting_id,
            error,
            attempts,
            updated_at,
            json.dumps(
                {"generation_profile": orchestrator._generation_profile()},
                sort_keys=True,
            ),
        ),
    )
    connection.commit()


def test_model_output_and_explicit_retryable_failures_share_three_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NCSCOPE_ALIO_RETRY_BASE_SEC", "30")
    now = "2026-01-01T00:00:30+00:00"

    model_output = _retry_run(
        "model_response_invalid_json|retryable=false|model=example/free"
    )
    deterministic_quality_rejection = _retry_run(
        "ncscope_openrouter_quality_gate_rejected|model=example/free|"
        "diagnostics=question_quality_report_failed"
    )
    transient = _retry_run(
        "openrouter_rate_limited|http_status=429|retryable=true"
    )
    exhausted = _retry_run(
        "openrouter_rate_limited|http_status=429|retryable=true",
        attempts=3,
    )
    shared_configuration = _retry_run(
        "openrouter_model_not_found|http_status=404|retryable=false"
    )

    assert orchestrator._unattended_retry_status(model_output, now) == "due"
    assert (
        orchestrator._unattended_retry_status(deterministic_quality_rejection, now)
        == "due"
    )
    assert orchestrator._unattended_retry_status(transient, now) == "due"
    assert orchestrator._unattended_retry_status(exhausted, now) == "exhausted"
    assert orchestrator._unattended_retry_status(shared_configuration, now) == "parked"
    assert orchestrator._retry_delay_seconds(exhausted) is None


def test_drive_never_calls_generator_after_generation_attempt_budget_is_exhausted() -> None:
    connection = sqlite3.connect(":memory:")
    orchestrator.ensure_orchestration_schema(connection)
    connection.execute(
        """
        INSERT INTO interview_runs(
            posting_id,document_id,match_id,scope_key,input_fingerprint,
            bundle_json,state,attempt_count,failure_stage,last_error,is_current,
            created_at,updated_at
        ) VALUES(
            'bounded',NULL,NULL,'document:0:match:0','fingerprint','{}',
            'failed',3,'generation',
            'openrouter_request_timeout|retryable=true',1,
            '2026-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00'
        )
        """
    )
    connection.commit()
    run_id = int(connection.execute("SELECT id FROM interview_runs").fetchone()[0])

    result = orchestrator._drive(
        connection,
        run_id,
        inert_generator,
        None,
        respect_retry_backoff=True,
    )

    assert result["state"] == "failed"
    assert result["attempt_count"] == 3
    assert orchestrator._unattended_retry_status(
        result,
        "2026-01-01T00:00:00+00:00",
    ) == "exhausted"


@pytest.mark.parametrize(
    ("state", "failure_stage"),
    [
        ("failed", "review"),
        ("awaiting_claude", "review_claimed:abandoned-worker"),
    ],
)
def test_drive_never_calls_reviewer_after_review_attempt_budget_is_exhausted(
    state: str,
    failure_stage: str,
) -> None:
    connection = sqlite3.connect(":memory:")
    orchestrator.ensure_orchestration_schema(connection)
    unit_code = "0202030101_23v1"
    bundle = {
        "generation_contract_version": orchestrator.INTERVIEW_GENERATION_CONTRACT_VERSION,
        "generation_profile": {"main_question_count": 1, "follow_up_count": 1},
        "ncs_match": {"official_detail_code": "02020301"},
        "verified_units": [
            {
                "official_unit_full_code": unit_code,
                "official_unit_base_code": "0202030101",
            }
        ],
    }
    output = {
        "schema_version": "alio-structured-interview-kit.v2",
        "quality_gate": {
            "passed": False,
            "status": "awaiting_claude_ncs_mcp_review",
            "generation_gate_passed": True,
            "independent_claude_ncs_mcp_review_required": True,
        },
        "ncs_detail": {"code": "02020301", "verified_unit_count": 1},
        "interview_questions": [
            {
                "question": "Q",
                "follow_ups": ["probe"],
                "structured_follow_ups": [
                    {
                        "trigger": "always",
                        "answer_slot": "slot_1",
                        "evidence_target": "evidence_1",
                        "prompt": "probe",
                        "ask_limit": 1,
                    }
                ],
                "evaluation_points": ["a", "b", "c", "d"],
                "assessment_guide": {
                    "dimension_rubrics": [
                        {"dimension": "a", "weight": 25},
                        {"dimension": "b", "weight": 25},
                        {"dimension": "c", "weight": 25},
                        {"dimension": "d", "weight": 25},
                    ]
                },
                "ncsClCd": unit_code,
                "ncs_traceability": {"ncs_code": unit_code},
                "deterministic_ready": True,
                "release_ready": False,
                "ready": False,
                "review_required": True,
                "question_artifact_hash": "a" * 64,
            }
        ],
    }
    output_hash = orchestrator._hash(output)
    connection.execute(
        """
        INSERT INTO interview_runs(
            posting_id,document_id,match_id,scope_key,input_fingerprint,
            bundle_json,state,attempt_count,review_attempt_count,failure_stage,
            last_error,generation_output_json,generation_output_hash,is_current,
            created_at,updated_at
        ) VALUES(
            'bounded-review',NULL,NULL,'document:0:match:0',?,?,?,1,3,
            ?,'claude_review_timeout|retryable=true',?,?,1,
            '2026-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00'
        )
        """,
        (
            "b" * 64,
            json.dumps(bundle, ensure_ascii=False, sort_keys=True),
            state,
            failure_stage,
            json.dumps(output, ensure_ascii=False, sort_keys=True),
            output_hash,
        ),
    )
    connection.commit()
    run_id = int(connection.execute("SELECT id FROM interview_runs").fetchone()[0])

    def forbidden_reviewer(_request):
        raise AssertionError("review attempt budget must stop provider calls")

    result = orchestrator._drive(
        connection,
        run_id,
        None,
        forbidden_reviewer,
        respect_retry_backoff=True,
    )

    assert result["state"] == state
    assert result["review_attempt_count"] == 3
    assert orchestrator._unattended_retry_status(
        result,
        "2026-01-01T00:00:00+00:00",
    ) == "exhausted"


def test_shared_preflight_is_scoped_and_prioritizes_only_due_requested_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _policy_database()
    monkeypatch.setenv("NCSCOPE_ALIO_RETRY_BASE_SEC", "30")
    monkeypatch.setattr(
        cli.orchestrator,
        "_utc_now",
        lambda: "2026-01-01T00:01:00+00:00",
    )
    rate_limit = "openrouter_rate_limited|http_status=429|retryable=true"
    _insert_failure(
        connection,
        run_id=1,
        posting_id="outside-scope",
        error=rate_limit,
        attempts=1,
        updated_at="2026-01-01T00:00:59+00:00",
    )
    _insert_failure(
        connection,
        run_id=2,
        posting_id="parked",
        error="openrouter_model_not_found|http_status=404|retryable=false",
        attempts=1,
        updated_at="2026-01-01T00:00:59+00:00",
    )
    _insert_failure(
        connection,
        run_id=3,
        posting_id="exhausted",
        error=rate_limit,
        attempts=3,
        updated_at="2026-01-01T00:00:59+00:00",
    )
    _insert_failure(
        connection,
        run_id=4,
        posting_id="due",
        error=rate_limit,
        attempts=1,
        updated_at="2026-01-01T00:00:00+00:00",
    )

    ordered, breaker = cli._shared_failure_preflight(
        connection,
        ["parked", "due", "exhausted"],
    )

    assert breaker == ""
    assert ordered == ["due", "parked", "exhausted"]
    assert "outside-scope" not in ordered


def test_requested_429_cooldown_blocks_without_expanding_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _policy_database()
    monkeypatch.setenv("NCSCOPE_ALIO_RETRY_BASE_SEC", "30")
    monkeypatch.setattr(
        cli.orchestrator,
        "_utc_now",
        lambda: "2026-01-01T00:00:29+00:00",
    )
    _insert_failure(
        connection,
        run_id=1,
        posting_id="requested",
        error="openrouter_rate_limited|http_status=429|retryable=true",
        attempts=1,
        updated_at="2026-01-01T00:00:00+00:00",
    )

    ordered, breaker = cli._shared_failure_preflight(
        connection,
        ["requested", "next"],
    )

    assert ordered == ["requested", "next"]
    assert breaker == "openrouter_rate_limited"


def test_old_parked_failure_does_not_reopen_breaker_but_new_failure_stops_once(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "policy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE interview_runs (
            id INTEGER PRIMARY KEY,
            posting_id TEXT NOT NULL,
            state TEXT NOT NULL,
            failure_stage TEXT NOT NULL,
            last_error TEXT NOT NULL,
            attempt_count INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            bundle_json TEXT NOT NULL,
            is_current INTEGER NOT NULL
        )
        """
    )
    parked_error = "openrouter_model_not_found|http_status=404|retryable=false"
    _insert_failure(
        connection,
        run_id=1,
        posting_id="parked",
        error=parked_error,
        attempts=1,
        updated_at="2026-01-01T00:00:00+00:00",
    )
    connection.close()
    driven: list[str] = []
    monkeypatch.setattr(cli.orchestrator, "prepare_posting", lambda *_args: [])

    def fake_drive(_connection, posting_id, **kwargs):
        driven.append(posting_id)
        if posting_id == "parked":
            result = {
                "id": 1,
                "posting_id": posting_id,
                "state": "failed",
                "failure_stage": "generation",
                "last_error": parked_error,
                "attempt_count": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "bundle": {},
            }
        else:
            result = {
                "id": 2,
                "posting_id": posting_id,
                "state": "failed",
                "failure_stage": "generation",
                "last_error": "openrouter_rate_limited|http_status=429|retryable=true",
                "attempt_count": 1,
                "updated_at": "2026-01-01T00:01:00+00:00",
                "bundle": {},
            }
        predicate = kwargs.get("stop_predicate")
        if predicate is not None:
            predicate(result)
        return [result]

    monkeypatch.setattr(cli.orchestrator, "drive_posting", fake_drive)

    exit_code = cli.main(
        [
            "run-once",
            "--database",
            str(database),
            "--posting-id",
            "parked",
            "--posting-id",
            "fresh",
            "--posting-id",
            "must-not-run",
            "--generator",
            f"{__name__}:inert_generator",
            "--respect-retry-backoff",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert driven == ["parked", "fresh"]
    assert output["processed_posting_count"] == 2
    assert output["circuit_breaker"] == "openrouter_rate_limited"

