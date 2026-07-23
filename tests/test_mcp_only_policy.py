from __future__ import annotations

import json
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services import ncs_mcp_client
from app.services.jd_strategy import fetch_ncs_ksa_by_units


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


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _mark_zip_encrypted(data: bytes) -> bytes:
    blob = bytearray(data)
    local_sig = b"PK\x03\x04"
    central_sig = b"PK\x01\x02"
    start = 0
    while True:
        idx = blob.find(local_sig, start)
        if idx < 0:
            break
        flags = int.from_bytes(blob[idx + 6 : idx + 8], "little") | 0x1
        blob[idx + 6 : idx + 8] = flags.to_bytes(2, "little")
        start = idx + 4
    start = 0
    while True:
        idx = blob.find(central_sig, start)
        if idx < 0:
            break
        flags = int.from_bytes(blob[idx + 8 : idx + 10], "little") | 0x1
        blob[idx + 8 : idx + 10] = flags.to_bytes(2, "little")
        start = idx + 4
    return bytes(blob)


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


def test_parse_review_accepts_zip_with_supported_jd_text():
    data = _zip_bytes({"job_description.txt": JD_TEXT})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.zip", data, "application/zip")},
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]
    assert "ZIP member: job_description.txt" in body["document"]["markdown"]


def test_parse_review_accepts_zip_with_supported_jd_image(mocker):
    data = _zip_bytes({"job_description.jpg": "fake image bytes"})
    parse = mocker.patch("app.main.parse_with_kordoc", return_value={"markdown": JD_TEXT})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.zip", data, "application/zip")},
        )

    body = resp.json()
    assert resp.status_code == 200
    assert body["fields"]["ncs_detail_candidates"] == ["\uacbd\uc601\uae30\ud68d"]
    assert "ZIP member: job_description.jpg" in body["document"]["markdown"]
    parse.assert_called_once()


def test_parse_review_rejects_encrypted_zip_as_422():
    data = _mark_zip_encrypted(_zip_bytes({"job_description.txt": JD_TEXT}))

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/parse-review",
            files={"jd_file": ("jd.zip", data, "application/zip")},
        )

    assert resp.status_code == 422
    assert "no parseable" in resp.text or "encrypted" in resp.text


def _confirmed_review_payload(fields: dict, confirmed: object = True, jd_text: str = JD_TEXT) -> dict:
    structured = {"document": {"markdown": jd_text}, "fields": fields}
    session = main._create_review_session(jd_text.encode("utf-8"), structured, "jd.txt")
    return {
        **structured,
        "review_confirmed": confirmed,
        "review_session_id": session["id"],
        "review_session": session,
    }


def test_notice_parse_review_prefills_duty_and_evaluation_text():
    notice = (
        "## 담당업무\n"
        "- 경영계획 수립 및 사업성과 분석\n"
        "## 면접전형 평가항목\n"
        "- 문제해결능력\n"
        "- 의사소통능력\n"
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/notice/parse-review",
            files={"notice_file": ("notice.txt", notice.encode("utf-8"), "text/plain")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "경영계획 수립" in body["fields"]["duty_text"]
    assert "문제해결능력" in body["fields"]["evaluation_text"]


def test_mcp_only_requires_human_review_confirmation(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}, confirmed=False)

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
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}, confirmed="false")

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 400
    assert "review_confirmed" in resp.text


