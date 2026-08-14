from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main


REQUEST_KEY = "sk-request-scoped-precision-boundary-test"
NCS_CODE = "0201010103_22v2"
REMOTE_CLIENT = ("203.0.113.42", 45123)


def _unsupported_precision_question() -> dict[str, Any]:
    return {
        "type": "발표면접",
        "question": (
            "최근 3년간 사업 실적표를 제공해 드리겠습니다. 사업 개선안을 "
            "발표해 주세요."
        ),
        "follow_ups": [
            "분자와 분모, 기준연도 값을 자료에서 가져와 계산 과정을 설명하세요."
        ],
        "evaluation_points": ["진단", "근거", "대안", "실행"],
        "question_source": "openai_api",
    }


def _unit() -> dict[str, str]:
    return {
        "ncsClCd": NCS_CODE,
        "compeUnitName": "경영계획 수립",
        "compeUnitLevel": "5",
        "ncsSubdCdnm": "경영기획",
        "compeUnitDef": "경영목표와 성과지표를 수립한다.",
    }


def _ksa() -> dict[str, str]:
    return {
        "ncsClCd": NCS_CODE,
        "compeUnitName": "경영계획 수립",
        "factorName": "핵심성과지표 설정 능력",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
        "ksaTypeName": "기술",
    }


def _strategy_result() -> dict[str, Any]:
    return {"interview_questions": [_unsupported_precision_question()]}


def _passed_post_quality(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    return {
        **strategy,
        "question_quality_report": {
            "passed": True,
            "summary": {"ready_count": 1, "needs_review_count": 0},
        },
        "question_quality_orchestration": {
            "status": "passed",
            "unresolved_count": 0,
        },
    }


def _patch_strategy_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502", lambda **_kwargs: [_ksa()])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **_kwargs: [_ksa()])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **_kwargs: {})
    monkeypatch.setattr(main, "build_jd_strategy_with_openai", lambda **_kwargs: _strategy_result())
    monkeypatch.setattr(
        main,
        "_adjust_generated_questions",
        lambda strategy, *_args, **_kwargs: strategy,
    )
    monkeypatch.setattr(
        main,
        "_attach_ksa_evidence_to_strategy",
        lambda strategy, *_args, **_kwargs: strategy,
    )
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        _passed_post_quality,
    )
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_record_audit_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "search_units_by_detail", lambda *_args, **_kwargs: [_unit()])
    monkeypatch.setattr(
        main,
        "rerank_ncs_matches",
        lambda *_args, **_kwargs: ([_unit()], "mcp"),
    )


def _from_text_payload() -> dict[str, Any]:
    return {
        "openai_api_key": REQUEST_KEY,
        "notice_text": "공공기관 경영기획 담당자 채용",
        "duty_text": "사업계획과 성과지표를 검토한다.",
        "selected_ncs": [_unit()],
        "question_plan": {
            "items": [
                {
                    "detail": "경영기획",
                    "enabled": True,
                    "main_count": 1,
                    "follow_up_count": 3,
                }
            ]
        },
        "interview_methods": ["발표면접"],
    }


