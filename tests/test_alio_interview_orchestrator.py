from __future__ import annotations

import copy
import hashlib
import json
import sqlite3

import pytest

from app.services import alio_interview_orchestrator as orchestrator


def _source_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE postings (
            posting_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            organization TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            detail_fields_json TEXT NOT NULL DEFAULT '{}',
            inline_notice_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'collected',
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posting_id TEXT NOT NULL,
            file_no TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL,
            kind TEXT NOT NULL,
            section TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            sha256 TEXT NOT NULL DEFAULT '',
            markdown TEXT NOT NULL DEFAULT '',
            fields_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'parsed',
            error TEXT NOT NULL DEFAULT '',
            parser TEXT NOT NULL DEFAULT 'test',
            parser_version TEXT NOT NULL DEFAULT '1'
        );
        CREATE TABLE document_ncs_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            source_detail_index INTEGER NOT NULL DEFAULT 0,
            source_detail TEXT NOT NULL DEFAULT '',
            ncs_detail_source TEXT NOT NULL DEFAULT '',
            source_signature TEXT NOT NULL DEFAULT '',
            source_document_sha256 TEXT NOT NULL DEFAULT '',
            source_fields_sha256 TEXT NOT NULL DEFAULT '',
            matcher_version TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            last_success_status TEXT NOT NULL DEFAULT '',
            match_method TEXT NOT NULL DEFAULT '',
            official_detail_code TEXT NOT NULL DEFAULT '',
            official_detail_name TEXT NOT NULL DEFAULT '',
            expected_unit_count INTEGER NOT NULL DEFAULT 0,
            verified_unit_count INTEGER NOT NULL DEFAULT 0,
            persisted_unit_count INTEGER NOT NULL DEFAULT 0,
            retrieval_complete INTEGER NOT NULL DEFAULT 0,
            retrieval_cap_limited INTEGER NOT NULL DEFAULT 0,
            retryable INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            attempted_at TEXT NOT NULL DEFAULT '',
            matched_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE document_ncs_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            official_unit_base_code TEXT NOT NULL,
            official_unit_full_code TEXT NOT NULL,
            official_unit_name TEXT NOT NULL,
            official_detail_code TEXT NOT NULL,
            official_detail_name TEXT NOT NULL,
            ownership_detail_code TEXT NOT NULL DEFAULT '',
            ownership_detail_name TEXT NOT NULL DEFAULT '',
            catalog_source TEXT NOT NULL DEFAULT '',
            catalog_resolution_kind TEXT NOT NULL DEFAULT '',
            catalog_unit_codes_json TEXT NOT NULL DEFAULT '[]',
            unit_catalog_verified INTEGER NOT NULL DEFAULT 0,
            unit_version_compatible INTEGER NOT NULL DEFAULT 0,
            detail_path_code_verified INTEGER NOT NULL DEFAULT 0,
            retrieval_kind TEXT NOT NULL DEFAULT '',
            retrieval_query TEXT NOT NULL DEFAULT '',
            mcp_source TEXT NOT NULL DEFAULT '',
            mcp_unit_name TEXT NOT NULL DEFAULT '',
            verified_at TEXT NOT NULL DEFAULT ''
        );
        """
    )


def _insert_source(
    connection: sqlite3.Connection,
    *,
    posting_id: str = "304404",
    with_notice: bool = True,
    match_status: str = "exact_complete",
    detail_source: str = "explicit",
    expected: int = 2,
    verified: int = 2,
    persisted: int = 2,
    complete: int = 1,
    capped: int = 0,
    retryable: int = 0,
    unit_rows: int = 2,
    catalog_verified: int = 1,
    catalog_source: str = "bundled_ncs_unit_catalog",
    catalog_resolution_kind: str = "catalog_full_code_exact",
    catalog_contains_full_code: bool = True,
    detail_path_code_verified: int = 1,
    ownership_detail_code: str = "02020301",
) -> tuple[int, int]:
    jd_fields_json = json.dumps(
        {"ncs_detail_source": detail_source, "ncs_detail_candidates": ["비서"]},
        ensure_ascii=False,
    )
    connection.execute(
        """
        INSERT INTO postings(
            posting_id,url,organization,title,detail_fields_json,inline_notice_json
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            posting_id,
            f"https://job.alio.go.kr/recruitview.do?idx={posting_id}",
            "한국마사회",
            "위촉직(비서) 채용공고",
            json.dumps({"접수기간": "2026-08-31"}, ensure_ascii=False),
            json.dumps({"고용형태": "위촉직"}, ensure_ascii=False),
        ),
    )
    if with_notice:
        connection.execute(
            """
            INSERT INTO documents(
                posting_id,file_no,url,kind,filename,sha256,markdown,fields_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                posting_id,
                "3072079",
                f"https://job.alio.go.kr/download.do?fileNo={posting_id}1",
                "notice",
                "채용공고문.pdf",
                "notice-sha",
                "# 채용공고\n지원자격과 전형절차",
                "{}",
            ),
        )
    cursor = connection.execute(
        """
        INSERT INTO documents(
            posting_id,file_no,url,kind,filename,sha256,markdown,fields_json
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            posting_id,
            "3072081",
            f"https://job.alio.go.kr/download.do?fileNo={posting_id}2",
            "job_description",
            "직무기술서.pdf",
            "jd-sha",
            "# 비서 직무기술서\n경영진 업무지원과 일정관리",
            jd_fields_json,
        ),
    )
    document_id = int(cursor.lastrowid)
    source_fields_sha256 = hashlib.sha256(jd_fields_json.encode("utf-8")).hexdigest()
    matcher_version = orchestrator.NCS_MATCH_GATE_VERSION
    source_signature = orchestrator._ncs_match_source_signature(
        document_id=document_id,
        source_detail_index=0,
        source_detail="비서",
        ncs_detail_source=detail_source,
        source_document_sha256="jd-sha",
        source_fields_sha256=source_fields_sha256,
        matcher_version=matcher_version,
    )
    cursor = connection.execute(
        """
        INSERT INTO document_ncs_matches(
            document_id,source_detail, ncs_detail_source,source_signature,
            source_document_sha256,source_fields_sha256,matcher_version,status,
            last_success_status,match_method,official_detail_code,
            official_detail_name,expected_unit_count,verified_unit_count,
            persisted_unit_count,retrieval_complete,retrieval_cap_limited,
            retryable,attempted_at,matched_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            document_id,
            "비서",
            detail_source,
            source_signature,
            "jd-sha",
            source_fields_sha256,
            matcher_version,
            match_status,
            match_status,
            "official_catalog_exact_via_ncs_mcp",
            "02020301",
            "비서",
            expected,
            verified,
            persisted,
            complete,
            capped,
            retryable,
            "2026-08-31T00:00:00+00:00",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    match_id = int(cursor.lastrowid)
    for index in range(1, unit_rows + 1):
        base_code = f"02020301{index:02d}"
        connection.execute(
            """
            INSERT INTO document_ncs_units(
                match_id,official_unit_base_code,official_unit_full_code,
                official_unit_name,official_detail_code,official_detail_name,
                ownership_detail_code,ownership_detail_name,catalog_source,
                catalog_resolution_kind,catalog_unit_codes_json,
                unit_catalog_verified,unit_version_compatible,
                detail_path_code_verified,retrieval_kind,retrieval_query,
                mcp_source,mcp_unit_name,verified_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                match_id,
                base_code,
                f"{base_code}_25v1",
                f"비서 능력단위 {index}",
                "02020301",
                "비서",
                ownership_detail_code,
                "비서",
                catalog_source,
                catalog_resolution_kind,
                json.dumps(
                    [f"{base_code}_25v1"] if catalog_contains_full_code else []
                ),
                catalog_verified,
                1,
                detail_path_code_verified,
                "detail_exact",
                "비서",
                "ncs-mcp",
                f"비서 능력단위 {index}",
                "2026-08-31T00:00:00+00:00",
            ),
        )
    connection.commit()
    return document_id, match_id


