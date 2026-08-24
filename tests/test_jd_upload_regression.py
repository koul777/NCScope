from __future__ import annotations

import json

from fastapi.testclient import TestClient

import app.main as main


REQUEST_OPENAI_KEY = "sk-test-request-scoped-upload-key"


def test_jd_strategy_upload_no_nameerror_regression(monkeypatch, mocker):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mocker.patch("app.main.init_db", return_value=None)
    mocker.patch("app.main.start_auto_runner", return_value=None)
    mocker.patch(
        "app.main.review_interview_questions_with_ai",
        side_effect=lambda **kwargs: {
            "status": "passed",
            "reviewed_count": len(kwargs.get("questions") or []),
            "scores": [],
            "reason_codes": [],
            "items": [],
            "model": "gpt-5.6-sol",
            "provider": "openai_api",
        },
    )
    parse_upload = mocker.patch("app.main._parse_upload_document")
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
    mocker.patch(
        "app.main._run_runtime_question_quality_orchestration",
        side_effect=lambda strategy, **_kwargs: strategy,
    )
    # This regression isolates the historical undefined-variable crash. The
    # production quality boundary is covered by dedicated endpoint tests.
    mocker.patch("app.main._require_institution_api_question_output", return_value=None)
    mocker.patch("app.main._institution_question_rejection_codes", return_value=[])
    build_strategy = mocker.patch(
        "app.main.build_jd_strategy_with_openai",
        return_value={
            "interview_questions": [
                {
                    "type": "경험면접",
                    "question_source": "openai_api",
                    "question": "제한된 자료로 우선순위를 정해 업무를 완료한 사례를 설명해 주세요.",
                    "follow_ups": [
                        "당시 목표와 본인의 역할은 무엇이었습니까?",
                        "우선순위를 정하기 위해 어떤 자료를 확인했습니까?",
                        "결과를 어떤 기준으로 점검했습니까?",
                    ],
                    "evaluation_points": [
                        "상황과 역할의 구체성",
                        "자료 확인 근거의 타당성",
                        "실행 행동의 명확성",
                        "결과 점검과 개선의 연계성",
                    ],
                },
            ]
        },
    )
    jd_text = "총무 및 자산관리 업무"
    structured = {"document": {"markdown": jd_text}, "fields": {"ncs_detail_candidates": ["총무"]}}
    session = main._create_review_session(jd_text.encode("utf-8"), structured, "jd.txt")
    review = {**structured, "review_confirmed": True, "review_session_id": session["id"], "review_session": session}
    notice_text = "공공기관 채용공고 담당업무 및 면접 평가항목"
    notice_structured = {
        "document": {"markdown": notice_text},
        "fields": {"duty_text": "담당업무", "evaluation_text": "면접 평가항목"},
    }
    notice_session = main._create_review_session(
        notice_text.encode("utf-8"),
        notice_structured,
        "notice.txt",
    )
    notice_review = {
        **notice_structured,
        "review_confirmed": True,
        "review_session_id": notice_session["id"],
        "review_session": notice_session,
    }

    with TestClient(main.app) as client:
        resp = client.post(
            "/api/jd/strategy/upload",
            files={
                "jd_file": ("jd.txt", jd_text, "text/plain"),
                "notice_file": ("notice.txt", notice_text, "text/plain"),
            },
            data={
                "jd_review_json": json.dumps(review, ensure_ascii=False),
                "notice_review_json": json.dumps(notice_review, ensure_ascii=False),
                "openai_api_key": REQUEST_OPENAI_KEY,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["pipeline_mode"] == "direct-ncs"
    assert body["ncs_source"].startswith("ncs-mcp")
    assert "strategy" in body
    build_strategy.assert_called_once()
    parse_upload.assert_not_called()
    assert build_strategy.call_args.kwargs["api_key_override"] == REQUEST_OPENAI_KEY
    assert REQUEST_OPENAI_KEY not in resp.text