@pytest.mark.parametrize(
    "path", ["/api/questions/generate-from-text", "/api/jd/strategy/upload"]
)
def test_primary_public_routes_reject_unsupported_precision_at_final_boundary(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    _patch_strategy_pipeline(monkeypatch)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        if path.endswith("generate-from-text"):
            response = client.post(path, json=_from_text_payload())
        else:
            review_payload = {
                "document": {"markdown": "경영기획 직무기술서"},
                "fields": {"ncs_detail_candidates": ["경영기획"]},
            }
            session = main._create_review_session(
                "경영기획 직무기술서".encode(), review_payload, "job.txt"
            )
            response = client.post(
                path,
                files={
                    "jd_file": (
                        "job.txt",
                        "경영기획 직무기술서",
                        "text/plain",
                    )
                },
                data={
                    "openai_api_key": REQUEST_KEY,
                    "jd_review_json": json.dumps(
                        {
                            **review_payload,
                            "review_confirmed": True,
                            "review_session_id": session["id"],
                            "review_session": session,
                        },
                        ensure_ascii=False,
                    ),
                },
            )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "openai_api_quality_rejected",
        "provider": "openai_api",
        "message": "생성된 질문이 NCS/KSA 품질 검사를 통과하지 못했습니다. 문항 수나 면접기법 범위를 줄여 다시 시도해 주세요.",
        "retryable": True,
    }
    assert "기준연도" not in response.text
    assert "분자" not in response.text
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/questions/generate-personalized",
        "/api/questions/generate-by-ncs-code",
        "/api/questions/generate-batch",
        "/api/questions/generate-diverse",
    ],
)
def test_auxiliary_public_routes_reject_unsupported_precision_generically(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    question = _unsupported_precision_question()
    if path.endswith("generate-by-ncs-code"):
        monkeypatch.setattr(
            main,
            "generate_interview_questions_by_ncs_code",
            lambda **_kwargs: {"main_questions": [question]},
        )
    elif path.endswith("generate-personalized"):
        monkeypatch.setattr(
            main,
            "generate_personalized_interview_questions",
            lambda **_kwargs: {"questions": [question]},
        )
    else:
        monkeypatch.setattr(
            main,
            "generate_diverse_interview_questions",
            lambda **_kwargs: {"questions": [question]},
        )

    payload: dict[str, Any] = {
        "openai_api_key": REQUEST_KEY,
        "ncs_code": NCS_CODE,
        "target_count": 1,
    }
    if path.endswith("generate-batch"):
        payload.pop("target_count")
        payload["batch_count"] = 10

    with TestClient(main.app) as client:
        response = client.post(path, json=payload)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "openai_api_generation_failed"
    assert "기준연도" not in response.text
    assert "분자" not in response.text
    assert REQUEST_KEY not in response.text


def test_quality_report_records_precision_failure_without_copying_source_text() -> None:
    question = _unsupported_precision_question()
    result = main._attach_question_quality_report(
        {"interview_questions": [question]}
    )
    item = result["question_quality_report"]["items"][0]
    precision_snapshot = json.dumps(
        {
            "issues": item["precision_grounding_issues"],
            "demands": item["precision_grounding_demands"],
            "metrics": item["precision_grounding_metrics"],
        },
        ensure_ascii=False,
    )

    assert item["checks"]["precision_grounding"] is False
    assert item["check_statuses"]["precision_grounding"] == "fail"
    assert "precision_grounding" in item["issues"]
    assert item["ready"] is False
    assert "unsupported_precision_demand" in item["precision_grounding_issue_codes"]
    assert item["precision_grounding_demands"][0]["location"] == "follow_ups[0]"
    assert item["precision_grounding_demands"][0]["text_sha256"]
    assert "기준연도 값을 자료에서" not in precision_snapshot


def test_final_boundary_matches_recorded_baseline_and_revised_precision_result() -> None:
    report_path = (
        Path(__file__).resolve().parents[1]
        / "reports"
        / "revised_prompt_cross_provider_20260814.json"
    )
    if not report_path.exists():
        pytest.skip("overnight cross-provider evidence report is not present")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))

    baseline_results = {
        (provider["provider"], row["case"]): main._public_questions_precision_grounded(
            {"questions": [row["question"]]}
        )
        for provider in report["baseline_analysis"]
        for row in provider["questions"]
    }
    revised_results = [
        main._public_questions_precision_grounded({"questions": [row["question"]]})
        for provider in report["revised_analysis"]
        for row in provider["questions"]
    ]

    assert len(baseline_results) == 8
    assert {
        key for key, passed in baseline_results.items() if not passed
    } == {
        ("codex_cli", "performance_indicator"),
        ("claude_code", "performance_indicator"),
    }
    assert revised_results == [True] * 8
