"""Persistent orchestration for ALIO interview generation.

This module deliberately consumes only the relational, audited NCS matches
written by ``scripts/collect_alio_documents.py``.  It never tries to infer an
NCS classification itself.  A generation callback is reached only after the
collector has persisted a complete official-detail unit set.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from app.services.ncs_match_gate import NCS_MATCH_GATE_VERSION
from app.services.openai_quality_config import (
    openai_role_model,
    quality_reasoning_effort,
)


RUN_STATES = frozenset(
    {
        "waiting_documents",
        "waiting_ncs_mcp",
        "needs_human_ncs_review",
        "queued",
        "generating",
        "awaiting_claude",
        "ready_automated",
        "revise",
        "needs_human_quality_review",
        "failed",
    }
)
TERMINAL_QUALITY_STATES = frozenset(
    {"ready_automated", "revise", "needs_human_quality_review"}
)
UPSTREAM_WAIT_STATES = frozenset(
    {"waiting_documents", "waiting_ncs_mcp", "needs_human_ncs_review"}
)

GENERATOR = Callable[[dict[str, Any]], Any]
REVIEWER = Callable[[dict[str, Any]], Any]
STOP_PREDICATE = Callable[[dict[str, Any]], bool]
CLAIM_LEASE_MINUTES = 15
RETRY_BACKOFF_BASE_SECONDS = 300
RETRY_BACKOFF_MAX_SECONDS = 3600
UNATTENDED_MAX_GENERATION_ATTEMPTS = 3
UNATTENDED_MAX_REVIEW_ATTEMPTS = 3
INTERVIEW_GENERATION_CONTRACT_VERSION = "alio-interview-generation-contract.v13"
REQUIRED_CLAUDE_MCP_REVIEWER = "claude_code_sonnet_ncs_mcp"
_CLAUDE_REVIEW_KEYS = frozenset(
    {
        "input_fingerprint",
        "generation_output_hash",
        "review_input_hash",
        "verdict",
        "reviewer",
        "mcp_verification",
        "field_readiness",
        "issues",
    }
)
_CLAUDE_MCP_VERIFICATION_KEYS = frozenset(
    {
        "enabled",
        "status",
        "detail_code",
        "checked_unit_codes",
        "checked_ksa_codes",
        "issues",
    }
)
_CLAUDE_FIELD_READINESS_KEYS = frozenset(
    {
        "real_interview_usable",
        "question_count",
        "all_have_three_followups",
        "all_have_evaluation_points",
        "all_have_rating_anchors",
        "all_trace_to_verified_ncs",
        "blocking_issue_count",
        "summary",
    }
)
_CLAUDE_ISSUE_KEYS = frozenset(
    {
        "severity",
        "category",
        "question_index",
        "finding",
        "evidence",
        "recommendation",
    }
)
_CLAUDE_ISSUE_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
_CLAUDE_ISSUE_CATEGORIES = frozenset(
    {
        "ncs_grounding",
        "job_specificity",
        "follow_up_flow",
        "scoring",
        "fairness",
        "traceability",
        "field_operability",
    }
)
_RETRYABLE_MODEL_OUTPUT_FAILURE_CODES = frozenset(
    {
        "institution_api_empty_generation",
        "model_question_content_missing",
        "model_question_count_mismatch",
        "model_question_diversity_mismatch",
        "model_response_content_filtered",
        "model_response_invalid_json",
        "model_response_invalid_shape",
        "model_response_not_object",
        "model_response_refused",
        "model_response_truncated",
        "ncscope_openrouter_quality_gate_rejected",
        "question_set_count_or_diversity_failed",
    }
)
_BUNDLE_EXCLUDED_DOCUMENT_FIELDS = frozenset(
    {
        # Coordinate/layout evidence is valuable in the normalized document
        # record, but can exceed a megabyte and is not an interview-generation
        # input. Persisting it once per NCS match caused avoidable DB growth.
        "positioned_items",
        "table_coordinate_contract",
        "table_scopes",
        "ncs_detail_candidate_evidence",
        "ncs_detail_absence_evidence",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _ncs_match_source_signature(
    *,
    document_id: int,
    source_detail_index: int,
    source_detail: str,
    ncs_detail_source: str,
    source_document_sha256: str,
    source_fields_sha256: str,
    matcher_version: str,
) -> str:
    """Rebuild the collector's version-bound currentness signature."""

    payload = json.dumps(
        {
            "document_id": document_id,
            "source_detail_index": source_detail_index,
            "source_detail": source_detail,
            "ncs_detail_source": ncs_detail_source,
            "source_document_sha256": source_document_sha256,
            "source_fields_sha256": source_fields_sha256,
            "matcher_version": matcher_version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decode_object(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _decode_list(value: Any) -> list[Any]:
    try:
        decoded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _lease_cutoff(now: str) -> str:
    try:
        instant = datetime.fromisoformat(now)
    except ValueError:
        instant = datetime.now(timezone.utc)
    return (instant - timedelta(minutes=CLAIM_LEASE_MINUTES)).replace(
        microsecond=0
    ).isoformat()


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _generation_profile() -> dict[str, Any]:
    """Bind non-secret execution settings so configuration changes supersede runs."""

    provider = str(os.getenv("INTERVIEW_GENERATION_PROVIDER") or "").strip().casefold()
    is_openrouter = provider in {
        "openrouter",
        "open_router",
        "open-router",
        "openrouter_api",
    }
    is_openai = provider in {"openai", "openai_api"}
    model = (
        str(os.getenv("OPENROUTER_DEFAULT_MODEL") or "").strip()
        if is_openrouter
        else openai_role_model("question_authoring")
        if is_openai
        else ""
    )
    quality_review_model = (
        model
        if is_openrouter
        else openai_role_model("quality_review")
        if is_openai
        else "provider_default"
    )
    if len(provider) > 60 or re.fullmatch(r"[a-z0-9_-]*", provider) is None:
        provider = "invalid"
    if len(model) > 200 or any(ord(character) < 33 or ord(character) > 126 for character in model):
        model = "invalid"
    reasoning_effort = (
        str(os.getenv("OPENROUTER_PRIMARY_REASONING_EFFORT") or "max")
        .strip()
        .casefold()
        if is_openrouter
        else quality_reasoning_effort(
            model,
            specific_env_name="OPENAI_STRATEGY_REASONING_EFFORT",
        )
        if is_openai
        else "provider_default"
    )
    if reasoning_effort not in {
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "provider_default",
    }:
        reasoning_effort = "invalid"
    quality_review_reasoning_effort = (
        str(
            os.getenv("OPENROUTER_QUALITY_RETRY_REASONING_EFFORT")
            or os.getenv("OPENROUTER_QUALITY_REVIEW_REASONING_EFFORT")
            or os.getenv("OPENROUTER_INVALID_OUTPUT_RETRY_REASONING_EFFORT")
            or os.getenv("OPENROUTER_FALLBACK_REASONING_EFFORT")
            or "high"
        )
        .strip()
        .casefold()
        if is_openrouter
        else quality_reasoning_effort(
            quality_review_model,
            specific_env_name="OPENAI_QUALITY_REVIEW_REASONING_EFFORT",
        )
        if is_openai
        else "provider_default"
    )
    if quality_review_reasoning_effort not in {
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "provider_default",
    }:
        quality_review_reasoning_effort = "invalid"
    execution_epoch = str(os.getenv("NCSCOPE_ALIO_GENERATION_EPOCH") or "1").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", execution_epoch) is None:
        execution_epoch = "invalid"
    return {
        "provider": provider or "adapter_default",
        "model": model or "provider_default",
        "reasoning_effort": reasoning_effort,
        "quality_review_model": quality_review_model,
        "quality_review_reasoning_effort": quality_review_reasoning_effort,
        "execution_epoch": execution_epoch,
        "main_question_count": _bounded_env_int(
            "NCSCOPE_ALIO_MAIN_QUESTION_COUNT", 5, minimum=1, maximum=20
        ),
        "follow_up_count": _bounded_env_int(
            "NCSCOPE_ALIO_FOLLOW_UP_COUNT", 3, minimum=1, maximum=5
        ),
    }


def _expected_generation_counts(bundle: dict[str, Any]) -> tuple[int, int]:
    """Return the exact question/probe counts bound into the run fingerprint."""

    profile = bundle.get("generation_profile")
    if (
        bundle.get("generation_contract_version")
        != INTERVIEW_GENERATION_CONTRACT_VERSION
        or not isinstance(profile, dict)
    ):
        raise ValueError("structured_interview_generation_contract_invalid")
    main_question_count = _safe_int(profile.get("main_question_count"))
    follow_up_count = _safe_int(profile.get("follow_up_count"))
    if not 1 <= main_question_count <= 20 or not 1 <= follow_up_count <= 5:
        raise ValueError("structured_interview_generation_contract_invalid")
    return main_question_count, follow_up_count


def _safe_callback_error(exc: Exception, fallback: str) -> str:
    """Persist only a bounded code and allowlisted fields from callback errors."""

    raw = str(exc or "").strip()
    parts = raw.split("|") if raw else []
    if not parts or re.fullmatch(r"[a-z0-9_]{1,120}", parts[0]) is None:
        return fallback
    sanitized = [parts[0]]
    seen_fields: set[str] = set()
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        if not separator or key in seen_fields:
            return fallback
        if key == "retryable" and value in {"true", "false"}:
            pass
        elif key == "http_status" and re.fullmatch(r"[1-5][0-9]{2}", value):
            pass
        elif key == "model" and re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", value):
            pass
        elif key == "diagnostics" and re.fullmatch(
            r"[a-z0-9_]{1,120}(?:,[a-z0-9_]{1,120}){0,19}", value
        ):
            pass
        else:
            return fallback
        seen_fields.add(key)
        sanitized.append(f"{key}={value}")
    return "|".join(sanitized)


def _retry_delay_seconds(run: dict[str, Any]) -> int | None:
    """Return the unattended retry delay for a failed callback run.

    Explicitly retryable failures and the bounded allowlist of response-shape
    failures may be retried by a continuous worker. Generation retries stop
    after three total provider calls. Manual ``run-once`` calls keep their
    operator-controlled behavior unless the caller opts into this policy.
    """

    if str(run.get("state") or "") != "failed":
        return 0
    error = str(run.get("last_error") or "").casefold()
    failure_stage = str(run.get("failure_stage") or "generation")
    attempt_count = (
        _safe_int(run.get("review_attempt_count"))
        if failure_stage == "review"
        else _safe_int(run.get("attempt_count"))
    )
    attempt_limit = (
        UNATTENDED_MAX_REVIEW_ATTEMPTS
        if failure_stage == "review"
        else UNATTENDED_MAX_GENERATION_ATTEMPTS
    )
    if attempt_count >= attempt_limit:
        return None
    error_code = error.split("|", 1)[0].strip()
    is_retryable_model_output = (
        failure_stage == "generation"
        and error_code in _RETRYABLE_MODEL_OUTPUT_FAILURE_CODES
    )
    if "retryable=true" not in error and not is_retryable_model_output:
        return None
    base = _bounded_env_int(
        "NCSCOPE_ALIO_RETRY_BASE_SEC",
        RETRY_BACKOFF_BASE_SECONDS,
        minimum=30,
        maximum=3600,
    )
    maximum = _bounded_env_int(
        "NCSCOPE_ALIO_RETRY_MAX_SEC",
        RETRY_BACKOFF_MAX_SECONDS,
        minimum=base,
        maximum=86400,
    )
    exponent = max(0, min(8, max(1, attempt_count) - 1))
    return min(maximum, base * (2**exponent))


def _unattended_retry_due(run: dict[str, Any], now: str) -> bool:
    delay = _retry_delay_seconds(run)
    if delay == 0:
        return True
    if delay is None:
        return False
    try:
        updated = datetime.fromisoformat(str(run.get("updated_at") or ""))
        instant = datetime.fromisoformat(now)
    except ValueError:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant >= updated + timedelta(seconds=delay)


def _unattended_retry_status(run: dict[str, Any], now: str) -> str:
    """Classify a run without mutating it or consuming provider budget.

    ``parked`` means the current input/profile cannot be retried unattended.
    A changed profile or another semantic input produces a new fingerprint
    and therefore a fresh run. ``exhausted`` is the equivalent terminal state
    after the bounded three generation attempts have been consumed.
    """

    state = str(run.get("state") or "")
    failure_stage = str(run.get("failure_stage") or "generation")
    stale_review_claim = state == "awaiting_claude" and failure_stage.startswith(
        "review_claimed:"
    )
    if stale_review_claim:
        if _safe_int(run.get("review_attempt_count")) >= UNATTENDED_MAX_REVIEW_ATTEMPTS:
            return "exhausted"
        try:
            updated = datetime.fromisoformat(str(run.get("updated_at") or ""))
            instant = datetime.fromisoformat(now)
        except ValueError:
            return "cooldown"
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return (
            "due"
            if instant >= updated + timedelta(minutes=CLAIM_LEASE_MINUTES)
            else "cooldown"
        )
    if state != "failed":
        return "ready"
    exhausted = (
        _safe_int(run.get("review_attempt_count")) >= UNATTENDED_MAX_REVIEW_ATTEMPTS
        if failure_stage == "review"
        else _safe_int(run.get("attempt_count"))
        >= UNATTENDED_MAX_GENERATION_ATTEMPTS
    )
    if exhausted:
        return "exhausted"
    delay = _retry_delay_seconds(run)
    if delay is None:
        return "parked"
    return "due" if _unattended_retry_due(run, now) else "cooldown"


def _row(cursor: sqlite3.Cursor, raw: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    return {str(column[0]): raw[index] for index, column in enumerate(cursor.description or ())}


def _fetchone(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Iterable[Any] = (),
) -> dict[str, Any] | None:
    cursor = connection.execute(sql, tuple(parameters))
    return _row(cursor, cursor.fetchone())


def _fetchall(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, tuple(parameters))
    return [_row(cursor, raw) or {} for raw in cursor.fetchall()]


def ensure_orchestration_schema(connection: sqlite3.Connection) -> None:
    """Idempotently install the orchestration/audit tables in a collector DB."""

    allowed_states = ",".join(f"'{state}'" for state in sorted(RUN_STATES))
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS interview_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posting_id TEXT NOT NULL,
            document_id INTEGER,
            match_id INTEGER,
            scope_key TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            bundle_json TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ({allowed_states})),
            generation_output_json TEXT NOT NULL DEFAULT '',
            generation_output_hash TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            review_attempt_count INTEGER NOT NULL DEFAULT 0,
            failure_stage TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            generation_started_at TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT NOT NULL DEFAULT '',
            superseded_at TEXT NOT NULL DEFAULT '',
            UNIQUE(posting_id, scope_key, input_fingerprint)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_interview_runs_current_scope
            ON interview_runs(posting_id, scope_key) WHERE is_current=1;
        CREATE INDEX IF NOT EXISTS idx_interview_runs_work_queue
            ON interview_runs(is_current, state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_interview_runs_match
            ON interview_runs(match_id, is_current);

        CREATE TABLE IF NOT EXISTS generation_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES interview_runs(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            input_fingerprint TEXT NOT NULL,
            claim_token TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('generating','failed','succeeded')),
            last_error TEXT NOT NULL DEFAULT '',
            generation_output_hash TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT '',
            UNIQUE(run_id, attempt_number)
        );
        CREATE INDEX IF NOT EXISTS idx_generation_attempts_run
            ON generation_attempts(run_id, attempt_number);

        CREATE TABLE IF NOT EXISTS claude_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES interview_runs(id) ON DELETE CASCADE,
            input_fingerprint TEXT NOT NULL,
            generation_output_hash TEXT NOT NULL,
            review_input_hash TEXT NOT NULL,
            verdict TEXT NOT NULL,
            review_json TEXT NOT NULL,
            reviewer_name TEXT NOT NULL DEFAULT 'claude',
            created_at TEXT NOT NULL,
            UNIQUE(run_id, review_input_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_claude_reviews_binding
            ON claude_reviews(input_fingerprint, generation_output_hash);
        """
    )
    interview_run_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(interview_runs)").fetchall()
    }
    if "review_attempt_count" not in interview_run_columns:
        connection.execute(
            "ALTER TABLE interview_runs ADD COLUMN "
            "review_attempt_count INTEGER NOT NULL DEFAULT 0"
        )
    connection.commit()


def _posting(connection: sqlite3.Connection, posting_id: str) -> dict[str, Any]:
    row = _fetchone(
        connection,
        """
        SELECT posting_id,url,organization,title,detail_fields_json,
               inline_notice_json,status,error
        FROM postings WHERE posting_id=?
        """,
        (posting_id,),
    )
    if row is None:
        raise LookupError(f"ALIO posting not found: {posting_id}")
    return {
        "posting_id": str(row["posting_id"]),
        "url": str(row["url"] or ""),
        "organization": str(row["organization"] or ""),
        "title": str(row["title"] or ""),
        "detail_fields": _decode_object(row["detail_fields_json"]),
        "inline_notice": _decode_object(row["inline_notice_json"]),
        "collector_status": str(row["status"] or ""),
        "collector_error": str(row["error"] or ""),
    }


def _documents(
    connection: sqlite3.Connection,
    posting_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    rows = _fetchall(
        connection,
        """
        SELECT id,file_no,url,kind,section,filename,sha256,markdown,
               fields_json,status,error,parser,parser_version
        FROM documents
        WHERE posting_id=? AND kind=?
        ORDER BY id
        """,
        (posting_id, kind),
    )
    for row in rows:
        row["id"] = int(row["id"])
        fields_json = str(row.pop("fields_json", "{}") or "{}")
        row["fields"] = _decode_object(fields_json)
        row["_fields_sha256"] = hashlib.sha256(
            fields_json.encode("utf-8")
        ).hexdigest()
    return rows


def _public_document(row: dict[str, Any]) -> dict[str, Any]:
    document = {
        key: row.get(key)
        for key in (
            "id",
            "file_no",
            "url",
            "kind",
            "section",
            "filename",
            "sha256",
            "markdown",
            "fields",
            "status",
            "error",
            "parser",
            "parser_version",
        )
    }
    fields = document.get("fields")
    if isinstance(fields, dict):
        document["fields"] = {
            key: value
            for key, value in fields.items()
            if key not in _BUNDLE_EXCLUDED_DOCUMENT_FIELDS
        }
    return document


def _matches(connection: sqlite3.Connection, document_id: int) -> list[dict[str, Any]]:
    return _fetchall(
        connection,
        """
        SELECT id,document_id,source_detail_index,source_detail,
               ncs_detail_source,source_signature,source_document_sha256,
               source_fields_sha256,matcher_version,status,last_success_status,
               match_method,official_detail_code,official_detail_name,
               expected_unit_count,verified_unit_count,persisted_unit_count,
               retrieval_complete,retrieval_cap_limited,retryable,error,
               attempted_at,matched_at
        FROM document_ncs_matches
        WHERE document_id=?
        ORDER BY source_detail_index,id
        """,
        (document_id,),
    )


def _units(connection: sqlite3.Connection, match_id: int) -> list[dict[str, Any]]:
    return _fetchall(
        connection,
        """
        SELECT id,official_unit_base_code,official_unit_full_code,
               official_unit_name,official_detail_code,official_detail_name,
               ownership_detail_code,ownership_detail_name,catalog_source,
               catalog_resolution_kind,catalog_unit_codes_json,
               unit_catalog_verified,unit_version_compatible,
               detail_path_code_verified,retrieval_kind,retrieval_query,
               mcp_source,mcp_unit_name,verified_at
        FROM document_ncs_units
        WHERE match_id=?
        ORDER BY official_unit_base_code,id
        """,
        (match_id,),
    )


def _eligibility(
    match: dict[str, Any],
    units: list[dict[str, Any]],
    document: dict[str, Any],
) -> tuple[bool, str, str]:
    """Return ``eligible, state, reason`` without making a loose match."""

    status = str(match.get("status") or "")
    if str(match.get("ncs_detail_source") or "") != "explicit":
        return False, "needs_human_ncs_review", "ncs_source_is_not_explicit"
    if status != "exact_complete":
        if status == "exact":
            return False, "waiting_ncs_mcp", "legacy_exact_requires_exact_complete_rematch"
        if status == "mcp_unavailable" or _safe_int(match.get("retryable")) == 1:
            return False, "waiting_ncs_mcp", "ncs_mcp_match_not_available"
        return False, "needs_human_ncs_review", f"ncs_match_status_{status or 'missing'}"

    if (
        str(match.get("source_document_sha256") or "")
        != str(document.get("sha256") or "")
        or str(match.get("source_fields_sha256") or "")
        != str(document.get("_fields_sha256") or "")
    ):
        return False, "waiting_ncs_mcp", "ncs_match_stale_for_document"

    matcher_version = str(match.get("matcher_version") or "").strip()
    if matcher_version != NCS_MATCH_GATE_VERSION:
        return False, "waiting_ncs_mcp", "ncs_match_stale_for_matcher_version"
    expected_signature = _ncs_match_source_signature(
        document_id=_safe_int(match.get("document_id")),
        source_detail_index=_safe_int(match.get("source_detail_index")),
        source_detail=str(match.get("source_detail") or ""),
        ncs_detail_source=str(match.get("ncs_detail_source") or ""),
        source_document_sha256=str(match.get("source_document_sha256") or ""),
        source_fields_sha256=str(match.get("source_fields_sha256") or ""),
        matcher_version=matcher_version,
    )
    if str(match.get("source_signature") or "") != expected_signature:
        return False, "waiting_ncs_mcp", "ncs_match_source_signature_stale"

    expected = _safe_int(match.get("expected_unit_count"), -1)
    verified = _safe_int(match.get("verified_unit_count"), -1)
    persisted = _safe_int(match.get("persisted_unit_count"), -1)
    complete = _safe_int(match.get("retrieval_complete"), -1)
    capped = _safe_int(match.get("retrieval_cap_limited"), -1)
    if min(expected, verified, persisted, complete, capped) < 0:
        return False, "needs_human_ncs_review", "invalid_ncs_coverage_counts"

    if complete != 1 or capped != 0:
        return False, "waiting_ncs_mcp", "ncs_unit_retrieval_incomplete_or_capped"
    if expected <= 0 or expected != verified or expected != persisted:
        return False, "needs_human_ncs_review", "ncs_coverage_count_mismatch"
    if len(units) != expected:
        return False, "needs_human_ncs_review", "persisted_unit_row_count_mismatch"

    detail_code = str(match.get("official_detail_code") or "")
    detail_name = str(match.get("official_detail_name") or "")
    if re.fullmatch(r"\d{8}", detail_code) is None or not detail_name.strip():
        return False, "needs_human_ncs_review", "invalid_official_detail_identity"
    base_codes: set[str] = set()
    verified_catalog_count = 0
    for unit in units:
        base_code = str(unit.get("official_unit_base_code") or "")
        full_code = str(unit.get("official_unit_full_code") or "")
        catalog_codes_raw = _decode_list(unit.get("catalog_unit_codes_json"))
        catalog_codes = {
            str(value).strip()
            for value in catalog_codes_raw
            if isinstance(value, str) and str(value).strip()
        } if isinstance(catalog_codes_raw, list) else set()
        if _safe_int(unit.get("unit_catalog_verified")) == 1:
            verified_catalog_count += 1
        if (
            re.fullmatch(r"\d{10}", base_code) is None
            or re.fullmatch(r"\d{10}(?:_[0-9A-Za-z]+)?", full_code) is None
            or not base_code.startswith(detail_code)
            or full_code.split("_", 1)[0] != base_code
            or str(unit.get("official_detail_code") or "") != detail_code
            or str(unit.get("official_detail_name") or "") != detail_name
            or str(unit.get("ownership_detail_code") or "") != detail_code
            or str(unit.get("ownership_detail_name") or "") != detail_name
            or not str(unit.get("official_unit_name") or "").strip()
            or str(unit.get("catalog_source") or "") != "bundled_ncs_unit_catalog"
            or str(unit.get("catalog_resolution_kind") or "")
            != "catalog_full_code_exact"
            or full_code not in catalog_codes
            or _safe_int(unit.get("detail_path_code_verified")) != 1
            or base_code in base_codes
        ):
            return False, "needs_human_ncs_review", "invalid_verified_unit_identity"
        base_codes.add(base_code)
    if verified_catalog_count != expected:
        return False, "needs_human_ncs_review", "unit_catalog_verified_count_mismatch"
    return True, "queued", ""


def _bundle(
    *,
    posting: dict[str, Any],
    notices: list[dict[str, Any]],
    job_description: dict[str, Any] | None,
    match: dict[str, Any] | None,
    units: list[dict[str, Any]],
    orchestration_state: str,
    reason: str,
) -> dict[str, Any]:
    public_match = dict(match or {})
    if public_match:
        for key in (
            "id",
            "document_id",
            "source_detail_index",
            "expected_unit_count",
            "verified_unit_count",
            "persisted_unit_count",
            "retrieval_complete",
            "retrieval_cap_limited",
            "retryable",
        ):
            if key in public_match:
                public_match[key] = _safe_int(public_match[key])
    public_units: list[dict[str, Any]] = []
    for original in units:
        unit = dict(original)
        unit["catalog_unit_codes"] = _decode_list(
            unit.pop("catalog_unit_codes_json", "[]")
        )
        for key in (
            "id",
            "unit_catalog_verified",
            "unit_version_compatible",
            "detail_path_code_verified",
        ):
            unit[key] = _safe_int(unit.get(key))
        public_units.append(unit)
    return {
        "schema_version": "alio-interview-generation-bundle.v1",
        "generation_contract_version": INTERVIEW_GENERATION_CONTRACT_VERSION,
        "generation_profile": _generation_profile(),
        "posting": posting,
        "notice_documents": [_public_document(row) for row in notices],
        "notice_markdown": "\n\n".join(
            f"## {row.get('filename') or '채용 공고문'}\n\n{str(row.get('markdown') or '').strip()}"
            for row in notices
        ),
        "job_description": _public_document(job_description) if job_description else None,
        "ncs_match": public_match or None,
        "verified_units": public_units,
        "orchestration": {"state": orchestration_state, "reason": reason},
    }


def _fingerprint(bundle: dict[str, Any]) -> str:
    """Hash semantic inputs while excluding database ids and audit timestamps."""

    posting = bundle.get("posting") if isinstance(bundle.get("posting"), dict) else {}

    def document_identity(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            key: value.get(key)
            for key in (
                "file_no",
                "url",
                "kind",
                "section",
                "filename",
                "sha256",
                "markdown",
                "fields",
            )
        }

    notices = [
        identity
        for item in bundle.get("notice_documents", [])
        if (identity := document_identity(item)) is not None
    ]
    notices.sort(
        key=lambda item: (
            str(item.get("sha256") or ""),
            str(item.get("file_no") or ""),
            str(item.get("url") or ""),
        )
    )
    match = bundle.get("ncs_match") if isinstance(bundle.get("ncs_match"), dict) else {}
    match_identity = {
        key: match.get(key)
        for key in (
            "source_detail_index",
            "source_detail",
            "ncs_detail_source",
            "source_signature",
            "source_document_sha256",
            "source_fields_sha256",
            "matcher_version",
            "status",
            "last_success_status",
            "match_method",
            "official_detail_code",
            "official_detail_name",
            "expected_unit_count",
            "verified_unit_count",
            "persisted_unit_count",
            "retrieval_complete",
            "retrieval_cap_limited",
        )
    } if match else None
    unit_keys = (
        "official_unit_base_code",
        "official_unit_full_code",
        "official_unit_name",
        "official_detail_code",
        "official_detail_name",
        "ownership_detail_code",
        "ownership_detail_name",
        "catalog_source",
        "catalog_resolution_kind",
        "catalog_unit_codes",
        "unit_catalog_verified",
        "unit_version_compatible",
        "detail_path_code_verified",
        "retrieval_kind",
        "retrieval_query",
        "mcp_source",
        "mcp_unit_name",
    )
    units = [
        {key: item.get(key) for key in unit_keys}
        for item in bundle.get("verified_units", [])
        if isinstance(item, dict)
    ]
    units.sort(
        key=lambda item: (
            str(item.get("official_unit_base_code") or ""),
            str(item.get("official_unit_full_code") or ""),
        )
    )
    semantic_input = {
        "schema_version": bundle.get("schema_version"),
        "generation_contract_version": bundle.get("generation_contract_version"),
        "generation_profile": bundle.get("generation_profile"),
        "posting": {
            key: posting.get(key)
            for key in ("posting_id", "url", "organization", "title", "detail_fields", "inline_notice")
        },
        "notice_documents": notices,
        "job_description": document_identity(bundle.get("job_description")),
        "ncs_match": match_identity,
        "verified_units": units,
        "orchestration": bundle.get("orchestration"),
    }
    return _hash(semantic_input)


def _scope_key(document_id: int | None, match_id: int | None) -> str:
    return f"document:{document_id or 0}:match:{match_id or 0}"


def _upsert_run(
    connection: sqlite3.Connection,
    *,
    posting_id: str,
    document_id: int | None,
    match_id: int | None,
    bundle: dict[str, Any],
    desired_state: str,
    reason: str,
    now: str,
) -> int:
    fingerprint = _fingerprint(bundle)
    scope = _scope_key(document_id, match_id)
    existing = _fetchone(
        connection,
        """
        SELECT id,state,failure_stage,last_error,updated_at FROM interview_runs
        WHERE posting_id=? AND scope_key=? AND input_fingerprint=?
        """,
        (posting_id, scope, fingerprint),
    )
    with connection:
        connection.execute(
            """
            UPDATE interview_runs
            SET is_current=0,superseded_at=?,updated_at=?
            WHERE posting_id=? AND scope_key=? AND is_current=1
              AND input_fingerprint<>?
            """,
            (now, now, posting_id, scope, fingerprint),
        )
        if existing is None:
            cursor = connection.execute(
                """
                INSERT INTO interview_runs(
                    posting_id,document_id,match_id,scope_key,input_fingerprint,
                    bundle_json,state,last_error,is_current,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,1,?,?)
                """,
                (
                    posting_id,
                    document_id,
                    match_id,
                    scope,
                    fingerprint,
                    _canonical_json(bundle),
                    desired_state,
                    reason,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

        run_id = int(existing["id"])
        current_state = str(existing["state"] or "")
        next_state = desired_state if current_state in UPSTREAM_WAIT_STATES else current_state
        preserves_runtime_state = current_state not in UPSTREAM_WAIT_STATES
        next_error = (
            reason
            if next_state in UPSTREAM_WAIT_STATES
            else str(existing.get("last_error") or "")
        )
        connection.execute(
            """
            UPDATE interview_runs
            SET document_id=?,match_id=?,bundle_json=?,state=?,last_error=?,
                is_current=1,superseded_at='',updated_at=CASE WHEN ? THEN updated_at ELSE ? END
            WHERE id=?
            """,
            (
                document_id,
                match_id,
                _canonical_json(bundle),
                next_state,
                next_error,
                int(preserves_runtime_state),
                now,
                run_id,
            ),
        )
        return run_id


def _run(connection: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = _fetchone(connection, "SELECT * FROM interview_runs WHERE id=?", (run_id,))
    if row is None:
        raise LookupError(f"interview run not found: {run_id}")
    row["id"] = int(row["id"])
    row["document_id"] = int(row["document_id"]) if row["document_id"] is not None else None
    row["match_id"] = int(row["match_id"]) if row["match_id"] is not None else None
    row["attempt_count"] = int(row["attempt_count"] or 0)
    row["review_attempt_count"] = int(row.get("review_attempt_count") or 0)
    row["is_current"] = int(row["is_current"] or 0)
    try:
        bundle = json.loads(str(row.pop("bundle_json")))
    except (TypeError, ValueError):
        bundle = None
    row["bundle"] = bundle if isinstance(bundle, dict) else {}
    row["bundle_json_valid"] = isinstance(bundle, dict)
    output_json = str(row.pop("generation_output_json") or "")
    try:
        output = json.loads(output_json) if output_json else None
        output_valid = not output_json or isinstance(output, dict)
    except (TypeError, ValueError):
        output = None
        output_valid = False
    row["generation_output"] = output
    row["generation_output_json_valid"] = output_valid
    return row


def _validate_generation_output(output: Any, bundle: dict[str, Any]) -> None:
    if not isinstance(output, dict):
        raise TypeError("structured_interview_generation_contract_invalid")
    expected_questions, expected_follow_ups = _expected_generation_counts(bundle)
    if str(output.get("schema_version") or "") != "alio-structured-interview-kit.v2":
        raise ValueError("structured_interview_generation_contract_invalid")
    questions = output.get("interview_questions")
    if not isinstance(questions, list) or len(questions) != expected_questions:
        raise ValueError("structured_interview_generation_contract_invalid")
    if any(
        not isinstance(item, dict)
        or not str(item.get("question") or "").strip()
        for item in questions
    ):
        raise ValueError("structured_interview_generation_contract_invalid")
    detail = output.get("ncs_detail")
    quality_gate = output.get("quality_gate")
    if (
        not isinstance(detail, dict)
        or not re.fullmatch(r"\d{8}", str(detail.get("code") or ""))
        or _safe_int(detail.get("verified_unit_count")) <= 0
        or not isinstance(quality_gate, dict)
        or quality_gate.get("passed") is not False
        or quality_gate.get("status") != "awaiting_claude_ncs_mcp_review"
        or quality_gate.get("generation_gate_passed") is not True
        or quality_gate.get("independent_claude_ncs_mcp_review_required") is not True
    ):
        raise ValueError("structured_interview_generation_contract_invalid")
    verified_codes = {
        str(value or "").strip()
        for unit in bundle.get("verified_units", [])
        if isinstance(unit, dict)
        for value in (
            unit.get("official_unit_full_code"),
            unit.get("official_unit_base_code"),
        )
        if str(value or "").strip()
    }
    for item in questions:
        follow_ups = item.get("follow_ups")
        structured = item.get("structured_follow_ups")
        evaluation_points = item.get("evaluation_points")
        guide = item.get("assessment_guide")
        rubrics = guide.get("dimension_rubrics") if isinstance(guide, dict) else None
        trace = item.get("ncs_traceability")
        trace_code = ""
        if isinstance(trace, dict):
            trace_code = str(
                item.get("ncsClCd")
                or item.get("ncs_code")
                or trace.get("ncs_code")
                or ""
            ).strip()
        if (
            not isinstance(follow_ups, list)
            or len(follow_ups) != expected_follow_ups
            or any(not str(prompt or "").strip() for prompt in follow_ups)
            or not isinstance(structured, list)
            or len(structured) != len(follow_ups)
            or any(
                not isinstance(probe, dict)
                or str(probe.get("prompt") or "").strip()
                != str(follow_ups[index] or "").strip()
                or not str(probe.get("trigger") or "").strip()
                or not str(probe.get("answer_slot") or "").strip()
                or not str(probe.get("evidence_target") or "").strip()
                or probe.get("ask_limit") != 1
                for index, probe in enumerate(structured)
            )
            or not isinstance(evaluation_points, list)
            or len(evaluation_points) != 4
            or any(not str(point or "").strip() for point in evaluation_points)
            or not isinstance(rubrics, list)
            or len(rubrics) != 4
            or any(not isinstance(row, dict) for row in rubrics)
            or sum(_safe_int(row.get("weight")) for row in rubrics if isinstance(row, dict)) != 100
            or not isinstance(trace, dict)
            or not trace_code
            or trace_code not in verified_codes
            or item.get("deterministic_ready") is not True
            or item.get("release_ready") is not False
            or item.get("ready") is not False
            or item.get("review_required") is not True
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                str(item.get("question_artifact_hash") or ""),
            )
        ):
            raise ValueError("structured_interview_generation_contract_invalid")


def _verified_claude_mcp_pass(response: dict[str, Any], run: dict[str, Any]) -> bool:
    """Require the independent reviewer and every bound MCP verification field."""

    if (
        set(response) != _CLAUDE_REVIEW_KEYS
        or response.get("verdict") != "pass"
        or response.get("reviewer") != REQUIRED_CLAUDE_MCP_REVIEWER
    ):
        return False
    verification = response.get("mcp_verification")
    readiness = response.get("field_readiness")
    issues = response.get("issues")
    output = run.get("generation_output")
    bundle = run.get("bundle")
    if (
        not isinstance(verification, dict)
        or set(verification) != _CLAUDE_MCP_VERIFICATION_KEYS
        or not isinstance(readiness, dict)
        or set(readiness) != _CLAUDE_FIELD_READINESS_KEYS
        or not isinstance(issues, list)
        or len(issues) > 50
        or not isinstance(output, dict)
        or not isinstance(bundle, dict)
    ):
        return False
    input_fingerprint = str(run.get("input_fingerprint") or "")
    generation_output_hash = str(run.get("generation_output_hash") or "")
    expected_review_hash = _hash(
        {
            "input_fingerprint": input_fingerprint,
            "generation_output_hash": generation_output_hash,
        }
    )
    if (
        not re.fullmatch(r"[0-9a-f]{64}", input_fingerprint)
        or not re.fullmatch(r"[0-9a-f]{64}", generation_output_hash)
        or _hash(output) != generation_output_hash
        or response.get("input_fingerprint") != input_fingerprint
        or response.get("generation_output_hash") != generation_output_hash
        or response.get("review_input_hash") != expected_review_hash
    ):
        return False
    expected_detail = str((bundle.get("ncs_match") or {}).get("official_detail_code") or "")
    reported_detail = verification.get("detail_code")
    if (
        re.fullmatch(r"\d{8}", expected_detail) is None
        or type(reported_detail) is not str
        or reported_detail != expected_detail
    ):
        return False
    questions = output.get("interview_questions")
    if not isinstance(questions, list) or not questions:
        return False
    expected_codes: set[str] = set()
    for item in questions:
        if not isinstance(item, dict):
            return False
        trace = item.get("ncs_traceability")
        code = str(item.get("ncsClCd") or item.get("ncs_code") or "").strip()
        if not code and isinstance(trace, dict):
            code = str(trace.get("ncs_code") or "").strip()
        if not code:
            return False
        expected_codes.add(code)

    checked_unit_values = verification.get("checked_unit_codes")
    checked_ksa_values = verification.get("checked_ksa_codes")
    if (
        not isinstance(checked_unit_values, list)
        or not isinstance(checked_ksa_values, list)
        or len(checked_unit_values) > 100
        or len(checked_ksa_values) > 100
        or any(
            type(code) is not str
            or not code
            or code != code.strip()
            or len(code) > 80
            for code in [*checked_unit_values, *checked_ksa_values]
        )
    ):
        return False
    checked_units = set(checked_unit_values)
    checked_ksa = set(checked_ksa_values)
    if checked_units != expected_codes or checked_ksa != expected_codes:
        return False

    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != _CLAUDE_ISSUE_KEYS:
            return False
        question_index = issue.get("question_index")
        if (
            issue.get("severity") not in _CLAUDE_ISSUE_SEVERITIES
            or issue.get("category") not in _CLAUDE_ISSUE_CATEGORIES
            or (
                question_index is not None
                and (
                    type(question_index) is not int
                    or not 1 <= question_index <= len(questions)
                )
            )
            or any(
                type(issue.get(key)) is not str
                or not str(issue.get(key)).strip()
                or len(str(issue.get(key))) > 2000
                for key in ("finding", "evidence", "recommendation")
            )
            or issue.get("severity") in {"critical", "high"}
        ):
            return False

    summary = readiness.get("summary")
    return bool(
        expected_codes
        and verification.get("enabled") is True
        and verification.get("status") == "verified"
        and verification.get("issues") == []
        and readiness.get("real_interview_usable") is True
        and type(readiness.get("question_count")) is int
        and readiness.get("question_count") == len(questions)
        and readiness.get("all_have_three_followups") is True
        and readiness.get("all_have_evaluation_points") is True
        and readiness.get("all_have_rating_anchors") is True
        and readiness.get("all_trace_to_verified_ncs") is True
        and type(readiness.get("blocking_issue_count")) is int
        and readiness.get("blocking_issue_count") == 0
        and type(summary) is str
        and bool(summary.strip())
        and len(summary) <= 2000
    )


def _generate(
    connection: sqlite3.Connection,
    run: dict[str, Any],
    generator: GENERATOR,
    now: str,
) -> dict[str, Any]:
    claim = f"generation_claimed:{uuid.uuid4().hex}"
    attempt_number = 0
    with connection:
        cursor = connection.execute(
            """
            UPDATE interview_runs
            SET state='generating',attempt_count=attempt_count+1,
                failure_stage=?,last_error='',generation_started_at=?,updated_at=?
            WHERE id=? AND input_fingerprint=? AND is_current=1 AND (
                state='queued'
                OR (state='failed' AND failure_stage NOT IN ('review'))
                OR (state='generating' AND generation_started_at<=?)
            )
            """,
            (
                claim,
                now,
                now,
                run["id"],
                run["input_fingerprint"],
                _lease_cutoff(now),
            ),
        )
        if cursor.rowcount == 1:
            attempt_row = connection.execute(
                "SELECT attempt_count FROM interview_runs WHERE id=?",
                (run["id"],),
            ).fetchone()
            attempt_number = int(attempt_row[0]) if attempt_row is not None else 0
            connection.execute(
                """
                UPDATE generation_attempts
                SET state='failed',last_error='generation_lease_expired',completed_at=?
                WHERE run_id=? AND state='generating'
                """,
                (now, run["id"]),
            )
            connection.execute(
                """
                INSERT INTO generation_attempts(
                    run_id,attempt_number,input_fingerprint,claim_token,state,started_at
                ) VALUES(?,?,?,?, 'generating', ?)
                """,
                (
                    run["id"],
                    attempt_number,
                    run["input_fingerprint"],
                    claim,
                    now,
                ),
            )
    if cursor.rowcount != 1:
        return _run(connection, run["id"])
    try:
        output = generator(run["bundle"])
        _validate_generation_output(output, run["bundle"])
        output_json = _canonical_json(output)
        output_hash = hashlib.sha256(output_json.encode("utf-8")).hexdigest()
    except Exception as exc:  # callbacks are an external reliability boundary
        completed_at = _utc_now()
        with connection:
            error = _safe_callback_error(exc, "generation_callback_failed")
            run_cursor = connection.execute(
                """
                UPDATE interview_runs
                SET state='failed',failure_stage='generation',last_error=?,updated_at=?
                WHERE id=? AND input_fingerprint=? AND is_current=1
                  AND state='generating' AND failure_stage=?
                """,
                (
                    error,
                    completed_at,
                    run["id"],
                    run["input_fingerprint"],
                    claim,
                ),
            )
            attempt_error = error if run_cursor.rowcount == 1 else "generation_claim_lost"
            connection.execute(
                """
                UPDATE generation_attempts
                SET state='failed',last_error=?,completed_at=?
                WHERE run_id=? AND attempt_number=? AND claim_token=?
                  AND state='generating'
                """,
                (attempt_error, completed_at, run["id"], attempt_number, claim),
            )
        return _run(connection, run["id"])

    completed_at = _utc_now()
    with connection:
        run_cursor = connection.execute(
            """
            UPDATE interview_runs
            SET state='awaiting_claude',generation_output_json=?,
                generation_output_hash=?,failure_stage='',last_error='',
                generated_at=?,updated_at=?
            WHERE id=? AND input_fingerprint=? AND is_current=1
              AND state='generating' AND failure_stage=?
            """,
            (
                output_json,
                output_hash,
                completed_at,
                completed_at,
                run["id"],
                run["input_fingerprint"],
                claim,
            ),
        )
        if run_cursor.rowcount == 1:
            connection.execute(
                """
                UPDATE generation_attempts
                SET state='succeeded',generation_output_hash=?,completed_at=?
                WHERE run_id=? AND attempt_number=? AND claim_token=?
                  AND state='generating'
                """,
                (output_hash, completed_at, run["id"], attempt_number, claim),
            )
        else:
            connection.execute(
                """
                UPDATE generation_attempts
                SET state='failed',last_error='generation_claim_lost',completed_at=?
                WHERE run_id=? AND attempt_number=? AND claim_token=?
                  AND state='generating'
                """,
                (completed_at, run["id"], attempt_number, claim),
            )
    return _run(connection, run["id"])


def _review(
    connection: sqlite3.Connection,
    run: dict[str, Any],
    reviewer: REVIEWER,
    now: str,
) -> dict[str, Any]:
    output_hash = str(run.get("generation_output_hash") or "")
    if not output_hash or run.get("generation_output") is None:
        return run
    if _hash(run["generation_output"]) != output_hash:
        with connection:
            connection.execute(
                """
                UPDATE interview_runs
                SET state='failed',failure_stage='review',
                    last_error='generation_output_hash_mismatch',updated_at=?
                WHERE id=? AND input_fingerprint=? AND is_current=1
                """,
                (now, run["id"], run["input_fingerprint"]),
            )
        return _run(connection, run["id"])
    claim = f"review_claimed:{uuid.uuid4().hex}"
    with connection:
        cursor = connection.execute(
            """
            UPDATE interview_runs
            SET state='awaiting_claude',failure_stage=?,last_error='',
                review_attempt_count=review_attempt_count+1,updated_at=?
            WHERE id=? AND input_fingerprint=? AND generation_output_hash=?
              AND is_current=1 AND (
                (state='awaiting_claude' AND failure_stage='')
                OR (state='failed' AND failure_stage='review')
                OR (state='awaiting_claude' AND failure_stage LIKE 'review_claimed:%'
                    AND updated_at<=?)
              )
            """,
            (
                claim,
                now,
                run["id"],
                run["input_fingerprint"],
                output_hash,
                _lease_cutoff(now),
            ),
        )
    if cursor.rowcount != 1:
        return _run(connection, run["id"])
    binding = {
        "input_fingerprint": run["input_fingerprint"],
        "generation_output_hash": output_hash,
    }
    review_input_hash = _hash(binding)
    request = {
        **binding,
        "review_input_hash": review_input_hash,
        "bundle": run["bundle"],
        "generation_output": run["generation_output"],
    }
    try:
        response = reviewer(request)
        response_json = _canonical_json(response)
        if not isinstance(response, dict):
            raise TypeError("Claude reviewer must return a JSON object")
    except Exception as exc:  # callbacks are an external reliability boundary
        completed_at = _utc_now()
        with connection:
            error = _safe_callback_error(exc, "claude_review_callback_failed")
            connection.execute(
                """
                UPDATE interview_runs
                SET state='failed',failure_stage='review',last_error=?,updated_at=?
                WHERE id=? AND input_fingerprint=? AND generation_output_hash=?
                  AND is_current=1 AND state='awaiting_claude'
                  AND failure_stage=?
                """,
                (
                    error,
                    completed_at,
                    run["id"],
                    run["input_fingerprint"],
                    output_hash,
                    claim,
                ),
            )
        return _run(connection, run["id"])

    echoed_input = str(response.get("input_fingerprint") or "")
    echoed_output = str(response.get("generation_output_hash") or "")
    echoed_review = str(response.get("review_input_hash") or "")
    # A pass is meaningful only when Claude explicitly echoes every binding.
    # Treat absent values exactly like stale ones: neither may promote a run.
    binding_mismatch = (
        echoed_input != run["input_fingerprint"]
        or echoed_output != output_hash
        or echoed_review != review_input_hash
    )
    verdict = str(response.get("verdict") or "").strip().casefold()
    if binding_mismatch:
        verdict = "binding_mismatch"
        next_state = "needs_human_quality_review"
    elif verdict == "pass" and _verified_claude_mcp_pass(response, run):
        next_state = "ready_automated"
    elif verdict == "pass":
        verdict = "mcp_verification_failed"
        next_state = "needs_human_quality_review"
    elif verdict == "revise":
        next_state = "revise"
    else:
        next_state = "needs_human_quality_review"

    completed_at = _utc_now()
    with connection:
        cursor = connection.execute(
            """
            UPDATE interview_runs
            SET state=?,failure_stage='',last_error='',reviewed_at=?,updated_at=?
            WHERE id=? AND input_fingerprint=? AND generation_output_hash=?
              AND is_current=1 AND state='awaiting_claude' AND failure_stage=?
            """,
            (
                next_state,
                completed_at,
                completed_at,
                run["id"],
                run["input_fingerprint"],
                output_hash,
                claim,
            ),
        )
        if cursor.rowcount == 1:
            connection.execute(
                """
                INSERT INTO claude_reviews(
                    run_id,input_fingerprint,generation_output_hash,
                    review_input_hash,verdict,review_json,reviewer_name,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id,review_input_hash) DO UPDATE SET
                    verdict=excluded.verdict,
                    review_json=excluded.review_json,
                    reviewer_name=excluded.reviewer_name,
                    created_at=excluded.created_at
                """,
                (
                    run["id"],
                    run["input_fingerprint"],
                    output_hash,
                    review_input_hash,
                    verdict,
                    response_json,
                    str(response.get("reviewer") or "claude"),
                    completed_at,
                ),
            )
    return _run(connection, run["id"])


def _drive(
    connection: sqlite3.Connection,
    run_id: int,
    generator: GENERATOR | None,
    reviewer: REVIEWER | None,
    *,
    respect_retry_backoff: bool = False,
) -> dict[str, Any]:
    now = _utc_now()
    run = _run(connection, run_id)
    if not run["is_current"] or run["state"] in UPSTREAM_WAIT_STATES:
        return run
    if not run["bundle_json_valid"]:
        with connection:
            connection.execute(
                """
                UPDATE interview_runs
                SET state='failed',failure_stage='data',
                    last_error='bundle_json_invalid',updated_at=?
                WHERE id=? AND is_current=1
                """,
                (now, run_id),
            )
        return _run(connection, run_id)
    if not run["generation_output_json_valid"]:
        with connection:
            connection.execute(
                """
                UPDATE interview_runs
                SET state='failed',failure_stage='generation',
                    last_error='generation_output_json_invalid',
                    generation_output_json='',generation_output_hash='',updated_at=?
                WHERE id=? AND is_current=1
                """,
                (now, run_id),
            )
        run = _run(connection, run_id)
    if run.get("generation_output") is not None:
        try:
            _validate_generation_output(run["generation_output"], run["bundle"])
        except (TypeError, ValueError):
            with connection:
                connection.execute(
                    """
                    UPDATE interview_runs
                    SET state='failed',failure_stage='generation',
                        last_error='generation_output_contract_invalid|retryable=true',
                        generation_output_json='',generation_output_hash='',
                        generated_at='',reviewed_at='',updated_at=?
                    WHERE id=? AND input_fingerprint=? AND is_current=1
                    """,
                    (now, run_id, run["input_fingerprint"]),
                )
            run = _run(connection, run_id)
    if run["state"] in TERMINAL_QUALITY_STATES:
        return run
    if (
        respect_retry_backoff
        and run["state"] == "failed"
        and not _unattended_retry_due(run, now)
    ):
        return run

    should_generate = run["state"] in {"queued", "generating"} or (
        run["state"] == "failed" and run["failure_stage"] != "review"
    )
    if should_generate and generator is not None:
        run = _generate(connection, run, generator, _utc_now())
    if run["state"] == "failed":
        if run["failure_stage"] != "review" or reviewer is None:
            return run
    if (
        respect_retry_backoff
        and run["state"] in {"awaiting_claude", "failed"}
        and reviewer is not None
        and _safe_int(run.get("review_attempt_count"))
        >= UNATTENDED_MAX_REVIEW_ATTEMPTS
    ):
        return run
    if run["state"] in {"awaiting_claude", "failed"} and reviewer is not None:
        run = _review(connection, run, reviewer, _utc_now())
    return run


def _desired_runs(
    connection: sqlite3.Connection,
    posting_id: str,
) -> list[tuple[int | None, int | None, dict[str, Any], str, str]]:
    """Build deterministic desired runs without mutating the database."""

    posting = _posting(connection, posting_id)
    notices_all = _documents(connection, posting_id, "notice")
    notices = [
        row
        for row in notices_all
        if str(row.get("status") or "") == "parsed"
        and str(row.get("markdown") or "").strip()
    ]
    descriptions_all = _documents(connection, posting_id, "job_description")
    parsed_descriptions = [
        row
        for row in descriptions_all
        if str(row.get("status") or "") == "parsed"
        and str(row.get("markdown") or "").strip()
    ]

    desired: list[tuple[int | None, int | None, dict[str, Any], str, str]] = []
    if not parsed_descriptions:
        document = descriptions_all[0] if descriptions_all else None
        bundle = _bundle(
            posting=posting,
            notices=notices,
            job_description=document,
            match=None,
            units=[],
            orchestration_state="waiting_documents",
            reason="parsed_job_description_required",
        )
        desired.append(
            (
                int(document["id"]) if document else None,
                None,
                bundle,
                "waiting_documents",
                "parsed_job_description_required",
            )
        )
    else:
        for document in parsed_descriptions:
            matches = _matches(connection, int(document["id"]))
            if not matches:
                bundle = _bundle(
                    posting=posting,
                    notices=notices,
                    job_description=document,
                    match=None,
                    units=[],
                    orchestration_state=(
                        "waiting_documents" if not notices else "waiting_ncs_mcp"
                    ),
                    reason=(
                        "parsed_notice_required" if not notices else "ncs_match_required"
                    ),
                )
                desired.append(
                    (
                        int(document["id"]),
                        None,
                        bundle,
                        str(bundle["orchestration"]["state"]),
                        str(bundle["orchestration"]["reason"]),
                    )
                )
                continue

            for match in matches:
                match_id = int(match["id"])
                units = _units(connection, match_id)
                if not notices:
                    eligible, state, reason = False, "waiting_documents", "parsed_notice_required"
                else:
                    eligible, state, reason = _eligibility(match, units, document)
                state = "queued" if eligible else state
                bundle = _bundle(
                    posting=posting,
                    notices=notices,
                    job_description=document,
                    match=match,
                    units=units,
                    orchestration_state=state,
                    reason=reason,
                )
                desired.append(
                    (int(document["id"]), match_id, bundle, state, reason)
                )

    return desired


def preview_posting(
    connection: sqlite3.Connection,
    posting_id: str,
) -> list[dict[str, Any]]:
    """Return the reconciliation plan without creating orchestration tables."""

    return [
        {
            "posting_id": posting_id,
            "document_id": document_id,
            "match_id": match_id,
            "scope_key": _scope_key(document_id, match_id),
            "input_fingerprint": _fingerprint(bundle),
            "state": state,
            "reason": reason,
            "bundle": bundle,
        }
        for document_id, match_id, bundle, state, reason in _desired_runs(
            connection, posting_id
        )
    ]


def prepare_posting(
    connection: sqlite3.Connection,
    posting_id: str,
) -> list[dict[str, Any]]:
    """Reconcile persistent runs without invoking a model or reviewer."""

    ensure_orchestration_schema(connection)
    now = _utc_now()
    desired = _desired_runs(connection, posting_id)
    current_ids: list[int] = []
    for document_id, match_id, bundle, state, reason in desired:
        current_ids.append(
            _upsert_run(
                connection,
                posting_id=posting_id,
                document_id=document_id,
                match_id=match_id,
                bundle=bundle,
                desired_state=state,
                reason=reason,
                now=now,
            )
        )

    placeholders = ",".join("?" for _ in current_ids)
    if current_ids:
        with connection:
            connection.execute(
                f"""
                UPDATE interview_runs
                SET is_current=0,superseded_at=?,updated_at=?
                WHERE posting_id=? AND is_current=1 AND id NOT IN ({placeholders})
                """,
                (now, now, posting_id, *current_ids),
            )

    return [_run(connection, run_id) for run_id in current_ids]


def drive_posting(
    connection: sqlite3.Connection,
    posting_id: str,
    *,
    generator: GENERATOR | None = None,
    reviewer: REVIEWER | None = None,
    respect_retry_backoff: bool = False,
    stop_predicate: STOP_PREDICATE | None = None,
) -> list[dict[str, Any]]:
    """Drive prepared current runs once with explicitly supplied callbacks.

    Missing callbacks never fabricate output: an eligible run remains
    ``queued`` without a generator and a generated run remains
    ``awaiting_claude`` without a reviewer.
    """

    ensure_orchestration_schema(connection)
    rows = connection.execute(
        """
        SELECT id FROM interview_runs
        WHERE posting_id=? AND is_current=1
        ORDER BY document_id,match_id,id
        """,
        (posting_id,),
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        result = _drive(
            connection,
            int(row[0]),
            generator,
            reviewer,
            respect_retry_backoff=respect_retry_backoff,
        )
        results.append(result)
        if stop_predicate is not None and stop_predicate(result):
            break
    return results


def orchestrate_posting(
    connection: sqlite3.Connection,
    posting_id: str,
    *,
    generator: GENERATOR,
    reviewer: REVIEWER | None = None,
    respect_retry_backoff: bool = False,
) -> list[dict[str, Any]]:
    """Reconcile and drive all current interview runs for one ALIO posting.

    ``generator`` receives a deterministic generation bundle.  ``reviewer``
    receives that bundle plus the generated output and its local hash binding.
    With no reviewer, successful generation intentionally stops at
    ``awaiting_claude``.
    """

    prepare_posting(connection, posting_id)
    return drive_posting(
        connection,
        posting_id,
        generator=generator,
        reviewer=reviewer,
        respect_retry_backoff=respect_retry_backoff,
    )


def current_runs(
    connection: sqlite3.Connection,
    posting_id: str,
) -> list[dict[str, Any]]:
    """Return current runs without invoking generation or review callbacks."""

    ensure_orchestration_schema(connection)
    rows = connection.execute(
        """
        SELECT id FROM interview_runs
        WHERE posting_id=? AND is_current=1
        ORDER BY document_id,match_id,id
        """,
        (posting_id,),
    ).fetchall()
    return [_run(connection, int(row[0])) for row in rows]