@pytest.fixture
def connection(tmp_path):
    database = sqlite3.connect(tmp_path / "alio.sqlite3")
    _source_schema(database)
    try:
        yield database
    finally:
        database.close()


def _structured_output(bundle: dict, *, first_question: str = "Q1") -> dict:
    profile = bundle["generation_profile"]
    question_count = int(profile["main_question_count"])
    follow_up_count = int(profile["follow_up_count"])
    verified_unit_codes = [
        str(unit["official_unit_full_code"])
        for unit in bundle["verified_units"]
    ]
    questions = []
    for index in range(question_count):
        question = first_question if index == 0 else f"{first_question}-{index + 1}"
        follow_ups = [f"probe-{probe + 1}" for probe in range(follow_up_count)]
        unit_code = verified_unit_codes[index % len(verified_unit_codes)]
        questions.append(
            {
                "question": question,
                "follow_ups": follow_ups,
                "structured_follow_ups": [
                    {
                        "trigger": "always" if probe == 0 else "if_evidence_missing",
                        "answer_slot": f"slot_{probe + 1}",
                        "evidence_target": f"evidence_{probe + 1}",
                        "prompt": prompt,
                        "ask_limit": 1,
                    }
                    for probe, prompt in enumerate(follow_ups)
                ],
                "evaluation_points": [
                    "situation evidence",
                    "decision evidence",
                    "action evidence",
                    "result evidence",
                ],
                "assessment_guide": {
                    "dimension_rubrics": [
                        {"dimension": f"dimension-{rubric + 1}", "weight": 25}
                        for rubric in range(4)
                    ]
                },
                "ncsClCd": unit_code,
                "ncs_traceability": {"ncs_code": unit_code},
                "deterministic_ready": True,
                "release_ready": False,
                "ready": False,
                "review_required": True,
                "question_artifact_hash": hashlib.sha256(
                    f"{question}:{index}".encode("utf-8")
                ).hexdigest(),
            }
        )
    return {
        "schema_version": "alio-structured-interview-kit.v2",
        "quality_gate": {
            "passed": False,
            "status": "awaiting_claude_ncs_mcp_review",
            "generation_gate_passed": True,
            "scope": "generation_and_deterministic_field_quality",
            "independent_claude_ncs_mcp_review_required": True,
        },
        "ncs_detail": {"code": "02020301", "verified_unit_count": 2},
        "interview_questions": questions,
    }


def _passing_review(request: dict) -> dict:
    questions = request["generation_output"]["interview_questions"]
    checked_codes = sorted({str(question["ncsClCd"]) for question in questions})
    return {
        "verdict": "pass",
        "reviewer": "claude_code_sonnet_ncs_mcp",
        "input_fingerprint": request["input_fingerprint"],
        "generation_output_hash": request["generation_output_hash"],
        "review_input_hash": request["review_input_hash"],
        "mcp_verification": {
            "enabled": True,
            "status": "verified",
            "detail_code": request["bundle"]["ncs_match"]["official_detail_code"],
            "checked_unit_codes": checked_codes,
            "checked_ksa_codes": checked_codes,
            "issues": [],
        },
        "field_readiness": {
            "real_interview_usable": True,
            "question_count": len(questions),
            "all_have_three_followups": True,
            "all_have_evaluation_points": True,
            "all_have_rating_anchors": True,
            "all_trace_to_verified_ncs": True,
            "blocking_issue_count": 0,
            "summary": "ready",
        },
        "issues": [],
    }


