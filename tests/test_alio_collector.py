from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_alio_documents.py"
SPEC = importlib.util.spec_from_file_location("ncscope_collect_alio_documents", SCRIPT_PATH)
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


def test_date_windows_are_complete_non_overlapping_and_newest_first():
    windows = list(collector._date_windows(date(2025, 1, 1), date(2025, 2, 5), 10))

    assert windows[0] == (date(2025, 1, 27), date(2025, 2, 5))
    assert windows[-1][0] == date(2025, 1, 1)
    for previous, current in zip(windows, windows[1:]):
        assert current[1].toordinal() + 1 == previous[0].toordinal()


def test_profile_examples_require_one_explicit_official_sclass(tmp_path, monkeypatch):
    db_path = tmp_path / "corpus.sqlite3"
    with collector.sqlite3.connect(db_path) as connection:
        collector._schema(connection)
        connection.execute(
            "INSERT INTO postings(posting_id,url,discovered_at) VALUES('1','https://job.alio.go.kr/recruitview.do?idx=1','now')"
        )
        fields = {
            "ncs_detail_candidates": ["경영기획"],
            "ncs_detail_source": "explicit",
            "duties": ["경영계획 수립", "사업환경 분석"],
        }
        connection.execute(
            """
            INSERT INTO documents(posting_id,file_no,url,kind,filename,markdown,fields_json,status,updated_at)
            VALUES('1','11','https://www.alio.go.kr/download/download.json?fileNo=11','job_description','jd.txt','경영계획',?,'parsed','now')
            """,
            (collector._json(fields),),
        )
        connection.commit()
        examples = collector._profile_examples(connection)

    assert len(examples) == 1
    assert examples[0]["sclass_name"] == "경영기획"
    assert examples[0]["ncs_code_no"] == "020101"