def test_mcp_only_requires_server_review_session(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = {"review_confirmed": True, "fields": {"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]}}

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 400
    assert "review_session_id" in resp.text


def test_mcp_only_requires_reviewed_detail_candidates(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = _confirmed_review_payload({"ncs_detail_candidates": []})

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
    review = _confirmed_review_payload({"ncs_detail_candidates": []})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    assert resp.status_code == 422
    assert "detail candidates" in resp.text
    search.assert_not_called()


def test_mcp_only_returns_manual_suggestions_when_detail_has_no_exact_match(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    mocker.patch("app.main.search_units_by_detail", return_value=[])
    suggestion = {
        "ncsClCd": "0601010101_20v1",
        "compeUnitName": "\uc758\ub8cc\uc9c0\uc6d0 \ud6c4\ubcf4",
        "ncsSubdCdnm": "\uc758\ub8cc\uae30\uae30\uad00\ub9ac",
        "source": "ncs-mcp-suggest",
        "isExactDetailMatch": False,
    }
    suggest = mocker.patch("app.main.suggest_units_by_text", return_value=[suggestion])
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uc784\uc0c1\ubcd1\ub9ac"]})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    body = resp.json()
    assert resp.status_code == 422
    suggest.assert_called_once_with(["\uc784\uc0c1\ubcd1\ub9ac"], max_units=12)
    assert body["detail"]["lookup_terms"] == ["\uc784\uc0c1\ubcd1\ub9ac"]
    assert body["detail"]["suggested_ncs_units"] == [suggestion]
    assert "exact competency units" in body["detail"]["message"]


def test_mcp_only_rejects_partial_detail_exact_coverage(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    matched_unit = {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "ncsSubdCdnm": "\uacbd\uc601\uae30\ud68d",
        "matchedDetailName": "\uacbd\uc601\uae30\ud68d",
        "source": "ncs-mcp",
    }
    mocker.patch("app.main.search_units_by_detail", return_value=[matched_unit])
    suggestion = {
        "ncsClCd": "0601010801_23v3",
        "compeUnitName": "\uc9c4\ub8cc\uc9c0\uc6d0\ubcf4\uc870",
        "ncsSubdCdnm": "\uc694\uc591\uc9c0\uc6d0",
        "source": "ncs-mcp-suggest",
    }
    suggest = mocker.patch("app.main.suggest_units_by_text", return_value=[suggestion])
    review = _confirmed_review_payload(
        {
            "ncs_detail_candidates": [
                "\uacbd\uc601\uae30\ud68d",
                "\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870",
            ]
        }
    )

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={"jd_review_json": json.dumps(review, ensure_ascii=False)},
        )

    body = resp.json()
    assert resp.status_code == 422
    suggest.assert_called_once_with(["\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870"], max_units=12)
    assert body["detail"]["matched_detail_terms"] == ["\uacbd\uc601\uae30\ud68d"]
    assert body["detail"]["unmatched_detail_terms"] == ["\uac04\ud638\uc5c5\ubb34 \ubcf4\uc870"]
    assert body["detail"]["suggested_ncs_units"] == [suggestion]
    assert "partial exact coverage" in body["detail"]["message"]


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
    rerank = mocker.patch("app.main.rerank_ncs_matches", return_value=([unit], "rule"))
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    rank_ksa = mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value={
            "interview_questions": [
                {
                    "question": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd \uc2dc \uc2dc\uc7a5\ud658\uacbd\uc744 \uc5b4\ub5bb\uac8c \ubd84\uc11d\ud558\uaca0\uc2b5\ub2c8\uae4c?",
                    "type": "\uc9c1\ubb34\uc9c0\uc2dd",
                    "competency": unit["compeUnitName"],
                    "ncsClCd": unit["ncsClCd"],
                    "follow_ups": ["\ubd84\uc11d \uadfc\uac70\ub294?", "\uc704\ud5d8\uc694\uc778\uc740?", "\uac1c\uc120\uc810\uc740?"],
                    "evaluation_points": ["\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d", "\uadfc\uac70 \uc81c\uc2dc", "\ub300\uc548 \ube44\uad50", "\uc2e4\ud589\uacc4\ud68d"],
                }
            ]
        },
    )
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]})
    request_key = "sk-test-ncscope-request-key"

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "duty_text": "duty: stakeholder workshop planning",
                "qualification_text": "\uc9c0\uc6d0\uc790\uaca9: \uad00\ub828 \ubd84\uc57c \uc2e4\ubb34\uacbd\ub825 3\ub144 \uc774\uc0c1",
                "preference_text": "\uc6b0\ub300\uc0ac\ud56d: \uacf5\uacf5\uae30\uad00 \uc0ac\uc5c5\uad00\ub9ac \uacbd\ud5d8",
                "evaluation_text": "evaluation: issue framing",
                "openai_api_key": request_key,
            },
        )

    body = resp.json()
    assert resp.status_code == 200
    rerank.assert_called_once()
    assert rerank.call_args.kwargs["openai_api_key"] == request_key
    rank_ksa.assert_called_once()
    ksa_query_text = rank_ksa.call_args.kwargs["query_text"]
    assert "duty: stakeholder workshop planning" in ksa_query_text
    assert "\uc2e4\ubb34\uacbd\ub825" in ksa_query_text
    assert "\uc0ac\uc5c5\uad00\ub9ac" in ksa_query_text
    assert "evaluation: issue framing" in ksa_query_text
    assert "\uacbd\uc601\uae30\ud68d" in ksa_query_text
    build_strategy.assert_called_once()
    assert build_strategy.call_args.kwargs["api_key_override"] == request_key
    assert request_key not in resp.text
    assert body["jd_review_confirmed"] is True
    assert "\uc2e4\ubb34\uacbd\ub825" in body["qualification_text_preview"]
    assert "\uc0ac\uc5c5\uad00\ub9ac" in body["preference_text_preview"]
    assert body["ncs_source"].startswith("ncs-mcp")
    assert body["ncs_ksa"][0]["factorSource"] == "ncs-mcp"
    assert body["ncs_ksa"][0]["ksaStatus"] == "official"
    question = body["strategy"]["interview_questions"][0]
    assert question["ksa_refs"] == ["\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d"]
    assert question["ksa_evidence"][0]["factorSource"] == "ncs-mcp"
    assert question["ksa_evidence"][0]["ksaStatus"] == "official"