def test_schema_migration_adds_persisted_review_attempt_counter():
    legacy = sqlite3.connect(":memory:")
    legacy.execute(
        """
        CREATE TABLE interview_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            posting_id TEXT NOT NULL,
            document_id INTEGER,
            match_id INTEGER,
            scope_key TEXT NOT NULL,
            input_fingerprint TEXT NOT NULL,
            bundle_json TEXT NOT NULL,
            state TEXT NOT NULL,
            generation_output_json TEXT NOT NULL DEFAULT '',
            generation_output_hash TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            failure_stage TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            is_current INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            generation_started_at TEXT NOT NULL DEFAULT '',
            generated_at TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT NOT NULL DEFAULT '',
            superseded_at TEXT NOT NULL DEFAULT '',
            UNIQUE(posting_id, scope_key, input_fingerprint)
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO interview_runs(
            posting_id,scope_key,input_fingerprint,bundle_json,state,
            created_at,updated_at
        ) VALUES('legacy','document:1:match:1',?,'{}','failed',?,?)
        """,
        ("a" * 64, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )

    orchestrator.ensure_orchestration_schema(legacy)

    columns = {row[1] for row in legacy.execute("PRAGMA table_info(interview_runs)")}
    assert "review_attempt_count" in columns
    assert legacy.execute(
        "SELECT review_attempt_count FROM interview_runs WHERE posting_id='legacy'"
    ).fetchone()[0] == 0


def test_complete_exact_match_builds_notice_jd_unit_bundle_and_waits_for_claude(
    connection,
):
    _insert_source(connection)
    generated: list[dict] = []

    def generator(bundle):
        generated.append(bundle)
        return _structured_output(bundle, first_question="일정 충돌을 조정한 경험은?")

    runs = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator
    )

    assert len(generated) == 1
    assert runs[0]["state"] == "awaiting_claude"
    assert (
        generated[0]["generation_contract_version"]
        == orchestrator.INTERVIEW_GENERATION_CONTRACT_VERSION
    )
    assert "지원자격과 전형절차" in generated[0]["notice_markdown"]
    assert "경영진 업무지원" in generated[0]["job_description"]["markdown"]
    assert [row["official_unit_base_code"] for row in generated[0]["verified_units"]] == [
        "0202030101",
        "0202030102",
    ]
    assert generated[0]["ncs_match"]["ncs_detail_source"] == "explicit"


def test_legacy_exact_match_requires_current_exact_complete_rematch(connection):
    _insert_source(connection, match_status="exact")
    generated: list[dict] = []

    runs = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: generated.append(bundle) or {
            "interview_questions": [{"question": "should not run"}]
        },
    )

    assert generated == []
    assert runs[0]["state"] == "waiting_ncs_mcp"
    assert runs[0]["last_error"] == "legacy_exact_requires_exact_complete_rematch"


@pytest.mark.parametrize(
    ("column", "value", "expected_reason"),
    [
        (
            "matcher_version",
            "superseded-matcher-v0",
            "ncs_match_stale_for_matcher_version",
        ),
        (
            "source_signature",
            "0" * 64,
            "ncs_match_source_signature_stale",
        ),
    ],
)
def test_exact_complete_with_stale_match_contract_never_reaches_generator(
    connection,
    column,
    value,
    expected_reason,
):
    _document_id, match_id = _insert_source(connection)
    connection.execute(
        f"UPDATE document_ncs_matches SET {column}=? WHERE id=?",
        (value, match_id),
    )
    connection.commit()
    generated: list[dict] = []

    runs = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: generated.append(bundle) or {
            "interview_questions": [{"question": "should not run"}]
        },
    )

    assert generated == []
    assert runs[0]["state"] == "waiting_ncs_mcp"
    assert runs[0]["last_error"] == expected_reason


def test_generic_hash_bound_pass_cannot_promote_without_claude_ncs_mcp_evidence(
    connection,
):
    _insert_source(connection, match_status="exact_complete")

    def generic_reviewer(request):
        return {
            "verdict": "pass",
            "input_fingerprint": request["input_fingerprint"],
            "generation_output_hash": request["generation_output_hash"],
            "review_input_hash": request["review_input_hash"],
            "reviewer": "generic-hash-echo-reviewer",
        }

    run = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q1"),
        reviewer=generic_reviewer,
    )[0]

    assert run["state"] == "needs_human_quality_review"
    assert connection.execute(
        "SELECT verdict FROM claude_reviews WHERE run_id=?", (run["id"],)
    ).fetchone()[0] == "mcp_verification_failed"