def _insert_job_description(connection, *, file_no: str, fields: dict) -> int:
    connection.execute(
        "INSERT OR IGNORE INTO postings(posting_id,url,discovered_at) VALUES('1','https://job.alio.go.kr/recruitview.do?idx=1','now')"
    )
    cursor = connection.execute(
        """
        INSERT INTO documents(
            posting_id,file_no,url,kind,filename,sha256,markdown,fields_json,status,updated_at
        ) VALUES('1',?,?,'job_description','jd.txt',?,'body',?,'parsed','now')
        """,
        (
            file_no,
            f"https://www.alio.go.kr/download/download.json?fileNo={file_no}",
            f"sha-{file_no}",
            collector._json(fields),
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def _exact_result(*, unit_suffix: str = "01", expected: int = 1) -> dict:
    detail_code = "02020301"
    detail_name = "비서"
    base_code = f"{detail_code}{unit_suffix}"
    full_code = f"{base_code}_25v1"
    return {
        "items": [
            {
                "ncsClCd": full_code,
                "compeUnitName": f"비서 능력단위 {unit_suffix}",
                "officialUnitBaseCode": base_code,
                "officialUnitName": f"비서 능력단위 {unit_suffix}",
                "officialDetailCode": detail_code,
                "officialDetailName": detail_name,
                "ncsSubdCdnm": detail_name,
                "unitCatalogVerified": True,
                "unitVersionCompatible": False,
                "unitResolutionKind": "catalog_full_code_exact",
                "catalogUnitCodes": [full_code],
                "detailPathCodeVerified": True,
                "unitRetrievalKind": "official_detail_name_query",
                "unitRetrievalQuery": detail_name,
                "source": "ncs-mcp",
                "mcpUnitName": f"비서 능력단위 {unit_suffix}",
            }
        ],
        "exactCoverage": {
            "details": [
                {
                    "sourceDetailName": detail_name,
                    "mappingState": "official_detail_resolved",
                    "officialDetailCode": detail_code,
                    "officialDetailName": detail_name,
                    "detailExpectedUnitBaseCount": expected,
                    "detailVerifiedUnitBaseCount": 1,
                    "detailRetrievalComplete": expected == 1,
                    "detailRetrievalCapLimited": expected > 1,
                }
            ]
        },
    }


def _flattened_secretary_markdown() -> str:
    return (
        "NCS 분류체계 대분류 중분류 소분류 세분류 "
        "02. 경영·회계·사무 02. 총무·인사 03. 일반사무 01. 비서 "
        "주요사업 공공서비스와 조직 운영 지원"
    )


def _insert_reparse_fixture(connection, monkeypatch) -> int:
    document_id = _insert_job_description(
        connection,
        file_no="reparse-existing",
        fields={
            "ncs_detail_source": "explicit",
            "ncs_detail_candidates": ["비서", "obsolete detail"],
        },
    )
    connection.execute(
        """
        UPDATE documents
        SET markdown=?,parser='hwp_text_fallback',parser_version='',updated_at='before'
        WHERE id=?
        """,
        (_flattened_secretary_markdown(), document_id),
    )
    connection.commit()
    monkeypatch.setattr(
        collector.ncs_mcp_client,
        "search_units_by_detail_result",
        lambda *_args, **_kwargs: _exact_result(),
    )
    collector.persist_document_ncs_matches(
        connection,
        document_ids=[document_id],
    )
    return document_id


def test_ncs_match_persistence_upserts_and_atomically_replaces_stale_units(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "corpus.sqlite3"
    with collector.sqlite3.connect(db_path) as connection:
        collector._schema(connection)
        document_id = _insert_job_description(
            connection,
            file_no="21",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": ["비서"]},
        )
        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: _exact_result(unit_suffix="01", expected=2),
        )
        first = collector.persist_document_ncs_matches(connection)

        assert first["incomplete"] == 1
        match = connection.execute(
            """
            SELECT status,official_detail_code,official_detail_name,
                   expected_unit_count,verified_unit_count,persisted_unit_count,
                   retrieval_complete,retrieval_cap_limited,matcher_version
            FROM document_ncs_matches WHERE document_id=?
            """,
            (document_id,),
        ).fetchone()
        assert match == (
            "incomplete",
            "02020301",
            "비서",
            2,
            1,
            1,
            0,
            1,
            collector.NCS_MATCHER_VERSION,
        )
        first_unit = connection.execute(
            """
            SELECT official_unit_base_code,official_unit_full_code,
                   official_unit_name,ownership_detail_code,
                   catalog_source,catalog_resolution_kind,unit_catalog_verified
            FROM document_ncs_units
            """
        ).fetchone()
        assert first_unit == (
            "0202030101",
            "0202030101_25v1",
            "비서 능력단위 01",
            "02020301",
            "bundled_ncs_unit_catalog",
            "catalog_full_code_exact",
            1,
        )

        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: _exact_result(unit_suffix="02"),
        )
        second = collector.persist_document_ncs_matches(connection)

        assert second["exact_complete"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM document_ncs_matches WHERE document_id=?",
            (document_id,),
        ).fetchone()[0] == 1
        units = connection.execute(
            "SELECT official_unit_base_code FROM document_ncs_units"
        ).fetchall()
        assert units == [("0202030102",)]


def test_contextual_and_empty_details_are_review_required_without_mcp(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "corpus.sqlite3"
    with collector.sqlite3.connect(db_path) as connection:
        collector._schema(connection)
        _insert_job_description(
            connection,
            file_no="31",
            fields={"ncs_detail_source": "contextual", "ncs_detail_candidates": ["비서"]},
        )
        _insert_job_description(
            connection,
            file_no="32",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": []},
        )

        def unexpected(*_args, **_kwargs):
            raise AssertionError("review-only inputs must not call MCP")

        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            unexpected,
        )
        summary = collector.persist_document_ncs_matches(connection)

        assert summary["review_required"] == 2
        rows = connection.execute(
            """
            SELECT source_detail,ncs_detail_source,status,official_detail_code
            FROM document_ncs_matches ORDER BY document_id
            """
        ).fetchall()
        assert rows == [
            ("비서", "contextual", "review_required", ""),
            ("", "explicit", "review_required", ""),
        ]


def test_ncs_mcp_error_preserves_same_input_selection_and_marks_retryable(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "corpus.sqlite3"
    with collector.sqlite3.connect(db_path) as connection:
        collector._schema(connection)
        document_id = _insert_job_description(
            connection,
            file_no="41",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": ["비서"]},
        )
        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: _exact_result(),
        )
        collector.persist_document_ncs_matches(connection)

        def unavailable(*_args, **_kwargs):
            raise collector.ncs_mcp_client.NcsMcpError("temporary outage")

        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            unavailable,
        )
        summary = collector.persist_document_ncs_matches(connection)

        assert summary["mcp_unavailable"] == 1
        assert summary["last_success_preserved"] == 1
        match = connection.execute(
            """
            SELECT status,last_success_status,retryable,official_detail_code,
                   official_detail_name,persisted_unit_count,error,
                   last_success_source_signature,last_success_matcher_version,
                   source_signature,matcher_version
            FROM document_ncs_matches WHERE document_id=?
            """,
            (document_id,),
        ).fetchone()
        assert match[:6] == (
            "mcp_unavailable",
            "exact_complete",
            1,
            "02020301",
            "비서",
            1,
        )
        assert match[6] == "NCS MCP unavailable"
        assert match[7] == match[9]
        assert match[8] == match[10] == collector.NCS_MATCHER_VERSION
        assert connection.execute(
            "SELECT COUNT(*) FROM document_ncs_units"
        ).fetchone()[0] == 1


