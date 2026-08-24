from __future__ import annotations

import json
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services import jd_strategy, openai_http
from app.services.ai_question_quality_review import AI_QUALITY_DIMENSIONS


REQUEST_KEY = "sk-request-scoped-boundary-secret"
SECOND_REQUEST_KEY = "sk-second-request-boundary-secret"
SERVER_KEY = "sk-server-fallback-must-not-be-used"
REMOTE_CLIENT = ("203.0.113.42", 45123)


def _unit() -> dict[str, str]:
    return {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "경영계획 수립",
        "compeUnitLevel": "5",
        "ncsSubdCdnm": "경영기획",
        "compeUnitDef": "경영목표를 수립하고 실행계획을 마련한다.",
    }


def _ksa() -> dict[str, str]:
    unit = _unit()
    return {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "시장환경 분석",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }


def _model_strategy() -> dict[str, Any]:
    evidence_id = main.stable_ksa_evidence_id(_ksa())
    return {
        "interview_questions": [
            {
                "question": "시장환경 분석 결과를 바탕으로 사업 우선순위를 정한 경험을 설명해 주세요.",
                "question_source": "openai_api",
                "ncsClCd": _unit()["ncsClCd"],
                "question_focus": _ksa()["factorName"],
                "question_focus_source": "official_ksa",
                "question_evidence_id": evidence_id,
                "question_evidence_required": True,
                "ksa_refs": [_ksa()["factorName"]],
                "follow_ups": [
                    "판단 기준은 무엇이었습니까?",
                    "어떤 대안을 비교했습니까?",
                    "결과를 어떤 기준으로 확인했습니까?",
                ],
                "evaluation_points": [
                    "자료 출처를 구분하는지",
                    "판단 기준이 구체적인지",
                    "우선순위 결정과 직무가 연결되는지",
                    "결과 검증 행동이 확인 가능한지",
                ],
            }
        ],
        "question_quality_report": {"passed": True},
        "question_quality_orchestration": {"status": "passed"},
    }


def _generation_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "openai_api_key": REQUEST_KEY,
        "generation_provider": "openai_api",
        "notice_text": "공공기관 경영기획 담당업무",
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
        "interview_methods": ["경험면접"],
    }
    payload.update(overrides)
    return payload


def _patch_generation_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[..., dict[str, Any]],
) -> None:
    """Keep endpoint tests focused on credential and provider boundaries."""

    ksa = _ksa()
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502", lambda **_kwargs: [ksa])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **_kwargs: [ksa])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **_kwargs: {})
    monkeypatch.setattr(main, "build_jd_strategy_with_openai", builder)
    monkeypatch.setattr(
        main,
        "_adjust_generated_questions",
        lambda strategy, *args, **kwargs: strategy,
    )
    monkeypatch.setattr(
        main,
        "_attach_ksa_evidence_to_strategy",
        lambda strategy, *args, **kwargs: strategy,
    )
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        lambda strategy, *args, **kwargs: strategy,
    )
    def fake_audit(strategy: dict[str, Any], _ksa_rows: list[dict[str, Any]]) -> dict[str, Any]:
        audited = dict(strategy)
        rows = list(audited.get("interview_questions") or [])
        audited["question_quality_report"] = {
            "passed": True,
            "summary": {"count_matches_plan": True},
            "items": [
                {"index": index, "ready": True, "issues": []}
                for index, _row in enumerate(rows, start=1)
            ],
        }
        audited["question_quality_orchestration"] = {
            "status": "passed",
            "items": [
                {"index": index, "final_issues": []}
                for index, _row in enumerate(rows, start=1)
            ],
        }
        return audited

    monkeypatch.setattr(main, "_audit_ai_authored_strategy_without_repair", fake_audit)
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **kwargs: {
            "policy": "independent-ai-question-review-v1",
            "status": "passed",
            "reviewed_count": len(kwargs.get("questions") or []),
            "scores": [
                {"index": index, **{dimension: 5 for dimension in AI_QUALITY_DIMENSIONS}}
                for index, _row in enumerate(kwargs.get("questions") or [], start=1)
            ],
            "reason_codes": [],
            "items": [
                {
                    "index": index,
                    "passed": True,
                    "scores": {dimension: 5 for dimension in AI_QUALITY_DIMENSIONS},
                    "reason_codes": [],
                    "regeneration_guidance_codes": [],
                }
                for index, _row in enumerate(kwargs.get("questions") or [], start=1)
            ],
            "model": "gpt-5.6-sol",
            "provider": "openai_api",
        },
    )
    monkeypatch.setattr(
        main,
        "_register_question_quality_evidence",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(main, "_record_audit_event", lambda *args, **kwargs: None)


def _assert_key_required(response) -> None:
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert set(detail) == {"code", "provider", "message", "retryable"}
    assert detail["code"] == "openai_api_key_required"
    assert detail["provider"] == "openai_api"
    assert isinstance(detail["message"], str) and detail["message"]
    assert detail["retryable"] is False
    assert REQUEST_KEY not in response.text
    assert SERVER_KEY not in response.text


def test_status_declares_request_key_required_without_receiving_a_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    def unexpected_verification(_api_key: str) -> tuple[bool, str]:
        raise AssertionError("status must not inspect a server or request key")

    monkeypatch.setattr(
        main,
        "_verify_institution_openai_api",
        unexpected_verification,
    )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = client.get("/api/generation-provider/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai_api"
    assert payload["auth_mode"] == "request_scoped_api_key"
    assert payload["status"] == "key_required"
    assert payload["authenticated"] is False
    assert payload["requires_request_api_key"] is True
    assert payload["local_only"] is False
    assert SERVER_KEY not in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/questions/generate-from-text",
        "/api/questions/generate-personalized",
        "/api/questions/generate-by-ncs-code",
        "/api/questions/generate-batch",
        "/api/questions/generate-diverse",
    ],
)
def test_public_json_generation_requires_request_key_even_with_server_key(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    with TestClient(main.app) as client:
        response = client.post(path, json={})

    _assert_key_required(response)


def test_upload_form_requires_request_key_even_with_server_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("job.txt", b"job description", "text/plain")},
        )

    _assert_key_required(response)