def test_exact_claude_ncs_mcp_pass_promotes_and_is_idempotent(connection):
    _insert_source(connection, match_status="exact_complete")
    calls = {"generate": 0, "review": 0}

    def generator(_bundle):
        calls["generate"] += 1
        return _structured_output(_bundle, first_question="Q1")

    first = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator
    )[0]

    def reviewer(request):
        calls["review"] += 1
        return _passing_review(request)

    second = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]
    third = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]

    assert first["id"] == second["id"] == third["id"]
    assert second["state"] == third["state"] == "ready_automated"
    assert second["review_attempt_count"] == third["review_attempt_count"] == 1
    assert calls == {"generate": 1, "review": 1}
    review = connection.execute(
        """
        SELECT input_fingerprint,generation_output_hash,review_input_hash,verdict
        FROM claude_reviews WHERE run_id=?
        """,
        (second["id"],),
    ).fetchone()
    expected_binding = hashlib.sha256(
        json.dumps(
            {
                "generation_output_hash": second["generation_output_hash"],
                "input_fingerprint": second["input_fingerprint"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert review == (
        second["input_fingerprint"],
        second["generation_output_hash"],
        expected_binding,
        "pass",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_top_level_key",
        "invalid_issue_severity",
        "invalid_issue_category",
        "critical_issue",
        "high_issue",
        "extra_mcp_key",
        "extra_checked_unit_code",
        "partial_checked_unit_codes",
        "extra_checked_ksa_code",
        "partial_checked_ksa_codes",
        "wrong_detail_code",
        "invalid_readiness_type",
    ],
)
def test_malformed_or_incomplete_claude_mcp_pass_never_promotes(
    connection,
    mutation,
):
    _insert_source(connection, match_status="exact_complete")

    def reviewer(request):
        response = copy.deepcopy(_passing_review(request))
        valid_issue = {
            "severity": "low",
            "category": "field_operability",
            "question_index": 1,
            "finding": "minor finding",
            "evidence": "bounded evidence",
            "recommendation": "minor recommendation",
        }
        if mutation == "extra_top_level_key":
            response["unexpected"] = True
        elif mutation == "invalid_issue_severity":
            response["issues"] = [{**valid_issue, "severity": "urgent"}]
        elif mutation == "invalid_issue_category":
            response["issues"] = [{**valid_issue, "category": "other"}]
        elif mutation == "critical_issue":
            response["issues"] = [{**valid_issue, "severity": "critical"}]
        elif mutation == "high_issue":
            response["issues"] = [{**valid_issue, "severity": "high"}]
        elif mutation == "extra_mcp_key":
            response["mcp_verification"]["unexpected"] = True
        elif mutation == "extra_checked_unit_code":
            response["mcp_verification"]["checked_unit_codes"].append("9999999999_99v9")
        elif mutation == "partial_checked_unit_codes":
            response["mcp_verification"]["checked_unit_codes"] = response[
                "mcp_verification"
            ]["checked_unit_codes"][:1]
        elif mutation == "extra_checked_ksa_code":
            response["mcp_verification"]["checked_ksa_codes"].append("9999999999_99v9")
        elif mutation == "partial_checked_ksa_codes":
            response["mcp_verification"]["checked_ksa_codes"] = response[
                "mcp_verification"
            ]["checked_ksa_codes"][:1]
        elif mutation == "wrong_detail_code":
            response["mcp_verification"]["detail_code"] = "99999999"
        elif mutation == "invalid_readiness_type":
            response["field_readiness"]["question_count"] = True
        return response

    run = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q1"),
        reviewer=reviewer,
    )[0]

    assert run["state"] == "needs_human_quality_review"
    assert run["review_attempt_count"] == 1
    assert connection.execute(
        "SELECT verdict FROM claude_reviews WHERE run_id=?", (run["id"],)
    ).fetchone()[0] == "mcp_verification_failed"


@pytest.mark.parametrize(
    ("overrides", "expected_state"),
    [
        ({"detail_source": "contextual"}, "needs_human_ncs_review"),
        ({"match_status": "review_required"}, "needs_human_ncs_review"),
        ({"match_status": "mcp_unavailable", "retryable": 1}, "waiting_ncs_mcp"),
        ({"complete": 0}, "waiting_ncs_mcp"),
        ({"capped": 1}, "waiting_ncs_mcp"),
        ({"verified": 1}, "needs_human_ncs_review"),
        ({"unit_rows": 1}, "needs_human_ncs_review"),
        ({"catalog_verified": 0}, "needs_human_ncs_review"),
        ({"catalog_source": "unknown"}, "needs_human_ncs_review"),
        ({"catalog_resolution_kind": "base_code_only"}, "needs_human_ncs_review"),
        ({"catalog_contains_full_code": False}, "needs_human_ncs_review"),
        ({"detail_path_code_verified": 0}, "needs_human_ncs_review"),
        ({"ownership_detail_code": "02020302"}, "needs_human_ncs_review"),
    ],
)
def test_strict_ncs_pre_gate_never_calls_generator(
    tmp_path, overrides, expected_state
):
    connection = sqlite3.connect(tmp_path / f"case-{len(str(overrides))}.sqlite3")
    _source_schema(connection)
    _insert_source(connection, **overrides)

    def forbidden(_bundle):
        raise AssertionError("ineligible NCS data must not reach generation")

    run = orchestrator.orchestrate_posting(
        connection, "304404", generator=forbidden
    )[0]
    connection.close()

    assert run["state"] == expected_state
    assert run["attempt_count"] == 0


def test_notice_is_required_before_generation(connection):
    _insert_source(connection, with_notice=False)

    def forbidden(_bundle):
        raise AssertionError("a notice-less bundle must not be generated")

    run = orchestrator.orchestrate_posting(
        connection, "304404", generator=forbidden
    )[0]

    assert run["state"] == "waiting_documents"
    assert run["last_error"] == "parsed_notice_required"


def test_generation_failure_is_persisted_and_retried(connection):
    _insert_source(connection)
    attempts = 0

    def flaky(_bundle):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("provider temporarily unavailable")
        return _structured_output(_bundle, first_question="recovered")

    failed = orchestrator.orchestrate_posting(
        connection, "304404", generator=flaky
    )[0]
    recovered = orchestrator.orchestrate_posting(
        connection, "304404", generator=flaky
    )[0]

    assert failed["state"] == "failed"
    assert failed["failure_stage"] == "generation"
    assert failed["last_error"] == "generation_callback_failed"
    assert recovered["state"] == "awaiting_claude"
    assert recovered["attempt_count"] == 2
    assert attempts == 2


@pytest.mark.parametrize("output", [None, {}, {"interview_questions": []}])
def test_invalid_generation_output_is_failed_not_awaiting_review(connection, output):
    _insert_source(connection)

    run = orchestrator.orchestrate_posting(
        connection, "304404", generator=lambda _bundle: output
    )[0]

    assert run["state"] == "failed"
    assert run["failure_stage"] == "generation"
    assert run["generation_output"] is None


def test_review_failure_retries_review_without_regenerating(connection):
    _insert_source(connection)
    calls = {"generate": 0, "review": 0}

    def generator(_bundle):
        calls["generate"] += 1
        return _structured_output(_bundle, first_question="Q")

    def reviewer(request):
        calls["review"] += 1
        if calls["review"] == 1:
            raise RuntimeError("Claude transport timeout")
        return _passing_review(request)

    failed = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]
    recovered = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]

    assert failed["state"] == "failed"
    assert failed["failure_stage"] == "review"
    assert failed["review_attempt_count"] == 1
    assert recovered["state"] == "ready_automated"
    assert recovered["review_attempt_count"] == 2
    assert calls == {"generate": 1, "review": 2}


def test_binding_mismatch_cannot_become_ready(connection):
    _insert_source(connection)

    run = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q"),
        reviewer=lambda _request: {
            "verdict": "pass",
            "generation_output_hash": "stale-output-hash",
        },
    )[0]

    assert run["state"] == "needs_human_quality_review"
    assert connection.execute(
        "SELECT verdict FROM claude_reviews WHERE run_id=?", (run["id"],)
    ).fetchone()[0] == "binding_mismatch"


def test_missing_binding_echo_cannot_become_ready(connection):
    _insert_source(connection)

    run = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q"),
        reviewer=lambda _request: {"verdict": "pass"},
    )[0]

    assert run["state"] == "needs_human_quality_review"
    assert connection.execute(
        "SELECT verdict FROM claude_reviews WHERE run_id=?", (run["id"],)
    ).fetchone()[0] == "binding_mismatch"


def test_stale_match_for_changed_job_description_never_calls_generator(connection):
    document_id, _match_id = _insert_source(connection)
    connection.execute(
        "UPDATE documents SET markdown=?,sha256=? WHERE id=?",
        ("# 변경된 직무기술서\n회의 운영", "jd-sha-v2", document_id),
    )
    connection.commit()

    def forbidden(_bundle):
        raise AssertionError("a stale NCS match must be refreshed before generation")

    run = orchestrator.orchestrate_posting(
        connection, "304404", generator=forbidden
    )[0]

    assert run["state"] == "waiting_ncs_mcp"
    assert run["last_error"] == "ncs_match_stale_for_document"