def test_matcher_version_change_changes_signature_and_preserves_prior_exact_on_outage(
    tmp_path, monkeypatch
):
    detail_name = _exact_result()["exactCoverage"]["details"][0]["sourceDetailName"]
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        document_id = _insert_job_description(
            connection,
            file_no="matcher-version",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": [detail_name]},
        )
        calls = []

        def exact(*_args, **_kwargs):
            calls.append("v1")
            return _exact_result()

        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            exact,
        )
        collector.persist_document_ncs_matches(connection, matcher_version="matcher-v1")
        prior_signature = connection.execute(
            "SELECT source_signature FROM document_ncs_matches WHERE document_id=?",
            (document_id,),
        ).fetchone()[0]

        def unavailable(*_args, **_kwargs):
            calls.append("v2")
            raise collector.ncs_mcp_client.NcsMcpError("temporary outage")

        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            unavailable,
        )
        summary = collector.persist_document_ncs_matches(
            connection,
            matcher_version="matcher-v2",
        )

        row = connection.execute(
            """
            SELECT status,source_signature,matcher_version,
                   last_success_status,last_success_source_signature,
                   last_success_matcher_version,official_detail_code
            FROM document_ncs_matches WHERE document_id=?
            """,
            (document_id,),
        ).fetchone()
        assert calls == ["v1", "v2"]
        assert row[0] == "mcp_unavailable"
        assert row[1] != prior_signature
        assert row[2] == "matcher-v2"
        assert row[3:6] == ("exact_complete", prior_signature, "matcher-v1")
        assert row[6] == "02020301"
        assert summary["last_success_preserved"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM document_ncs_units"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("failed_result", "expected_status", "expected_retryable"),
    [
        (_exact_result(unit_suffix="01", expected=2), "incomplete", 1),
        (
            {
                "items": [],
                "exactCoverage": {
                    "details": [
                        {
                            "sourceDetailName": _exact_result()["exactCoverage"]["details"][0]["sourceDetailName"],
                            "mappingState": "unresolved",
                        }
                    ]
                },
            },
            "unresolved",
            0,
        ),
    ],
)
def test_failed_rematch_keeps_prior_exact_evidence_but_exposes_current_failure(
    tmp_path,
    monkeypatch,
    failed_result,
    expected_status,
    expected_retryable,
):
    detail_name = _exact_result()["exactCoverage"]["details"][0]["sourceDetailName"]
    with collector.sqlite3.connect(tmp_path / f"{expected_status}.sqlite3") as connection:
        collector._schema(connection)
        document_id = _insert_job_description(
            connection,
            file_no=f"preserve-{expected_status}",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": [detail_name]},
        )
        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: _exact_result(unit_suffix="02"),
        )
        collector.persist_document_ncs_matches(connection)
        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: failed_result,
        )

        summary = collector.persist_document_ncs_matches(connection)

        row = connection.execute(
            """
            SELECT status,last_success_status,retryable,official_detail_code,
                   persisted_unit_count,matched_at,error
            FROM document_ncs_matches WHERE document_id=?
            """,
            (document_id,),
        ).fetchone()
        assert row[0:5] == (
            expected_status,
            "exact_complete",
            expected_retryable,
            "02020301",
            1,
        )
        assert row[5]
        assert row[6]
        assert summary["last_success_preserved"] == 1
        assert connection.execute(
            "SELECT official_unit_base_code FROM document_ncs_units"
        ).fetchall() == [("0202030102",)]