@pytest.mark.parametrize(
    "path",
    [
        "/api/questions/generate-from-text",
        "/api/questions/generate-personalized",
        "/api/questions/generate-by-ncs-code",
        "/api/questions/generate-batch",
        "/api/questions/generate-diverse",
    ],
)
def test_public_json_generation_accepts_body_key_before_domain_validation(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    with TestClient(main.app) as client:
        response = client.post(path, json={"openai_api_key": REQUEST_KEY})

    assert response.status_code == 400
    assert not (
        isinstance(response.json().get("detail"), dict)
        and response.json()["detail"].get("code") == "openai_api_key_required"
    )
    assert REQUEST_KEY not in response.text
    assert SERVER_KEY not in response.text


def test_upload_form_accepts_form_key_before_file_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("job.txt", b"", "text/plain")},
            data={"openai_api_key": REQUEST_KEY},
        )

    assert response.status_code == 400
    assert "jd_file is empty" in response.text
    assert "openai_api_key_required" not in response.text
    assert REQUEST_KEY not in response.text
    assert SERVER_KEY not in response.text


def test_request_key_query_parameter_is_rejected_without_echoing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            params={"openai_api_key": REQUEST_KEY},
            json=_generation_payload(openai_api_key=SECOND_REQUEST_KEY),
        )

    assert response.status_code == 400
    assert "query" in response.text.lower()
    assert REQUEST_KEY not in response.text
    assert SECOND_REQUEST_KEY not in response.text
    assert SERVER_KEY not in response.text


def test_remote_generation_passes_only_supplied_request_key_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)
    captured: dict[str, Any] = {}

    def builder(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _model_strategy()

    _patch_generation_pipeline(monkeypatch, builder)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            headers={"X-Forwarded-For": "198.51.100.17"},
            json=_generation_payload(),
        )

    assert response.status_code == 200
    assert captured["generation_provider"] == "openai_api"
    assert captured["api_key_override"] == REQUEST_KEY
    assert response.json()["openai_key_source"] == "request"
    assert REQUEST_KEY not in response.text
    assert SERVER_KEY not in response.text


def test_settings_never_resolve_server_environment_as_generation_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    assert main.settings.resolve_openai_key(REQUEST_KEY) == REQUEST_KEY
    assert main.settings.resolve_openai_key("") == ""
    assert main.settings.openai_key_source(REQUEST_KEY) == "request"
    assert main.settings.openai_key_source("") == "missing"