def test_generation_output_hash_is_rechecked_before_review(connection):
    _insert_source(connection)
    generated = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q"),
    )[0]
    tampered = _structured_output(generated["bundle"], first_question="tampered")
    connection.execute(
        "UPDATE interview_runs SET generation_output_json=? WHERE id=?",
        (orchestrator._canonical_json(tampered), generated["id"]),
    )
    connection.commit()

    def forbidden(_request):
        raise AssertionError("tampered output must not reach Claude")

    run = orchestrator.drive_posting(
        connection, "304404", reviewer=forbidden
    )[0]

    assert run["state"] == "failed"
    assert run["failure_stage"] == "review"
    assert run["last_error"] == "generation_output_hash_mismatch"


def test_preview_is_read_only_and_prepare_without_generator_stays_queued(connection):
    _insert_source(connection)

    preview = orchestrator.preview_posting(connection, "304404")
    table_before = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='interview_runs'"
    ).fetchone()
    prepared = orchestrator.prepare_posting(connection, "304404")
    unchanged = orchestrator.drive_posting(connection, "304404")

    assert table_before is None
    assert preview[0]["state"] == "queued"
    assert prepared[0]["state"] == unchanged[0]["state"] == "queued"
    assert unchanged[0]["attempt_count"] == 0
    assert unchanged[0]["generation_output"] is None


def test_generation_bundle_omits_large_parser_layout_evidence() -> None:
    document = orchestrator._public_document(
        {
            "id": 1,
            "markdown": "직무 내용",
            "fields": {
                "duties": ["임원 일정 관리"],
                "positioned_items": [{"text": "large parser evidence"}],
                "table_coordinate_contract": {"version": 1},
                "table_scopes": [{"rows": 100}],
                "ncs_detail_candidate_evidence": [{"detail": "비서"}],
                "ncs_detail_absence_evidence": {"reason": "none"},
            },
        }
    )

    assert document["fields"] == {"duties": ["임원 일정 관리"]}
    assert document["markdown"] == "직무 내용"


def test_fresh_generation_claim_prevents_duplicate_callback(connection):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]
    connection.execute(
        """
        UPDATE interview_runs
        SET state='generating',failure_stage='generation_claimed:peer',
            generation_started_at='2999-01-01T00:00:00+00:00'
        WHERE id=?
        """,
        (prepared["id"],),
    )
    connection.commit()

    def forbidden(_bundle):
        raise AssertionError("an active generation claim must not be duplicated")

    run = orchestrator.drive_posting(
        connection, "304404", generator=forbidden
    )[0]

    assert run["state"] == "generating"
    assert run["attempt_count"] == 0


def test_stale_review_claim_survives_prepare_clock_and_is_recovered(connection):
    _insert_source(connection)
    generated = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q"),
    )[0]
    connection.execute(
        """
        UPDATE interview_runs
        SET failure_stage='review_claimed:dead-worker',updated_at='2000-01-01T00:00:00+00:00'
        WHERE id=?
        """,
        (generated["id"],),
    )
    connection.commit()

    def reviewer(request):
        return _passing_review(request)

    orchestrator.prepare_posting(connection, "304404")
    preserved = connection.execute(
        "SELECT updated_at FROM interview_runs WHERE id=?", (generated["id"],)
    ).fetchone()[0]
    run = orchestrator.drive_posting(
        connection, "304404", reviewer=reviewer
    )[0]

    assert preserved == "2000-01-01T00:00:00+00:00"
    assert run["state"] == "ready_automated"


def test_invalid_persisted_generation_json_is_recoverable(connection):
    _insert_source(connection)
    generated = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q1"),
    )[0]
    connection.execute(
        "UPDATE interview_runs SET generation_output_json='{broken' WHERE id=?",
        (generated["id"],),
    )
    connection.commit()

    run = orchestrator.drive_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q2"),
    )[0]

    assert run["state"] == "awaiting_claude"
    assert run["generation_output"]["interview_questions"][0]["question"] == "Q2"


def test_generation_and_review_each_claim_with_fresh_time(connection, monkeypatch):
    _insert_source(connection)
    times = iter(
        [
            "2026-01-01T00:00:00+00:00",  # prepare
            "2026-01-01T00:01:00+00:00",  # drive inspection
            "2026-01-01T00:20:00+00:00",  # generation claim
            "2026-01-01T00:35:00+00:00",  # generation callback completed
            "2026-01-01T00:40:00+00:00",  # review claim
            "2026-01-01T00:55:00+00:00",  # review callback completed
        ]
    )
    monkeypatch.setattr(orchestrator, "_utc_now", lambda: next(times))

    def reviewer(request):
        return _passing_review(request)

    run = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q"),
        reviewer=reviewer,
    )[0]

    assert run["generation_started_at"] == "2026-01-01T00:20:00+00:00"
    assert run["generated_at"] == "2026-01-01T00:35:00+00:00"
    assert run["reviewed_at"] == "2026-01-01T00:55:00+00:00"
    assert run["updated_at"] == "2026-01-01T00:55:00+00:00"


def test_generation_failure_backoff_starts_after_callback_completion(
    connection, monkeypatch
):
    _insert_source(connection)
    times = iter(
        [
            "2026-01-01T00:00:00+00:00",  # prepare
            "2026-01-01T00:01:00+00:00",  # drive inspection
            "2026-01-01T00:02:00+00:00",  # generation claim
            "2026-01-01T00:12:00+00:00",  # failed callback completed
        ]
    )
    monkeypatch.setattr(orchestrator, "_utc_now", lambda: next(times))

    def generator(_bundle):
        raise RuntimeError("provider_timeout|retryable=true")

    run = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=generator,
    )[0]

    assert run["state"] == "failed"
    assert run["updated_at"] == "2026-01-01T00:12:00+00:00"
    assert orchestrator._unattended_retry_due(
        run, "2026-01-01T00:16:59+00:00"
    ) is False
    assert orchestrator._unattended_retry_due(
        run, "2026-01-01T00:17:00+00:00"
    ) is True