def test_persistence_entrypoint_enables_sqlite_foreign_keys(tmp_path):
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        connection.execute("PRAGMA foreign_keys=OFF")
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0

        summary = collector.persist_document_ncs_matches(connection)

        assert summary["documents"] == 0
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_dotenv_loader_reads_only_credential_free_ncs_mcp_url(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=must-not-load\nNCS_MCP_URL='http://127.0.0.1:8778/mcp'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("NCS_MCP_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert collector._load_ncs_mcp_url_from_dotenv(env_path) is True
    assert collector.os.environ["NCS_MCP_URL"] == "http://127.0.0.1:8778/mcp"
    assert "OPENAI_API_KEY" not in collector.os.environ


def test_ncs_match_schema_has_audit_indexes(tmp_path):
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(document_ncs_matches)")
        }
        unit_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(document_ncs_units)")
        }

    assert "idx_document_ncs_matches_official_detail" in indexes
    assert "idx_document_ncs_matches_retryable" in indexes
    assert "idx_document_ncs_units_detail_base" in unit_indexes


def test_ncs_match_persistence_can_target_one_existing_document(tmp_path, monkeypatch):
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        first_id = _insert_job_description(
            connection,
            file_no="target-1",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": ["비서"]},
        )
        second_id = _insert_job_description(
            connection,
            file_no="target-2",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": ["비서"]},
        )
        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: _exact_result(),
        )

        summary = collector.persist_document_ncs_matches(
            connection,
            document_ids=[second_id],
        )

        assert summary["documents"] == 1
        assert summary["exact_complete"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM document_ncs_matches WHERE document_id=?",
            (first_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM document_ncs_matches WHERE document_id=?",
            (second_id,),
        ).fetchone()[0] == "exact_complete"


def test_format_variant_owner_name_is_not_dropped_after_gate(tmp_path, monkeypatch):
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        document_id = _insert_job_description(
            connection,
            file_no="punctuation-owner",
            fields={
                "ncs_detail_source": "explicit",
                "ncs_detail_candidates": ["회계·감사"],
            },
        )
        result = _exact_result()
        detail = result["exactCoverage"]["details"][0]
        detail["sourceDetailName"] = "회계·감사"
        detail["officialDetailName"] = "회계·감사"
        unit = result["items"][0]
        unit["officialDetailName"] = "회계·감사"
        unit["ncsSubdCdnm"] = "회계.감사"
        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: result,
        )

        summary = collector.persist_document_ncs_matches(connection)

        assert summary["exact_complete"] == 1
        assert connection.execute(
            "SELECT status,persisted_unit_count FROM document_ncs_matches WHERE document_id=?",
            (document_id,),
        ).fetchone() == ("exact_complete", 1)


def test_gate_complete_is_downgraded_if_persistence_drops_a_unit(tmp_path, monkeypatch):
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        document_id = _insert_job_description(
            connection,
            file_no="dropped-unit",
            fields={"ncs_detail_source": "explicit", "ncs_detail_candidates": ["비서"]},
        )
        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            lambda *_args, **_kwargs: _exact_result(),
        )
        monkeypatch.setattr(collector, "_verified_ncs_unit_rows", lambda *_args, **_kwargs: [])

        summary = collector.persist_document_ncs_matches(connection)

        assert summary["incomplete"] == 1
        assert connection.execute(
            "SELECT status,persisted_unit_count,error FROM document_ncs_matches WHERE document_id=?",
            (document_id,),
        ).fetchone() == ("incomplete", 0, "persisted_unit_rows_dropped")