def test_upload_rejects_invalid_request_openai_key(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    _patch_mcp_upload_common(mocker)
    review = _confirmed_review_payload({"ncs_detail_candidates": ["\uacbd\uc601\uae30\ud68d"]})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files=_upload_files(),
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "openai_api_key": "sk-test invalid",
            },
        )

    assert resp.status_code == 400
    assert "openai_api_key" in resp.text


def test_generate_from_text_passes_request_openai_key(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    unit = {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd",
        "compeUnitLevel": "5",
        "ncsSubdCdnm": "\uacbd\uc601\uae30\ud68d",
        "compeUnitDef": "\uacbd\uc601\ubaa9\ud45c\ub97c \uc218\ub9bd\ud55c\ub2e4",
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "\uc2dc\uc7a5\ud658\uacbd \ubd84\uc11d",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    rank_ksa = mocker.patch("app.main.rank_ksa_factors_by_query", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch("app.main.build_jd_strategy_with_openai", return_value={"interview_questions": []})
    request_key = "sk-test-manual-request-key"

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-from-text",
            json={
                "notice_text": "\uacbd\uc601\uae30\ud68d \ub2f4\ub2f9\uc5c5\ubb34",
                "duty_text": "duty: board reporting and KPI dashboard",
                "evaluation_text": "\ubb38\uc81c\ud574\uacb0\ub2a5\ub825",
                "selected_ncs": [unit],
                "question_plan": {
                    "items": [
                        {"detail": "\uacbd\uc601\uae30\ud68d", "enabled": True, "main_count": 2, "follow_up_count": 4}
                    ]
                },
                "interview_methods": ["\ubc1c\ud45c\uba74\uc811", "\ud1a0\ub860\uba74\uc811"],
                "openai_api_key": request_key,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["openai_key_source"] == "request"
    assert body["question_plan"]["total_main_count"] == 2
    assert body["question_plan"]["follow_up_count"] == 4
    assert body["interview_methods"] == ["\ubc1c\ud45c\uba74\uc811", "\ud1a0\ub860\uba74\uc811"]
    rank_ksa.assert_called_once()
    ksa_query_text = rank_ksa.call_args.kwargs["query_text"]
    assert "duty: board reporting and KPI dashboard" in ksa_query_text
    assert "\ubb38\uc81c\ud574\uacb0\ub2a5\ub825" in ksa_query_text
    assert "\uacbd\uc601\uae30\ud68d \ub2f4\ub2f9\uc5c5\ubb34" in ksa_query_text
    build_strategy.assert_called_once()
    kwargs = build_strategy.call_args.kwargs
    assert kwargs["api_key_override"] == request_key
    assert kwargs["target_count_override"] == 2
    assert kwargs["follow_up_count"] == 4
    assert kwargs["question_plan"]["selected_terms"] == ["\uacbd\uc601\uae30\ud68d"]
    assert kwargs["interview_methods"] == ["\ubc1c\ud45c\uba74\uc811", "\ud1a0\ub860\uba74\uc811"]
    assert request_key not in resp.text


def test_generate_from_text_restricts_stale_question_plan_to_selected_ncs(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    unit = {
        "ncsClCd": "0202030201_25v3",
        "compeUnitName": "\ubb38\uc11c\uc791\uc131",
        "compeUnitLevel": "3",
        "ncsSubdCdnm": "\uc0ac\ubb34\ud589\uc815",
        "compeUnitDef": "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d\uc744 \ud30c\uc545\ud558\uc5ec \ubb38\uc11c\ub97c \uc791\uc131\ud55c\ub2e4",
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "\ubb38\uc11c \uc694\uad6c\uc0ac\ud56d \ud30c\uc545",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    mocker.patch("app.main.fetch_ncs_ksa_by_units", return_value=[ksa])
    mocker.patch("app.main.build_ncs_context_pack", return_value={})
    build_strategy = mocker.patch("app.main.build_jd_strategy_with_openai", return_value={"interview_questions": []})

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/questions/generate-from-text",
            json={
                "notice_text": "\uc0ac\ubb34\ud589\uc815 \ub2f4\ub2f9\uc5c5\ubb34",
                "selected_ncs": [unit],
                "question_plan": {
                    "items": [
                        {"detail": "\ub2e4\ub978\uc138\ubd84\ub958", "enabled": True, "main_count": 9, "follow_up_count": 5}
                    ]
                },
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["question_plan"]["selected_terms"] == ["\uc0ac\ubb34\ud589\uc815"]
    assert body["question_plan"]["total_main_count"] == 3
    assert body["question_plan"]["follow_up_count"] == 5
    kwargs = build_strategy.call_args.kwargs
    assert kwargs["question_plan"]["selected_terms"] == ["\uc0ac\ubb34\ud589\uc815"]
    assert kwargs["target_count_override"] == 3


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


def test_mcp_search_normalizes_middle_dot_and_spacing_variants(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "dot-match",
                    "text": "일식 복어조리",
                    "path": {"small": "음식조리", "sub": "일식·복어조리"},
                }
            ]
        },
    )

    rows = ncs_mcp_client.search_units_by_detail(["일식· 복어・조리"])

    assert [row["ncsClCd"] for row in rows] == ["dot-match"]


def test_mcp_search_resolves_safe_detail_alias(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        calls.append(arguments["query"])
        if arguments["query"] == "건축공사감리":
            return {
                "results": [
                    {
                        "id": "alias-match",
                        "text": "공사착공관리",
                        "path": {"small": "건축설계·감리", "sub": "건축공사감리"},
                    }
                ]
            }
        return {"results": []}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["건축감리"], max_units=5)

    assert calls == ["건축감리", "건축공사감리"]
    assert [row["ncsClCd"] for row in rows] == ["alias-match"]
    assert rows[0]["matchedDetailName"] == "건축감리"
    assert rows[0]["resolvedDetailName"] == "건축공사감리"
    assert rows[0]["detailQueryName"] == "건축공사감리"
    assert rows[0]["source"] == "ncs-mcp-detail-alias"


def test_mcp_search_does_not_apply_alias_after_exact_detail_match(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        calls.append(arguments["query"])
        if arguments["query"] == "건축공사감리":
            raise AssertionError("alias query should not run after an exact 세분류 match")
        return {
            "results": [
                {
                    "id": "exact-match",
                    "text": "건축감리 수행",
                    "path": {"small": "건축설계·감리", "sub": "건축감리"},
                }
            ]
        }

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["건축감리"], max_units=5)

    assert calls == ["건축감리"]
    assert [row["ncsClCd"] for row in rows] == ["exact-match"]
    assert rows[0]["matchedDetailName"] == "건축감리"
    assert rows[0]["resolvedDetailName"] == ""
    assert rows[0]["detailQueryName"] == ""
    assert rows[0]["source"] == "ncs-mcp"


def test_mcp_search_uses_wider_window_for_truncated_exact_detail(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    calls = []

    def fake_call_tool(name, arguments):
        calls.append(arguments)
        if arguments["limit"] <= 50:
            return {"results": []}
        broad_rows = [
            {
                "id": f"broad-{idx}",
                "text": "시설물안전관리",
                "path": {"small": "총무", "sub": "자산관리"},
            }
            for idx in range(60)
        ]
        exact_rows = [
            {
                "id": "1401030101_25v3",
                "text": "유지관리 계획수립",
                "path": {"small": "건설시공후관리", "sub": "유지관리"},
            }
        ]
        return {"results": broad_rows + exact_rows}

    mocker.patch("app.services.ncs_mcp_client._call_tool", side_effect=fake_call_tool)

    rows = ncs_mcp_client.search_units_by_detail(["유지관리"], max_units=8)

    assert calls[0]["limit"] > 50
    assert [row["ncsClCd"] for row in rows] == ["1401030101_25v3"]
    assert rows[0]["ncsSubdCdnm"] == "유지관리"
    assert rows[0]["source"] == "ncs-mcp"


def test_mcp_suggest_units_by_text_keeps_non_exact_candidates(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "suggested-unit",
                    "text": "\uc784\uc0c1\ubcd1\ub9ac \uad00\ub828 \uc9c8\ubcd1\uc9c4\ub2e8",
                    "path": {"small": "\ucd95\uc0b0\uc790\uc6d0\uac1c\ubc1c", "sub": "\uc218\uc758\uc11c\ube44\uc2a4"},
                    "score": 0.42,
                }
            ]
        },
    )

    rows = ncs_mcp_client.suggest_units_by_text(["\uc784\uc0c1\ubcd1\ub9ac"], max_units=5)

    assert rows[0]["ncsClCd"] == "suggested-unit"
    assert rows[0]["source"] == "ncs-mcp-suggest"
    assert rows[0]["isExactDetailMatch"] is False
    assert rows[0]["isExactUnitNameMatch"] is False
    assert rows[0]["canonicalDetailName"] == "\uc218\uc758\uc11c\ube44\uc2a4"


def test_mcp_suggest_units_by_text_marks_exact_unit_name_match(mocker):
    mocker.patch("app.services.ncs_mcp_client._tool_names", return_value={"ncs_search"})
    mocker.patch(
        "app.services.ncs_mcp_client._call_tool",
        return_value={
            "results": [
                {
                    "id": "unit-name-match",
                    "text": "\uce74\uc9c0\ub178 \uace0\uac1d \uc9c0\uc6d0",
                    "path": {"small": "\uad00\uad11\ub808\uc800\uc11c\ube44\uc2a4", "sub": "\uce74\uc9c0\ub178\uc6b4\uc601\uad00\ub9ac"},
                    "score": 0.0,
                }
            ]
        },
    )

    rows = ncs_mcp_client.suggest_units_by_text(["\uce74\uc9c0\ub178 \uace0\uac1d \uc9c0\uc6d0"], max_units=5)

    assert rows[0]["ncsClCd"] == "unit-name-match"
    assert rows[0]["isExactDetailMatch"] is False
    assert rows[0]["isExactUnitNameMatch"] is True
    assert rows[0]["canonicalDetailName"] == "\uce74\uc9c0\ub178\uc6b4\uc601\uad00\ub9ac"


def test_ncs_unit_options_falls_back_to_manual_suggestions(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    mocker.patch("app.main.search_units_by_detail", return_value=[])
    suggestion = {
        "ncsClCd": "0601010101_20v1",
        "compeUnitName": "\uc758\ub8cc\uc9c0\uc6d0 \ud6c4\ubcf4",
        "ncsSubdCdnm": "\uc758\ub8cc\uae30\uae30\uad00\ub9ac",
        "source": "ncs-mcp-suggest",
    }
    mocker.patch("app.main.suggest_units_by_text", return_value=[suggestion])

    with TestClient(main.app) as client:
        resp = client.get("/api/ncs/units/options?q=\uc784\uc0c1\ubcd1\ub9ac&limit=10")

    body = resp.json()
    assert resp.status_code == 200
    assert body["source"] == "ncs-mcp-suggest"
    assert body["items"] == [suggestion]
    assert "Exact detail-class match" in body["message"]


def test_legacy_ncs_sclass_ksa_endpoint_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_LEGACY_NCS_API", raising=False)

    with TestClient(main.app) as client:
        resp = client.get("/api/ncs/sclass/ksa?sclassName=\ucd1d\ubb34")

    assert resp.status_code == 410


def test_ksa_lookup_requires_ncs_mcp_url(monkeypatch):
    monkeypatch.delenv("NCS_MCP_URL", raising=False)

    with pytest.raises(ncs_mcp_client.NcsMcpError, match="NCS_MCP_URL"):
        fetch_ncs_ksa_by_units(
            [{"ncsClCd": "0201010103_22v2", "compeUnitName": "\uacbd\uc601\uacc4\ud68d \uc218\ub9bd"}],
            max_units=1,
            max_factors_per_unit=1,
        )


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