def test_generation_attempt_history_preserves_failure_after_successful_retry(
    connection, monkeypatch
):
    _insert_source(connection)
    run = orchestrator.prepare_posting(connection, "304404")[0]
    completed_times = iter(
        [
            "2026-01-01T00:01:00+00:00",
            "2026-01-01T00:03:00+00:00",
        ]
    )
    monkeypatch.setattr(orchestrator, "_utc_now", lambda: next(completed_times))

    def failed_generator(_bundle):
        raise RuntimeError(
            "model_question_count_mismatch|retryable=false|model=test/model"
        )

    failed = orchestrator._generate(
        connection,
        run,
        failed_generator,
        "2026-01-01T00:00:00+00:00",
    )
    succeeded = orchestrator._generate(
        connection,
        failed,
        lambda bundle: _structured_output(bundle, first_question="Q"),
        "2026-01-01T00:02:00+00:00",
    )

    assert succeeded["state"] == "awaiting_claude"
    attempts = connection.execute(
        """
        SELECT attempt_number,state,last_error,generation_output_hash,
               started_at,completed_at
        FROM generation_attempts WHERE run_id=? ORDER BY attempt_number
        """,
        (run["id"],),
    ).fetchall()
    assert [tuple(row[:3]) for row in attempts] == [
        (
            1,
            "failed",
            "model_question_count_mismatch|retryable=false|model=test/model",
        ),
        (2, "succeeded", ""),
    ]
    assert attempts[0][3] == ""
    assert len(attempts[1][3]) == 64
    assert attempts[0][4:] == (
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:01:00+00:00",
    )
    assert attempts[1][4:] == (
        "2026-01-01T00:02:00+00:00",
        "2026-01-01T00:03:00+00:00",
    )


def test_review_failure_backoff_starts_after_callback_completion(
    connection, monkeypatch
):
    _insert_source(connection)
    times = iter(
        [
            "2026-01-01T00:00:00+00:00",  # prepare
            "2026-01-01T00:01:00+00:00",  # drive inspection
            "2026-01-01T00:02:00+00:00",  # generation claim
            "2026-01-01T00:03:00+00:00",  # generation completed
            "2026-01-01T00:04:00+00:00",  # review claim
            "2026-01-01T00:14:00+00:00",  # failed review completed
        ]
    )
    monkeypatch.setattr(orchestrator, "_utc_now", lambda: next(times))

    def reviewer(_request):
        raise RuntimeError("ai_quality_review_timeout|retryable=true")

    run = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q"),
        reviewer=reviewer,
    )[0]

    assert run["state"] == "failed"
    assert run["failure_stage"] == "review"
    assert run["updated_at"] == "2026-01-01T00:14:00+00:00"
    assert orchestrator._unattended_retry_due(
        run, "2026-01-01T00:18:59+00:00"
    ) is False
    assert orchestrator._unattended_retry_due(
        run, "2026-01-01T00:19:00+00:00"
    ) is True


def test_changed_input_supersedes_ready_run_and_creates_new_fingerprint(connection):
    document_id, match_id = _insert_source(connection)
    generated_markdown: list[str] = []

    def generator(bundle):
        generated_markdown.append(bundle["job_description"]["markdown"])
        return _structured_output(bundle, first_question="Q")

    def reviewer(request):
        return _passing_review(request)
    first = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]
    connection.execute(
        "UPDATE documents SET markdown=?,sha256=? WHERE id=?",
        ("# 변경된 직무기술서\n회의 운영", "jd-sha-v2", document_id),
    )
    source_fields_sha256 = connection.execute(
        "SELECT source_fields_sha256 FROM document_ncs_matches WHERE id=?",
        (match_id,),
    ).fetchone()[0]
    source_signature = orchestrator._ncs_match_source_signature(
        document_id=document_id,
        source_detail_index=0,
        source_detail="비서",
        ncs_detail_source="explicit",
        source_document_sha256="jd-sha-v2",
        source_fields_sha256=source_fields_sha256,
        matcher_version=orchestrator.NCS_MATCH_GATE_VERSION,
    )
    connection.execute(
        """
        UPDATE document_ncs_matches SET
            source_document_sha256=?,source_signature=?
        WHERE id=?
        """,
        ("jd-sha-v2", source_signature, match_id),
    )
    connection.commit()
    second = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]

    assert first["state"] == second["state"] == "ready_automated"
    assert first["id"] != second["id"]
    assert first["input_fingerprint"] != second["input_fingerprint"]
    persisted = connection.execute(
        """
        SELECT id,is_current,superseded_at FROM interview_runs
        WHERE posting_id='304404' ORDER BY id
        """
    ).fetchall()
    assert persisted[0][0:2] == (first["id"], 0)
    assert persisted[0][2]
    assert persisted[1][0:2] == (second["id"], 1)
    assert generated_markdown == [
        "# 비서 직무기술서\n경영진 업무지원과 일정관리",
        "# 변경된 직무기술서\n회의 운영",
    ]


def test_generation_contract_version_supersedes_terminal_output(
    connection,
    monkeypatch,
):
    _insert_source(connection)

    def generator(_bundle):
        return _structured_output(_bundle, first_question="Q")

    def reviewer(request):
        return _passing_review(request)

    first = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=generator,
        reviewer=reviewer,
    )[0]
    monkeypatch.setattr(
        orchestrator,
        "INTERVIEW_GENERATION_CONTRACT_VERSION",
        "alio-interview-generation-contract.test-next",
    )
    second = orchestrator.orchestrate_posting(
        connection,
        "304404",
        generator=generator,
        reviewer=reviewer,
    )[0]

    assert first["state"] == second["state"] == "ready_automated"
    assert first["id"] != second["id"]
    assert first["input_fingerprint"] != second["input_fingerprint"]
    persisted = connection.execute(
        "SELECT id,is_current FROM interview_runs ORDER BY id"
    ).fetchall()
    assert persisted == [(first["id"], 0), (second["id"], 1)]


def test_non_secret_generation_profile_change_supersedes_current_run(
    connection,
    monkeypatch,
):
    _insert_source(connection)
    monkeypatch.setenv("INTERVIEW_GENERATION_PROVIDER", "openai_api")
    monkeypatch.delenv("OPENROUTER_DEFAULT_MODEL", raising=False)
    first = orchestrator.prepare_posting(connection, "304404")[0]

    monkeypatch.setenv("INTERVIEW_GENERATION_PROVIDER", "openrouter_api")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "example/free-model")
    second = orchestrator.prepare_posting(connection, "304404")[0]

    assert first["id"] != second["id"]
    assert first["input_fingerprint"] != second["input_fingerprint"]
    assert first["bundle"]["generation_profile"] == {
        "provider": "openai_api",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "quality_review_model": "gpt-5.6-sol",
        "quality_review_reasoning_effort": "high",
        "execution_epoch": "1",
        "main_question_count": 5,
        "follow_up_count": 3,
    }
    assert second["bundle"]["generation_profile"]["provider"] == "openrouter_api"
    assert second["bundle"]["generation_profile"]["model"] == "example/free-model"


