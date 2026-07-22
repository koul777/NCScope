from __future__ import annotations

import json

from fastapi.testclient import TestClient

import app.main as main


def test_jd_strategy_upload_no_nameerror_regression(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    mocker.patch("app.main.init_db", return_value=None)
    mocker.patch("app.main.start_auto_runner", return_value=None)
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
    unit = {
        "ncsClCd": "0202010101_22v2",
        "compeUnitName": "총무 업무 지원",
        "ncsSubdCdnm": "총무",
        "compeUnitDef": "총무 업무를 수행한다.",
        "score": 1.0,
    }
    mocker.patch("app.main.search_units_by_detail", return_value=[unit])
    mocker.patch("app.main.rerank_ncs_matches", return_value=([unit], "rule"))
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    mocker.patch("app.main.build_jd_strategy_with_openai", return_value={"interview_questions": []})
    jd_text = "총무 및 자산관리 업무"
    structured = {"document": {"markdown": jd_text}, "fields": {"ncs_detail_candidates": ["총무"]}}
    session = main._create_review_session(jd_text.encode("utf-8"), structured, "jd.txt")
    review = {**structured, "review_confirmed": True, "review_session_id": session["id"], "review_session": session}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("jd.txt", jd_text, "text/plain")},
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline_mode"] == "direct-ncs"
    assert body["ncs_source"].startswith("ncs-mcp")
    assert "strategy" in body