def test_provider_failure_returns_no_questions_and_does_not_expose_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SERVER_KEY)

    def builder(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["api_key_override"] == REQUEST_KEY
        raise RuntimeError(
            f"upstream failure request={REQUEST_KEY} server={SERVER_KEY}"
        )

    _patch_generation_pipeline(monkeypatch, builder)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json=_generation_payload(),
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "openai_api_generation_failed"
    assert "strategy" not in response.json()
    assert "server_ksa_fallback" not in response.text
    assert "upstream failure" not in response.text
    assert "template_fallback" not in response.text
    assert REQUEST_KEY not in response.text
    assert SERVER_KEY not in response.text


@pytest.mark.parametrize(
    "method",
    [
        "경험면접",
        "상황면접",
        "발표면접",
        "토론면접",
        "인바스켓면접",
        "직무지식면접",
        "창의적 문제해결력면접",
    ],
)
@pytest.mark.parametrize("question_count", [1, 2, 3, 4, 5])
def test_public_endpoint_releases_all_supported_methods_and_counts_only_after_ai_review(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    question_count: int,
) -> None:
    evidence_id = main.stable_ksa_evidence_id(_ksa())

    def builder(**kwargs: Any) -> dict[str, Any]:
        follow_up_count = int(kwargs["follow_up_count"])
        return {
            "interview_questions": [
                {
                    "question": f"담당 업무에서 서로 다른 근거를 비교해 결정한 사례 {index}를 설명해 주세요.",
                    "question_source": "openai_api",
                    "type": method,
                    "ncsClCd": _unit()["ncsClCd"],
                    "question_evidence_id": evidence_id,
                    "question_focus": _ksa()["factorName"],
                    "question_focus_source": "official_ksa",
                    "ksa_refs": [_ksa()["factorName"]],
                    "follow_ups": [
                        f"그 답변에서 판단 근거 {follow_up_index}를 어떻게 확인했습니까?"
                        for follow_up_index in range(1, follow_up_count + 1)
                    ],
                    "evaluation_points": [
                        "비교한 근거의 구체성",
                        "판단 과정의 일관성",
                        "본인이 수행한 행동",
                        "결과 확인 방법",
                    ],
                }
                for index in range(1, int(kwargs["target_count_override"]) + 1)
            ],
            "provider_generation_model": "gpt-5.6-terra",
            "provider_generation_request_count": 1,
        }

    _patch_generation_pipeline(monkeypatch, builder)
    payload = _generation_payload(
        interview_methods=[method],
        question_plan={
            "items": [
                {
                    "detail": _unit()["ncsSubdCdnm"],
                    "enabled": True,
                    "main_count": question_count,
                    "follow_up_count": question_count,
                }
            ]
        },
    )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = client.post("/api/questions/generate-from-text", json=payload)

    assert response.status_code == 200, response.text
    body = response.json()
    questions = body["strategy"]["interview_questions"]
    assert len(questions) == question_count
    assert all(question["type"] == method for question in questions)
    assert all(len(question["follow_ups"]) == question_count for question in questions)
    assert all(question["question_source"] == "openai_api" for question in questions)
    assert body["strategy"]["ai_quality_review"]["status"] == "passed"


def test_provider_failure_cannot_enter_a_server_question_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-or-server-fallback-internal-secret"

    def provider_failure(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(f"provider failed with {secret}")

    _patch_generation_pipeline(monkeypatch, provider_failure)
    assert not hasattr(main, "build_server_ksa_fallback_strategy")

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json=_generation_payload(),
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "openai_api_generation_failed"
    assert "server_ksa_fallback" not in response.text
    assert "provider failed" not in response.text
    assert secret not in response.text


@pytest.mark.parametrize("provider", ["codex_cli", "claude_code"])
def test_public_endpoints_reject_personal_subscription_cli_providers(
    provider: str,
) -> None:
    with TestClient(main.app) as client:
        status_response = client.get(
            "/api/generation-provider/status",
            params={"provider": provider},
        )
        generation_response = client.post(
            "/api/questions/generate-from-text",
            json=_generation_payload(generation_provider=provider),
        )

    assert status_response.status_code == 400
    assert generation_response.status_code == 400
    assert "openai_api" in status_response.text
    assert "openai_api" in generation_response.text
    assert REQUEST_KEY not in generation_response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/questions/generate-personalized",
        "/api/questions/generate-by-ncs-code",
        "/api/questions/generate-batch",
        "/api/questions/generate-diverse",
    ],
)
@pytest.mark.parametrize("provider", ["codex_cli", "claude_code"])
def test_all_legacy_generation_routes_reject_subscription_cli_providers(
    path: str,
    provider: str,
) -> None:
    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={
                "generation_provider": provider,
                "openai_api_key": REQUEST_KEY,
            },
        )

    assert response.status_code == 400
    assert "openai_api" in response.text
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize(
    ("status_code", "expected_ok", "expected_message"),
    [(200, True, ""), (401, False, "http_401"), (403, False, "http_403")],
)
def test_connectivity_probe_requires_successful_api_authentication(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_ok: bool,
    expected_message: str,
) -> None:
    class StubResponse:
        def __init__(self, code: int) -> None:
            self.status_code = code

    class StubClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> StubClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def get(self, _url: str, **_kwargs: Any) -> StubResponse:
            return StubResponse(status_code)

    monkeypatch.setattr(openai_http.httpx, "Client", StubClient)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://approved-gateway.example/v1")

    ok, message = openai_http.check_openai_connectivity_with_retries(
        REQUEST_KEY,
        max_attempts=1,
    )

    assert ok is expected_ok
    assert message == expected_message