def test_reasoning_effort_and_execution_epoch_supersede_current_run(
    connection,
    monkeypatch,
):
    _insert_source(connection)
    monkeypatch.setenv("INTERVIEW_GENERATION_PROVIDER", "openrouter_api")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "example/free-model")
    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "medium")
    monkeypatch.setenv("NCSCOPE_ALIO_GENERATION_EPOCH", "1")
    first = orchestrator.prepare_posting(connection, "304404")[0]

    monkeypatch.setenv("OPENROUTER_PRIMARY_REASONING_EFFORT", "none")
    second = orchestrator.prepare_posting(connection, "304404")[0]
    monkeypatch.setenv("NCSCOPE_ALIO_GENERATION_EPOCH", "2")
    third = orchestrator.prepare_posting(connection, "304404")[0]

    assert len({first["id"], second["id"], third["id"]}) == 3
    assert len(
        {
            first["input_fingerprint"],
            second["input_fingerprint"],
            third["input_fingerprint"],
        }
    ) == 3
    assert second["bundle"]["generation_profile"]["reasoning_effort"] == "none"
    assert third["bundle"]["generation_profile"]["execution_epoch"] == "2"


def test_quality_retry_reasoning_effort_supersedes_current_run(
    connection,
    monkeypatch,
):
    _insert_source(connection)
    monkeypatch.setenv("INTERVIEW_GENERATION_PROVIDER", "openrouter_api")
    monkeypatch.setenv("OPENROUTER_DEFAULT_MODEL", "example/free-model")
    monkeypatch.setenv("OPENROUTER_QUALITY_RETRY_REASONING_EFFORT", "high")
    first = orchestrator.prepare_posting(connection, "304404")[0]

    monkeypatch.setenv("OPENROUTER_QUALITY_RETRY_REASONING_EFFORT", "medium")
    second = orchestrator.prepare_posting(connection, "304404")[0]

    assert first["id"] != second["id"]
    assert first["input_fingerprint"] != second["input_fingerprint"]
    assert (
        first["bundle"]["generation_profile"]["quality_review_reasoning_effort"]
        == "high"
    )
    assert (
        second["bundle"]["generation_profile"]["quality_review_reasoning_effort"]
        == "medium"
    )


def test_audit_only_changes_preserve_fingerprint_and_ready_run(connection):
    document_id, match_id = _insert_source(connection)
    calls = {"generate": 0, "review": 0}

    def generator(_bundle):
        calls["generate"] += 1
        return _structured_output(_bundle, first_question="Q")

    def reviewer(request):
        calls["review"] += 1
        return _passing_review(request)

    first = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]
    connection.execute(
        "UPDATE postings SET status='reinspected',error='old warning' WHERE posting_id='304404'"
    )
    connection.execute(
        "UPDATE documents SET parser_version='2' WHERE id=?", (document_id,)
    )
    connection.execute(
        "UPDATE document_ncs_matches SET attempted_at=?,matched_at=? WHERE id=?",
        ("2027-01-01", "2027-01-01", match_id),
    )
    connection.execute(
        "UPDATE document_ncs_units SET verified_at='2027-01-01' WHERE match_id=?",
        (match_id,),
    )
    connection.commit()
    second = orchestrator.orchestrate_posting(
        connection, "304404", generator=generator, reviewer=reviewer
    )[0]

    assert second["id"] == first["id"]
    assert second["input_fingerprint"] == first["input_fingerprint"]
    assert second["state"] == "ready_automated"
    assert calls == {"generate": 1, "review": 1}


def test_unattended_worker_respects_retryable_failure_backoff(connection, monkeypatch):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]
    connection.execute(
        """
        UPDATE interview_runs
        SET state='failed',failure_stage='generation',attempt_count=1,
            last_error='openrouter_rate_limited|http_status=429|retryable=true',
            updated_at='2026-01-01T00:00:00+00:00'
        WHERE id=?
        """,
        (prepared["id"],),
    )
    connection.commit()
    monkeypatch.setenv("NCSCOPE_ALIO_RETRY_BASE_SEC", "30")
    monkeypatch.setattr(
        orchestrator, "_utc_now", lambda: "2026-01-01T00:00:29+00:00"
    )

    def forbidden(_bundle):
        raise AssertionError("retryable failures must cool down before retry")

    run = orchestrator.drive_posting(
        connection,
        "304404",
        generator=forbidden,
        respect_retry_backoff=True,
    )[0]

    assert run["state"] == "failed"
    assert run["attempt_count"] == 1


def test_prepare_preserves_failed_runtime_error_and_backoff_clock(connection):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]
    connection.execute(
        """
        UPDATE interview_runs
        SET state='failed',failure_stage='generation',attempt_count=1,
            last_error='openrouter_rate_limited|http_status=429|retryable=true',
            updated_at='2026-01-01T00:00:00+00:00'
        WHERE id=?
        """,
        (prepared["id"],),
    )
    connection.commit()

    refreshed = orchestrator.prepare_posting(connection, "304404")[0]

    assert refreshed["id"] == prepared["id"]
    assert refreshed["state"] == "failed"
    assert refreshed["failure_stage"] == "generation"
    assert refreshed["last_error"] == (
        "openrouter_rate_limited|http_status=429|retryable=true"
    )
    assert refreshed["updated_at"] == "2026-01-01T00:00:00+00:00"


