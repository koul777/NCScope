from __future__ import annotations

import json
import hashlib
import sqlite3

import pytest

from scripts import collect_alio_documents as collector
from scripts import run_alio_interview_orchestrator as cli


def injected_generator(bundle):
    question_count = int(bundle["generation_profile"]["main_question_count"])
    follow_up_count = int(bundle["generation_profile"]["follow_up_count"])
    verified_unit_codes = [
        str(unit["official_unit_full_code"])
        for unit in bundle["verified_units"]
    ]
    questions = []
    for question_index in range(question_count):
        question = (
            "여러 일정이 충돌했을 때 우선순위를 조정한 경험을 말씀해 주세요."
            if question_index == 0
            else f"구조화 면접 질문 {question_index + 1}"
        )
        follow_ups = [f"확인 질문 {index + 1}" for index in range(follow_up_count)]
        unit_code = verified_unit_codes[question_index % len(verified_unit_codes)]
        questions.append(
            {
                "question": question,
                "follow_ups": follow_ups,
                "structured_follow_ups": [
                    {
                        "trigger": "always" if index == 0 else "if_evidence_missing",
                        "answer_slot": f"slot_{index + 1}",
                        "evidence_target": f"evidence_{index + 1}",
                        "prompt": prompt,
                        "ask_limit": 1,
                    }
                    for index, prompt in enumerate(follow_ups)
                ],
                "evaluation_points": ["상황", "판단", "행동", "결과"],
                "assessment_guide": {
                    "dimension_rubrics": [
                        {"dimension": f"평가요소 {index + 1}", "weight": 25}
                        for index in range(4)
                    ]
                },
                "ncsClCd": unit_code,
                "ncs_traceability": {"ncs_code": unit_code},
                "deterministic_ready": True,
                "release_ready": False,
                "ready": False,
                "review_required": True,
                "question_artifact_hash": hashlib.sha256(
                    f"{question}:{question_index}".encode("utf-8")
                ).hexdigest(),
                "official_detail_code": bundle["ncs_match"]["official_detail_code"],
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
        "ncs_detail": {
            "code": bundle["ncs_match"]["official_detail_code"],
            "verified_unit_count": len(bundle["verified_units"]),
        },
        "interview_questions": questions,
    }


def hash_bound_reviewer(request):
    return {
        "verdict": "pass",
        "reviewer": "generic-hash-echo-reviewer",
        "input_fingerprint": request["input_fingerprint"],
        "generation_output_hash": request["generation_output_hash"],
        "review_input_hash": request["review_input_hash"],
    }


def ncs_mcp_passing_reviewer(request):
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


def _database(tmp_path):
    path = tmp_path / "alio.sqlite3"
    connection = sqlite3.connect(path)
    collector._schema(connection)
    connection.execute(
        """
        INSERT INTO postings(
            posting_id,url,organization,title,detail_fields_json,
            inline_notice_json,discovered_at,status
        ) VALUES(?,?,?,?,?,?,?,'collected')
        """,
        (
            "304404",
            "https://job.alio.go.kr/recruitview.do?idx=304404",
            "한국마사회",
            "위촉직(비서) 채용공고",
            "{}",
            "{}",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO documents(
            posting_id,file_no,url,kind,filename,sha256,markdown,
            fields_json,status,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "304404",
            "3072079",
            "https://job.alio.go.kr/download.do?fileNo=3072079",
            "notice",
            "채용공고문.pdf",
            "notice-sha",
            "# 채용공고문\n지원자격과 전형절차",
            "{}",
            "parsed",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    jd_fields_json = json.dumps(
        {"ncs_detail_source": "explicit", "ncs_detail_candidates": ["비서"]},
        ensure_ascii=False,
    )
    cursor = connection.execute(
        """
        INSERT INTO documents(
            posting_id,file_no,url,kind,filename,sha256,markdown,
            fields_json,status,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "304404",
            "3072081",
            "https://job.alio.go.kr/download.do?fileNo=3072081",
            "job_description",
            "직무기술서.pdf",
            "jd-sha",
            "# 비서 직무기술서\n경영진 일정관리",
            jd_fields_json,
            "parsed",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    document_id = int(cursor.lastrowid)
    source_fields_sha256 = hashlib.sha256(jd_fields_json.encode("utf-8")).hexdigest()
    matcher_version = cli.orchestrator.NCS_MATCH_GATE_VERSION
    source_signature = cli.orchestrator._ncs_match_source_signature(
        document_id=document_id,
        source_detail_index=0,
        source_detail="비서",
        ncs_detail_source="explicit",
        source_document_sha256="jd-sha",
        source_fields_sha256=source_fields_sha256,
        matcher_version=matcher_version,
    )
    cursor = connection.execute(
        """
        INSERT INTO document_ncs_matches(
            document_id,source_detail_index,source_detail,ncs_detail_source,
            source_signature,source_document_sha256,source_fields_sha256,
            matcher_version,status,last_success_status,match_method,
            official_detail_code,official_detail_name,expected_unit_count,
            verified_unit_count,persisted_unit_count,retrieval_complete,
            retrieval_cap_limited,retryable,attempted_at,matched_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            document_id,
            0,
            "비서",
            "explicit",
            source_signature,
            "jd-sha",
            source_fields_sha256,
            matcher_version,
            "exact_complete",
            "exact_complete",
            "official_catalog_exact_via_ncs_mcp",
            "02020301",
            "비서",
            1,
            1,
            1,
            1,
            0,
            0,
            "2026-08-31T00:00:00+00:00",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    match_id = int(cursor.lastrowid)
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
            "0202030101",
            "0202030101_25v1",
            "경영진 일정관리",
            "02020301",
            "비서",
            "02020301",
            "비서",
            "bundled_ncs_unit_catalog",
            "catalog_full_code_exact",
            '["0202030101_25v1"]',
            1,
            0,
            1,
            "detail_exact",
            "비서",
            "ncs-mcp",
            "경영진 일정관리",
            "2026-08-31T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()
    return path


def _invoke(capsys, *arguments):
    exit_code = cli.main(list(arguments))
    output = json.loads(capsys.readouterr().out)
    return exit_code, output


def test_dry_run_is_read_only_and_reports_exact_eligible_state(tmp_path, capsys):
    database = _database(tmp_path)

    exit_code, output = _invoke(
        capsys,
        "dry-run",
        "--database",
        str(database),
        "--posting-id",
        "304404",
    )

    connection = sqlite3.connect(database)
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='interview_runs'"
    ).fetchone()
    connection.close()
    assert exit_code == 0
    assert table is None
    assert output["state_counts"] == {"queued": 1}
    assert output["runs"][0]["official_detail_code"] == "02020301"
    assert output["runs"][0]["verified_unit_count"] == 1


def test_prepare_and_unconfigured_run_once_never_fabricate_questions(tmp_path, capsys):
    database = _database(tmp_path)

    _, prepared = _invoke(
        capsys, "prepare", "--database", str(database), "--posting-id", "304404"
    )
    _, unchanged = _invoke(
        capsys, "run-once", "--database", str(database), "--posting-id", "304404"
    )

    connection = sqlite3.connect(database)
    persisted = connection.execute(
        "SELECT state,attempt_count,generation_output_json FROM interview_runs"
    ).fetchone()
    connection.close()
    assert prepared["state_counts"] == {"queued": 1}
    assert unchanged["state_counts"] == {"queued": 1}
    assert unchanged["generator_configured"] is False
    assert persisted == ("queued", 0, "")


def test_run_once_generic_hash_echo_pass_is_not_ready_automated(tmp_path, capsys):
    database = _database(tmp_path)
    module = __name__

    _invoke(
        capsys,
        "run-once",
        "--database",
        str(database),
        "--posting-id",
        "304404",
        "--generator",
        f"{module}:injected_generator",
    )
    _, reviewed = _invoke(
        capsys,
        "run-once",
        "--database",
        str(database),
        "--posting-id",
        "304404",
        "--reviewer",
        f"{module}:hash_bound_reviewer",
    )

    connection = sqlite3.connect(database)
    review = connection.execute(
        "SELECT verdict FROM claude_reviews"
    ).fetchone()
    run = connection.execute("SELECT state FROM interview_runs").fetchone()
    connection.close()
    assert reviewed["state_counts"] == {"needs_human_quality_review": 1}
    assert review == ("mcp_verification_failed",)
    assert run == ("needs_human_quality_review",)


def test_run_once_injects_generation_then_exact_claude_ncs_mcp_review(
    tmp_path,
    capsys,
):
    database = _database(tmp_path)
    module = __name__

    _, generated = _invoke(
        capsys,
        "run-once",
        "--database",
        str(database),
        "--posting-id",
        "304404",
        "--generator",
        f"{module}:injected_generator",
    )
    _, reviewed = _invoke(
        capsys,
        "run-once",
        "--database",
        str(database),
        "--posting-id",
        "304404",
        "--reviewer",
        f"{module}:ncs_mcp_passing_reviewer",
    )

    connection = sqlite3.connect(database)
    review = connection.execute(
        "SELECT verdict,input_fingerprint,generation_output_hash FROM claude_reviews"
    ).fetchone()
    run = connection.execute(
        "SELECT state,input_fingerprint,generation_output_hash FROM interview_runs"
    ).fetchone()
    connection.close()
    assert generated["state_counts"] == {"awaiting_claude": 1}
    assert reviewed["state_counts"] == {"ready_automated": 1}
    assert review == ("pass", run[1], run[2])
    assert run[0] == "ready_automated"


def test_one_bad_posting_does_not_abort_the_remaining_batch(tmp_path, capsys):
    database = _database(tmp_path)

    exit_code, output = _invoke(
        capsys,
        "dry-run",
        "--database",
        str(database),
        "--posting-id",
        "missing",
        "--posting-id",
        "304404",
    )

    assert exit_code == 1
    assert output["ok"] is False
    assert output["state_counts"] == {"queued": 1}
    assert output["runs"][0]["posting_id"] == "304404"
    assert output["errors"][0]["posting_id"] == "missing"


def test_shared_provider_failure_opens_batch_circuit_breaker(
    tmp_path,
    capsys,
    monkeypatch,
):
    database = _database(tmp_path)
    driven: list[str] = []
    monkeypatch.setattr(cli.orchestrator, "prepare_posting", lambda *_args: [])

    def fake_drive(_connection, posting_id, **_kwargs):
        driven.append(posting_id)
        return [
            {
                "id": len(driven),
                "posting_id": posting_id,
                "state": "failed" if posting_id == "first" else "queued",
                "last_error": (
                    "openrouter_rate_limited|http_status=429|retryable=true"
                    if posting_id == "first"
                    else ""
                ),
                "bundle": {},
            }
        ]

    monkeypatch.setattr(cli.orchestrator, "drive_posting", fake_drive)

    exit_code, output = _invoke(
        capsys,
        "run-once",
        "--database",
        str(database),
        "--posting-id",
        "first",
        "--posting-id",
        "second",
        "--generator",
        f"{__name__}:injected_generator",
    )

    assert exit_code == 0
    assert driven == ["first"]
    assert output["posting_count"] == 2
    assert output["processed_posting_count"] == 1
    assert output["circuit_breaker"] == "openrouter_rate_limited"
    assert output["state_counts"] == {"failed": 1}


def test_shared_infrastructure_codes_cover_quality_and_claude_runtime_failures():
    for error in (
        "ai_quality_review_timeout|retryable=true",
        "ai_quality_review_provider_failed|retryable=true",
        "ai_quality_review_key_unavailable|retryable=false",
        "claude_review_process_failed|retryable=true",
        "claude_review_cli_failed_exit_1|retryable=true",
    ):
        assert cli._shared_infrastructure_code(error) == error.split("|", 1)[0]


@pytest.mark.parametrize(
    "error",
    [
        "model_question_count_mismatch|retryable=false|model=openrouter/free",
        "model_response_invalid_json|retryable=false|model=openrouter/free",
        "question_set_count_or_diversity_failed|retryable=false|model=openrouter/free",
    ],
)
def test_model_output_failures_do_not_open_shared_infrastructure_breaker(error: str):
    assert cli._shared_infrastructure_code(error) == ""


def test_active_shared_failure_backoff_blocks_batch_before_any_callback(
    tmp_path,
    capsys,
    monkeypatch,
):
    database = _database(tmp_path)
    connection = sqlite3.connect(database)
    cli.orchestrator.prepare_posting(connection, "304404")
    connection.execute(
        """
        UPDATE interview_runs
        SET state='failed',attempt_count=1,failure_stage='generation',
            last_error='openrouter_rate_limited|http_status=429|retryable=true',
            updated_at=?
        """,
        (cli.orchestrator._utc_now(),),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(
        cli.orchestrator,
        "prepare_posting",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must stop before prepare")),
    )
    monkeypatch.setattr(
        cli.orchestrator,
        "drive_posting",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must stop before callback")
        ),
    )

    exit_code, output = _invoke(
        capsys,
        "run-once",
        "--database",
        str(database),
        "--posting-id",
        "304404",
        "--generator",
        f"{__name__}:injected_generator",
        "--respect-retry-backoff",
    )

    assert exit_code == 0
    assert output["processed_posting_count"] == 0
    assert output["circuit_breaker"] == "openrouter_rate_limited"
    assert output["runs"] == []


def test_live_adapter_fails_before_prepare_when_provider_profile_is_missing(
    tmp_path,
    capsys,
    monkeypatch,
):
    database = _database(tmp_path)
    monkeypatch.delenv("INTERVIEW_GENERATION_PROVIDER", raising=False)
    monkeypatch.setattr(
        cli.orchestrator,
        "prepare_posting",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must fail before prepare")),
    )

    exit_code, output = _invoke(
        capsys,
        "run-once",
        "--database",
        str(database),
        "--generator",
        cli.DEFAULT_LIVE_GENERATOR,
    )

    assert exit_code == 2
    assert output["error"] == "generation_provider_not_configured"