def test_openai_http_ignores_multi_endpoint_failover_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://approved-gateway.example/v1/")
    monkeypatch.setenv(
        "OPENAI_BASE_URLS",
        "https://unapproved-a.example/v1,https://unapproved-b.example/v1",
    )

    assert openai_http._openai_base_urls() == ["https://api.openai.com/v1"]


def test_openai_strategy_marks_freeform_provenance_and_requests_exact_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_chat(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"interview_questions":[{'
                            '"type":"경험면접",'
                            '"competency":"문서작성",'
                            '"ncsClCd":"0202030201_25v3",'
                            '"question":"마감 직전에 원자료 오류를 발견해 제출 범위를 조정한 경험을 설명해 주세요.",'
                            '"follow_ups":["어떤 자료를 먼저 대조했습니까?"],'
                            '"evaluation_points":["대조 자료","판단 근거","수정 조치","완료 기록"],'
                            '"question_evidence_id":"ksa-evidence-001",'
                            '"question_focus_surface":"문서 요구사항 확인 절차",'
                            '"question_focus":"문서 요구사항 파악",'
                            '"ksa_refs":["문서 요구사항 파악"]'
                            "}]}"
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        jd_strategy,
        "_check_openai_connectivity",
        lambda **_kwargs: (True, ""),
    )
    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", fake_chat)

    result = jd_strategy.build_strategy_with_openai(
        jd_text="문서 작성과 원자료 검증",
        notice_text="사무행정 담당자 채용",
        strengths="",
        region="",
        ncs_matches=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "compeUnitDef": "문서 요구사항에 따라 문서를 작성한다.",
            }
        ],
        ncs_ksa=[
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "factorName": "문서 요구사항 파악",
            }
        ],
        api_key_override=REQUEST_KEY,
        target_count_override=1,
    )

    question = result["interview_questions"][0]
    assert question["question_source"] == "openai_api"
    assert question["question_evidence_id"] == "ksa-evidence-001"
    prompt = captured["payload"]["messages"][1]["content"]
    assert "question_evidence_id" in prompt
    assert "required_factorName" in prompt
    assert "question, follow_ups, evaluation_points에는 노출하지 마세요" in prompt
    assert "evaluation_points는 정확히 4개" in prompt
    assert REQUEST_KEY not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    "path",
    ["/api/questions/generate-from-text", "/api/jd/strategy/upload"],
)
def test_post_quality_deterministic_repair_cannot_replace_ai_authored_text(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    _patch_generation_pipeline(monkeypatch, lambda **_kwargs: _model_strategy())
    monkeypatch.setattr(main, "search_units_by_detail", lambda *args, **kwargs: [_unit()])
    monkeypatch.setattr(
        main,
        "rerank_ncs_matches",
        lambda *args, **kwargs: ([_unit()], "mcp"),
    )
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        lambda strategy, **_kwargs: {
            **strategy,
            "interview_questions": [
                {
                    "question": "결정론적으로 재작성된 질문",
                    "question_source": "quality_orchestrator_repair",
                }
            ],
        },
    )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        if path.endswith("generate-from-text"):
            response = client.post(path, json=_generation_payload())
        else:
            review_payload = {
                "document": {"markdown": "직무기술서"},
                "fields": {"ncs_detail_candidates": ["경영기획"]},
            }
            session = main._create_review_session(
                "직무기술서".encode(),
                review_payload,
                "job.txt",
            )
            response = client.post(
                path,
                files={"jd_file": ("job.txt", "직무기술서", "text/plain")},
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

    assert response.status_code == 200
    strategy = response.json()["strategy"]
    assert strategy["interview_questions"][0]["question_source"] == "openai_api"
    assert strategy["ai_quality_review"]["status"] == "passed"
    assert "quality_orchestrator_repair" not in response.text
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize(
    "path",
    ["/api/questions/generate-from-text", "/api/jd/strategy/upload"],
)
@pytest.mark.parametrize(
    ("quality_report", "orchestration"),
    [
        (
            {
                "passed": False,
                "summary": {"ready_count": 0, "needs_review_count": 1},
            },
            {"status": "passed", "unresolved_count": 0},
        ),
        (
            {
                "passed": True,
                "summary": {"ready_count": 1, "needs_review_count": 0},
            },
            {"status": "needs_review", "unresolved_count": 1},
        ),
    ],
)
def test_legacy_post_quality_metadata_cannot_replace_ai_reviewed_text(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    quality_report: dict[str, Any],
    orchestration: dict[str, Any],
) -> None:
    _patch_generation_pipeline(monkeypatch, lambda **_kwargs: _model_strategy())
    monkeypatch.setattr(main, "search_units_by_detail", lambda *args, **kwargs: [_unit()])
    monkeypatch.setattr(
        main,
        "rerank_ncs_matches",
        lambda *args, **kwargs: ([_unit()], "mcp"),
    )

    def failed_post_quality(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {
            **strategy,
            "question_quality_report": quality_report,
            "question_quality_orchestration": orchestration,
        }

    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        failed_post_quality,
    )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        if path.endswith("generate-from-text"):
            response = client.post(path, json=_generation_payload())
        else:
            review_payload = {
                "document": {"markdown": "직무기술서"},
                "fields": {"ncs_detail_candidates": ["경영기획"]},
            }
            session = main._create_review_session(
                "직무기술서".encode(),
                review_payload,
                "job.txt",
            )
            response = client.post(
                path,
                files={"jd_file": ("job.txt", "직무기술서", "text/plain")},
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

    assert response.status_code == 200
    strategy = response.json()["strategy"]
    assert strategy["interview_questions"][0]["question_source"] == "openai_api"
    assert strategy["ai_quality_review"]["status"] == "passed"
    assert _model_strategy()["interview_questions"][0]["question"] in response.text
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize(
    "builder_result",
    [
        {"interview_questions": []},
        {
            "interview_questions": [],
            "question_generation_policy": "model_only_no_template_fallback",
            "error": "model_generation_failed: upstream detail",
        },
    ],
)
def test_empty_model_output_returns_no_fallback_questions(
    monkeypatch: pytest.MonkeyPatch,
    builder_result: dict[str, Any],
) -> None:
    _patch_generation_pipeline(monkeypatch, lambda **_kwargs: dict(builder_result))

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json=_generation_payload(),
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] in {
        "openai_api_generation_failed",
        "openai_api_invalid_output",
    }
    assert "strategy" not in response.json()
    assert "server_ksa_fallback" not in response.text
    assert "upstream detail" not in response.text
    assert "template_fallback" not in response.text
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize(
    ("reason", "status_code", "public_code"),
    [
        ("openai_request_timeout", 504, "openai_api_timeout"),
        ("model_response_truncated", 502, "openai_api_invalid_output"),
        ("model_question_count_mismatch", 502, "openai_api_invalid_output"),
        ("model_question_diversity_mismatch", 502, "openai_api_quality_rejected"),
        ("openai_http_503", 502, "openai_api_upstream_unavailable"),
        ("openai_http_400", 502, "openai_api_request_rejected"),
    ],
)
def test_provider_failure_mapper_exposes_safe_actionable_reason(
    reason: str,
    status_code: int,
    public_code: str,
) -> None:
    error = main._institution_api_provider_http_error(RuntimeError(reason))

    assert error.status_code == status_code
    assert error.detail["code"] == public_code
    assert error.detail["provider"] == "openai_api"
    assert isinstance(error.detail["message"], str) and error.detail["message"]


def test_model_output_boundary_preserves_only_allowlisted_failure_reason() -> None:
    with pytest.raises(RuntimeError, match="^model_response_truncated$"):
        main._require_institution_api_model_output(
            {
                "interview_questions": [],
                "error": "model_generation_failed: model_response_truncated",
            }
        )

    with pytest.raises(RuntimeError, match="^institution_api_empty_generation$"):
        main._require_institution_api_model_output(
            {
                "interview_questions": [],
                "error": f"model_generation_failed: private={REQUEST_KEY}",
            }
        )
