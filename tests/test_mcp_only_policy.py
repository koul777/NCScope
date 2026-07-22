from __future__ import annotations

import json

from fastapi.testclient import TestClient

import app.main as main
from app.services import ncs_mcp_client


JD_TEXT = "\uc138\ubd84\ub958: \uacbd\uc601\uae30\ud68d\n\ub2f4\ub2f9\uc5c5\ubb34: \uacbd\uc601\uacc4\ud68d \uc218\ub9bd"


def _upload_files() -> dict:
    return {
        "jd_file": ("jd.txt", JD_TEXT.encode("utf-8"), "text/plain"),
        "notice_file": ("notice.txt", "\uba74\uc811\ud3c9\uac00: \ubb38\uc81c\ud574\uacb0\ub2a5\ub825".encode("utf-8"), "text/plain"),
    }


def _patch_mcp_upload_common(mocker) -> None:
    mocker.patch("app.main.extract_small_categories_from_jd", return_value=[])
    mocker.patch("app.main.extract_detail_categories_from_jd", return_value=[])
    mocker.patch("app.main.extract_subcategory_text", return_value="")
    mocker.patch(
        "app.main.resolve_sclass_candidates_bundle",
        return_value={
            "reverse_sclass_candidates": [],
            "direct_sclass_candidates_raw": [],
            "csv_sclass_candidates": [],
            "verified_sclass": [],
        },
    )
    mocker.patch("app.main.infer_keywords_from_subcategory_ai", return_value=[])
    mocker.patch("app.main.review_ocr_terms_with_openai", return_value=[])


def test_parse_review_returns_detail_candidates(mocker):
    mocker.patch("app.main.parse_with_kordoc", return_value={"markdown": JD_TEXT})
    mocker.patch(
        "app.main.structure_job_description",
        return_value={
            "document": {"markdown": JD_TEXT},
            "fields": {"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]},
        },
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.pdf", b"%PDF-test", "application/pdf")},
        )

    assert resp.status_code == 200
    assert resp.json()["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]


def test_mcp_only_requires_human_review_confirmation(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = {"review_confirmed": False, "fields": {"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 400
    assert "review_confirmed" in resp.text


def test_mcp_only_rejects_truthy_string_confirmation(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = {"review_confirmed": "false", "fields": {"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 400
    assert "review_confirmed" in resp.text


def test_mcp_only_requires_reviewed_detail_candidates(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = {"review_confirmed": True, "fields": {"ncs_detail_candidates": []}}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 422
    assert "detail candidates" in resp.text


def test_mcp_only_does_not_autofill_reviewed_detail_candidates(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    mocker.patch("app.main.extract_detail_categories_from_jd", return_value=["\uacbd\uc601\uae30\ud68d"])
    search = mocker.patch("app.main.search_units_by_detail", return_value=[])
    review = {"review_confirmed": True, "fields": {"ncs_detail_candidates": []}}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 422
    assert "detail candidates" in resp.text
    search.assert_not_called()


def test_mcp_only_success_uses_official_ksa(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    unit = {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "ncsSubdCdnm": "\uacbd\uc601\uae30\ud68d",
        "compeUnitDef": "\uacbd\uc601\ubaa9\ud45c\ub97c \uc218\ub9bd\ud55c\ub2e4",
        "score": 1.0,
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.search_units_by_detail", return_value=[unit])
    mocker.patch("app.main.rerank_ncs_matches", return_value=([unit], "rule"))
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    mocker.patch("app.main.build_jd_strategy_with_openai", return_value={"interview_questions": []})
    review = {"review_confirmed": True, "fields": {"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["jd_review_confirmed"] is True
    assert body["ncs_source"].startswith("ncs-mcp")
    assert body["ncs_ksa"][0]["factorSource"] == "ncs-mcp"
    assert body["ncs_ksa"][0]["ksaStatus"] == "official"


def test_mcp_search_matches_detail_not_small_category(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "small-only",
                    "text": "\uc18c\ubd84\ub958\ub9cc \uc77c\uce58",
                    "path": {"small": "\uacbd\uc601\uae30\ud68d", "sub": "\uacbd\uc601\ubd84\uc11d"},
                },
                {
                    "id": "sub-match",
                    "text": "\uc138\ubd84\ub958 \uc77c\uce58",
                    "path": {"small": "\uae30\ud68d\uc0ac\ubb34", "sub": "\uacbd\uc601\uae30\ud68d"},
                },
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail(["\uacbd\uc601\uae30\ud68d"])

    assert [row["ncsClCd"] for row in rows] == ["sub-match"]


def test_legacy_ncs_sclass_ksa_endpoint_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LEGACY_NCS_API", raising=False)

    with TestClient(main.app) as client:
        resp = client.get("/api/ncs/sclass/ksa?sclassName=\ucd1d\ubb34")

    assert resp.status_code == 410


def test_parse_review_rejects_large_upload_before_kordoc(monkeypatch, mocker):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    parse = mocker.patch("app.main.parse_with_kordoc")

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("large.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")},
        )

    assert resp.status_code == 413
    parse.assert_not_called()