def test_reparse_existing_dry_run_reports_changes_without_writing(tmp_path, monkeypatch):
    db_path = tmp_path / "corpus.sqlite3"
    with collector.sqlite3.connect(db_path) as connection:
        collector._schema(connection)
        document_id = _insert_reparse_fixture(connection, monkeypatch)
        before_document = connection.execute(
            "SELECT sha256,markdown,fields_json,updated_at FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()
        before_matches = connection.execute(
            """
            SELECT source_detail_index,source_detail,status,source_signature
            FROM document_ncs_matches WHERE document_id=? ORDER BY source_detail_index
            """,
            (document_id,),
        ).fetchall()

        summary = collector.reparse_existing_job_descriptions(
            connection,
            dry_run=True,
        )

        assert summary == {
            "documents": 1,
            "changed": 1,
            "unchanged": 0,
            "obsolete_matches": 1,
            "preservable_exact_matches": 1,
            "dry_run": 1,
            "changed_document_ids": [document_id],
        }
        assert connection.execute(
            "SELECT sha256,markdown,fields_json,updated_at FROM documents WHERE id=?",
            (document_id,),
        ).fetchone() == before_document
        assert connection.execute(
            """
            SELECT source_detail_index,source_detail,status,source_signature
            FROM document_ncs_matches WHERE document_id=? ORDER BY source_detail_index
            """,
            (document_id,),
        ).fetchall() == before_matches


def test_reparse_then_outage_preserves_exact_and_removes_obsolete_match(
    tmp_path,
    monkeypatch,
):
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        document_id = _insert_reparse_fixture(connection, monkeypatch)
        old_match = connection.execute(
            """
            SELECT source_signature,source_fields_sha256
            FROM document_ncs_matches
            WHERE document_id=? AND source_detail_index=0
            """,
            (document_id,),
        ).fetchone()
        source_before = connection.execute(
            "SELECT sha256,markdown FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()

        reparse = collector.reparse_existing_job_descriptions(connection)
        reparsed_document = connection.execute(
            "SELECT sha256,markdown,fields_json FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()
        reparsed_fields = json.loads(reparsed_document[2])
        assert reparse["changed_document_ids"] == [document_id]
        assert reparsed_document[:2] == source_before
        assert reparsed_fields["ncs_detail_source"] == "explicit"
        assert reparsed_fields["ncs_detail_candidates"] == ["비서"]

        def unavailable(*_args, **_kwargs):
            raise collector.ncs_mcp_client.NcsMcpError("temporary outage")

        monkeypatch.setattr(
            collector.ncs_mcp_client,
            "search_units_by_detail_result",
            unavailable,
        )
        match_summary = collector.persist_document_ncs_matches(
            connection,
            document_ids=reparse["changed_document_ids"],
        )

        current = connection.execute(
            """
            SELECT status,last_success_status,source_signature,
                   last_success_source_signature,source_fields_sha256,
                   source_document_sha256,official_detail_code
            FROM document_ncs_matches
            WHERE document_id=? AND source_detail_index=0
            """,
            (document_id,),
        ).fetchone()
        assert current[0:2] == ("mcp_unavailable", "exact_complete")
        assert current[2] != old_match[0]
        assert current[3] == old_match[0]
        assert current[4] != old_match[1]
        assert current[5] == source_before[0]
        assert current[6] == "02020301"
        assert match_summary["last_success_preserved"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM document_ncs_matches WHERE document_id=?",
            (document_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM document_ncs_units",
        ).fetchone()[0] == 1


def test_reparse_existing_is_all_or_nothing_when_structuring_fails(
    tmp_path,
    monkeypatch,
):
    with collector.sqlite3.connect(tmp_path / "corpus.sqlite3") as connection:
        collector._schema(connection)
        first_id = _insert_job_description(
            connection,
            file_no="reparse-atomic-1",
            fields={"ncs_detail_candidates": []},
        )
        second_id = _insert_job_description(
            connection,
            file_no="reparse-atomic-2",
            fields={"ncs_detail_candidates": []},
        )
        before = connection.execute(
            "SELECT id,fields_json,updated_at FROM documents ORDER BY id"
        ).fetchall()
        calls = 0

        def structure(_parsed, filename=""):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("structure failed")
            return {
                "fields": {
                    "ncs_detail_source": "explicit",
                    "ncs_detail_candidates": ["비서"],
                }
            }

        monkeypatch.setattr(collector, "structure_job_description", structure)

        with pytest.raises(RuntimeError, match="structure failed"):
            collector.reparse_existing_job_descriptions(
                connection,
                document_ids=[first_id, second_id],
            )

        assert connection.execute(
            "SELECT id,fields_json,updated_at FROM documents ORDER BY id"
        ).fetchall() == before


def test_reparse_cli_dry_run_uses_read_only_database(tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "corpus"
    out_dir.mkdir()
    db_path = out_dir / "alio_corpus.sqlite3"
    with collector.sqlite3.connect(db_path) as connection:
        collector._schema(connection)
        document_id = _insert_reparse_fixture(connection, monkeypatch)
        before = connection.execute(
            "SELECT fields_json,updated_at FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()

    result = collector.main(
        [
            "--out-dir",
            str(out_dir),
            "--reparse-existing-job-descriptions",
            "--dry-run",
        ]
    )

    assert result == 0
    assert '"dry_run": true' in capsys.readouterr().out
    with collector.sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT fields_json,updated_at FROM documents WHERE id=?",
            (document_id,),
        ).fetchone() == before
        assert connection.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 0
    assert not (out_dir / "summary.json").exists()
    assert not (out_dir / "sclass_profiles.json").exists()