def test_drive_posting_stops_within_posting_when_predicate_matches(connection):
    _insert_source(connection)
    source = connection.execute(
        "SELECT * FROM document_ncs_matches LIMIT 1"
    ).fetchone()
    columns = [item[1] for item in connection.execute("PRAGMA table_info(document_ncs_matches)")]
    row = dict(zip(columns, source, strict=True))
    row.pop("id")
    row["source_detail_index"] = 1
    placeholders = ",".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO document_ncs_matches({','.join(row)}) VALUES({placeholders})",
        tuple(row.values()),
    )
    connection.commit()
    orchestrator.prepare_posting(connection, "304404")
    calls = 0

    def unavailable(_bundle):
        nonlocal calls
        calls += 1
        raise RuntimeError("openrouter_rate_limited|http_status=429|retryable=true")

    results = orchestrator.drive_posting(
        connection,
        "304404",
        generator=unavailable,
        stop_predicate=lambda run: "openrouter_rate_limited" in run["last_error"],
    )

    assert calls == 1
    assert len(results) == 1
    assert results[0]["state"] == "failed"


def test_unattended_worker_retries_explicit_retryable_failure_when_due(
    connection, monkeypatch
):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]
    connection.execute(
        """
        UPDATE interview_runs
        SET state='failed',failure_stage='generation',attempt_count=1,
            last_error='ai_quality_review_network_failed|retryable=true',
            updated_at='2026-01-01T00:00:00+00:00'
        WHERE id=?
        """,
        (prepared["id"],),
    )
    connection.commit()
    monkeypatch.setenv("NCSCOPE_ALIO_RETRY_BASE_SEC", "30")
    monkeypatch.setattr(
        orchestrator, "_utc_now", lambda: "2026-01-01T00:00:30+00:00"
    )

    run = orchestrator.drive_posting(
        connection,
        "304404",
        generator=lambda bundle: _structured_output(bundle, first_question="Q"),
        respect_retry_backoff=True,
    )[0]

    assert run["state"] == "awaiting_claude"
    assert run["attempt_count"] == 2


def test_unattended_worker_never_retries_non_retryable_failure(connection, monkeypatch):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]
    connection.execute(
        """
        UPDATE interview_runs
        SET state='failed',failure_stage='generation',attempt_count=1,
            last_error='openrouter_credit_or_model_access_required|http_status=402|retryable=false',
            updated_at='2000-01-01T00:00:00+00:00'
        WHERE id=?
        """,
        (prepared["id"],),
    )
    connection.commit()
    monkeypatch.setattr(
        orchestrator, "_utc_now", lambda: "2026-01-01T00:00:00+00:00"
    )

    def forbidden(_bundle):
        raise AssertionError("unattended workers must not repeat non-retryable failures")

    run = orchestrator.drive_posting(
        connection,
        "304404",
        generator=forbidden,
        respect_retry_backoff=True,
    )[0]

    assert run["state"] == "failed"
    assert run["attempt_count"] == 1


def test_persisted_structured_kit_missing_operating_probes_is_regenerated(connection):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]
    incomplete = {
        "schema_version": "alio-structured-interview-kit.v2",
        "quality_gate": {
            "passed": False,
            "status": "awaiting_claude_ncs_mcp_review",
            "generation_gate_passed": True,
            "scope": "generation_and_deterministic_field_quality",
            "independent_claude_ncs_mcp_review_required": True,
        },
        "ncs_detail": {"code": "02020301", "verified_unit_count": 11},
        "interview_questions": [
            {
                "question": "기존 질문",
                "follow_ups": ["상황은?", "행동은?", "결과는?"],
                "assessment_guide": {"dimension_rubrics": []},
            }
        ],
    }
    serialized = orchestrator._canonical_json(incomplete)
    connection.execute(
        """
        UPDATE interview_runs
        SET state='awaiting_claude',generation_output_json=?,generation_output_hash=?
        WHERE id=?
        """,
        (serialized, orchestrator._hash(incomplete), prepared["id"]),
    )
    connection.commit()
    valid = _structured_output(prepared["bundle"], first_question="재생성 질문")

    run = orchestrator.drive_posting(
        connection,
        "304404",
        generator=lambda _bundle: valid,
    )[0]

    assert run["state"] == "awaiting_claude"
    assert run["attempt_count"] == 1
    assert run["generation_output"]["interview_questions"][0]["question"] == "재생성 질문"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda output: output.pop("schema_version"),
        lambda output: output["interview_questions"].pop(),
        lambda output: output["interview_questions"][0]["follow_ups"].pop(),
        lambda output: output["interview_questions"][0]["evaluation_points"].pop(),
    ],
)
def test_generation_boundary_rejects_any_non_exact_interview_shape(
    connection,
    mutate,
):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]
    output = _structured_output(prepared["bundle"])
    mutate(output)

    run = orchestrator.drive_posting(
        connection,
        "304404",
        generator=lambda _bundle: output,
    )[0]

    assert run["state"] == "failed"
    assert run["failure_stage"] == "generation"
    assert run["last_error"] == "structured_interview_generation_contract_invalid"
    assert run["generation_output"] is None


def test_stale_generation_reclaim_terminalizes_old_attempt_and_keeps_winner(
    connection,
):
    _insert_source(connection)
    prepared = orchestrator.prepare_posting(connection, "304404")[0]

    def stale_worker(bundle):
        connection.execute(
            """
            UPDATE interview_runs
            SET generation_started_at='2000-01-01T00:00:00+00:00',
                updated_at='2000-01-01T00:00:00+00:00'
            WHERE id=?
            """,
            (prepared["id"],),
        )
        connection.commit()
        reclaimed = orchestrator._generate(
            connection,
            orchestrator._run(connection, prepared["id"]),
            lambda current_bundle: _structured_output(
                current_bundle,
                first_question="winning generation",
            ),
            "2026-01-01T00:20:00+00:00",
        )
        assert reclaimed["state"] == "awaiting_claude"
        return _structured_output(bundle, first_question="stale generation")

    final = orchestrator._generate(
        connection,
        prepared,
        stale_worker,
        "2026-01-01T00:00:00+00:00",
    )

    assert final["state"] == "awaiting_claude"
    assert final["generation_output"]["interview_questions"][0]["question"] == (
        "winning generation"
    )
    attempts = connection.execute(
        """
        SELECT attempt_number,state,last_error,generation_output_hash
        FROM generation_attempts WHERE run_id=? ORDER BY attempt_number
        """,
        (prepared["id"],),
    ).fetchall()
    assert attempts[0] == (1, "failed", "generation_lease_expired", "")
    assert attempts[1][0:3] == (2, "succeeded", "")
    assert attempts[1][3] == final["generation_output_hash"]

