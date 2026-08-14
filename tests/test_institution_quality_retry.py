from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services.question_surface import stable_ksa_evidence_id


REQUEST_KEY = "sk-request-scoped-quality-retry-secret"
REMOTE_CLIENT = ("203.0.113.84", 45124)
PRIMARY_PATHS = [
    "/api/questions/generate-from-text",
    "/api/jd/strategy/upload",
]


def _unit() -> dict[str, Any]:
    return {
        "ncsClCd": "0201010103_22v2",
        "compeUnitName": "경영계획 수립",
        "compeUnitLevel": "5",
        "ncsSubdCdnm": "경영기획",
        "compeUnitDef": "경영목표를 수립하고 실행계획을 마련한다.",
        "score": 1.0,
        "matched_keywords": ["경영기획"],
    }


def _ksa(factor_name: str = "시장환경 분석", factor_no: str = "1") -> dict[str, str]:
    return {
        "ncsClCd": _unit()["ncsClCd"],
        "compeUnitName": _unit()["compeUnitName"],
        "elementName": "경영환경 분석",
        "ksaTypeName": "기술",
        "factorName": factor_name,
        "factorNo": factor_no,
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }


def _model_strategy(*, evidence_id: str | None = None) -> dict[str, Any]:
    if evidence_id is None:
        evidence_id = stable_ksa_evidence_id(_ksa())
    question: dict[str, Any] = {
        "question": (
            "신규 사업 검토 자료에서 수요 전망과 비용 추정이 충돌할 때, "
            "무엇을 먼저 검증하고 어떤 우선순위표를 제출하겠습니까?"
        ),
        "question_source": "openai_api",
        "ncsClCd": _unit()["ncsClCd"],
        "question_focus": "시장환경 분석",
        "ksa_refs": ["시장환경 분석"],
        "follow_ups": [
            "방금 선택한 검증 항목에서 어떤 수치가 나오면 판단을 바꾸겠습니까?",
            "말씀한 우선순위에서 가장 큰 위험은 무엇입니까?",
            "최종 표에는 어떤 근거를 남기겠습니까?",
        ],
        "evaluation_points": ["검증 순서", "판단 근거", "위험 통제", "산출물 완결성"],
    }
    if evidence_id:
        question["question_evidence_id"] = evidence_id
    return {"interview_questions": [question]}


def _quality_result(strategy: dict[str, Any], *, passed: bool) -> dict[str, Any]:
    return {
        **strategy,
        "question_quality_report": {
            "passed": passed,
            "summary": {
                "ready_count": 1 if passed else 0,
                "needs_review_count": 0 if passed else 1,
            },
        },
        "question_quality_orchestration": {
            "status": "passed" if passed else "needs_review",
            "unresolved_count": 0 if passed else 1,
        },
    }


def _generation_payload() -> dict[str, Any]:
    return {
        "openai_api_key": REQUEST_KEY,
        "generation_provider": "openai_api",
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
        "interview_methods": ["상황면접"],
    }


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    builder: Callable[..., dict[str, Any]],
    *,
    ksa_rows: list[dict[str, Any]] | None = None,
) -> None:
    rows = list(ksa_rows or [_ksa()])
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502", lambda **_kwargs: rows)
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **_kwargs: rows)
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **_kwargs: {})
    monkeypatch.setattr(main, "build_jd_strategy_with_openai", builder)
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
    monkeypatch.setattr(main, "_public_questions_precision_grounded", lambda _result: True)
    monkeypatch.setattr(main, "search_units_by_detail", lambda *_args, **_kwargs: [_unit()])
    monkeypatch.setattr(
        main,
        "rerank_ncs_matches",
        lambda *_args, **_kwargs: ([_unit()], "mcp"),
    )
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_record_audit_event", lambda *_args, **_kwargs: None)


def _post_primary(client: TestClient, path: str):
    if path.endswith("generate-from-text"):
        return client.post(path, json=_generation_payload())

    upload_bytes = "공공기관 경영기획 직무기술서".encode()
    review_payload = {
        "document": {"markdown": "공공기관 경영기획 직무기술서"},
        "fields": {"ncs_detail_candidates": ["경영기획"]},
    }
    session = main._create_review_session(upload_bytes, review_payload, "job.txt")
    return client.post(
        path,
        files={"jd_file": ("job.txt", upload_bytes, "text/plain")},
        data={
            "openai_api_key": REQUEST_KEY,
            "generation_provider": "openai_api",
            "question_plan_json": json.dumps(
                {
                    "items": [
                        {
                            "detail": "경영기획",
                            "enabled": True,
                            "main_count": 1,
                            "follow_up_count": 3,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "interview_methods_json": json.dumps(["상황면접"], ensure_ascii=False),
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


@pytest.mark.parametrize("path", PRIMARY_PATHS)
def test_primary_endpoint_retries_one_failed_quality_candidate_then_returns_200(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    build_calls: list[dict[str, Any]] = []
    quality_calls = 0

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        return _model_strategy()

    def quality(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal quality_calls
        quality_calls += 1
        return _quality_result(strategy, passed=quality_calls == 2)

    _patch_pipeline(monkeypatch, builder)
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", quality)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert response.status_code == 200
    assert len(build_calls) == 2
    retry_budget_fields = {
        "extra_context",
        "max_model_requests",
        "transport_max_attempts",
    }
    first = {
        key: value
        for key, value in build_calls[0].items()
        if key not in retry_budget_fields
    }
    second = {
        key: value
        for key, value in build_calls[1].items()
        if key not in retry_budget_fields
    }
    assert first == second
    assert build_calls[1]["max_model_requests"] == 1
    assert build_calls[1]["transport_max_attempts"] == 1
    assert build_calls[0]["extra_context"] != build_calls[1]["extra_context"]
    assert "question_quality_report_failed" in build_calls[1]["extra_context"]
    assert "question_quality_orchestration_failed" in build_calls[1]["extra_context"]
    assert all(REQUEST_KEY not in str(call.get("extra_context") or "") for call in build_calls)

    metadata = response.json()["strategy"]["model_quality_retry"]
    assert metadata == {
        "policy": "institution-openai-quality-retry-v1",
        "provider": "openai_api",
        "attempted": True,
        "retry_count": 1,
        "attempt_count": 2,
        "outcome": "passed_after_retry",
        "trigger_codes": [
            "question_quality_orchestration_failed",
            "question_quality_report_failed",
        ],
        "previous_candidate_count": 1,
        "evidence_lock_count": 1,
        "provider_generation_request_count": 0,
        "provider_generation_request_limit": 3,
        "transport_attempt_limit_per_generation_request": 1,
    }
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
def test_primary_endpoint_does_not_retry_when_first_candidate_passes(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        return _model_strategy()

    _patch_pipeline(monkeypatch, builder)
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        lambda strategy, **_kwargs: _quality_result(strategy, passed=True),
    )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert response.status_code == 200
    assert len(build_calls) == 1
    assert response.json()["strategy"]["model_quality_retry"] == {
        "policy": "institution-openai-quality-retry-v1",
        "provider": "openai_api",
        "attempted": False,
        "retry_count": 0,
        "attempt_count": 1,
        "outcome": "not_needed",
        "trigger_codes": [],
        "previous_candidate_count": 0,
        "evidence_lock_count": 1,
        "provider_generation_request_count": 0,
        "provider_generation_request_limit": 3,
        "transport_attempt_limit_per_generation_request": 1,
    }
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
def test_primary_endpoint_stops_after_one_quality_retry_and_returns_sanitized_502(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        return _model_strategy()

    _patch_pipeline(monkeypatch, builder)
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        lambda strategy, **_kwargs: _quality_result(strategy, passed=False),
    )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert response.status_code == 502
    assert len(build_calls) == 2
    assert response.json()["detail"]["code"] == "openai_api_quality_rejected"
    assert "question_quality_report_failed" not in response.text
    assert "needs_review" not in response.text
    assert REQUEST_KEY not in response.text
    assert all(REQUEST_KEY not in str(call.get("extra_context") or "") for call in build_calls)


@pytest.mark.parametrize("path", PRIMARY_PATHS)
@pytest.mark.parametrize("failure_stage", ["provider", "empty", "postprocess"])
def test_non_quality_failures_are_never_retried(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    failure_stage: str,
) -> None:
    build_count = 0

    def builder(**_kwargs: Any) -> dict[str, Any]:
        nonlocal build_count
        build_count += 1
        if failure_stage == "provider":
            raise RuntimeError(f"provider leaked {REQUEST_KEY}")
        if failure_stage == "empty":
            return {"interview_questions": []}
        return _model_strategy()

    _patch_pipeline(monkeypatch, builder)
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        lambda strategy, **_kwargs: _quality_result(strategy, passed=True),
    )
    if failure_stage == "postprocess":
        monkeypatch.setattr(
            main,
            "_adjust_generated_questions",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError(f"postprocess leaked {REQUEST_KEY}")
            ),
        )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert response.status_code == 502
    assert build_count == 1
    assert response.json()["detail"]["code"] == "openai_api_generation_failed"
    assert "leaked" not in response.text
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
@pytest.mark.parametrize("change_assignment", [False, True])
def test_retry_locks_each_server_validated_evidence_assignment(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    change_assignment: bool,
) -> None:
    first_row = _ksa("시장환경 분석", "1")
    second_row = _ksa("사업타당성 분석", "2")
    first_id = stable_ksa_evidence_id(first_row)
    second_id = stable_ksa_evidence_id(second_row)
    build_calls: list[dict[str, Any]] = []
    quality_calls = 0

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        evidence_id = second_id if change_assignment and len(build_calls) == 2 else first_id
        return _model_strategy(evidence_id=evidence_id)

    def quality(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal quality_calls
        quality_calls += 1
        return _quality_result(strategy, passed=quality_calls == 2)

    _patch_pipeline(monkeypatch, builder, ksa_rows=[first_row, second_row])
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", quality)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert len(build_calls) == 2
    assert first_id in build_calls[1]["extra_context"]
    assert REQUEST_KEY not in build_calls[1]["extra_context"]
    if change_assignment:
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "openai_api_quality_rejected"
        assert "evidence_assignment_changed" not in response.text
    else:
        assert response.status_code == 200
        metadata = response.json()["strategy"]["model_quality_retry"]
        assert metadata["evidence_lock_count"] == 1
        assert metadata["outcome"] == "passed_after_retry"
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
@pytest.mark.parametrize("first_assignment", ["missing", "invented", "other_valid"])
def test_primary_endpoint_retries_raw_evidence_assignment_drift_against_server_plan(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    first_assignment: str,
) -> None:
    planned_row = _ksa("?쒖옣?섍꼍 遺꾩꽍", "1")
    other_row = _ksa("?ъ뾽??뱀꽦 遺꾩꽍", "2")
    planned_id = stable_ksa_evidence_id(planned_row)
    first_ids = {
        "missing": "",
        "invented": "ksa_000000000000000000000000",
        "other_valid": stable_ksa_evidence_id(other_row),
    }
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        evidence_id = first_ids[first_assignment] if len(build_calls) == 1 else planned_id
        return _model_strategy(evidence_id=evidence_id)

    _patch_pipeline(monkeypatch, builder, ksa_rows=[planned_row, other_row])
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        lambda strategy, **_kwargs: _quality_result(strategy, passed=True),
    )

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert response.status_code == 200
    assert len(build_calls) == 2
    assert "question_evidence_assignment_failed" in build_calls[1]["extra_context"]
    assert planned_id in build_calls[1]["extra_context"]
    strategy = response.json()["strategy"]
    assert strategy["question_evidence_assignment"] == {
        "policy": "planned-question-evidence-assignment-v1",
        "applicable": True,
        "passed": True,
        "expected_count": 1,
        "matched_count": 1,
        "mismatch_count": 0,
        "mismatched_indexes": [],
    }
    assert strategy["model_quality_retry"]["trigger_codes"] == [
        "question_evidence_assignment_failed"
    ]
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize(
    ("missing_field", "expected_code"),
    [
        ("question_quality_report", "question_quality_report_missing"),
        (
            "question_quality_orchestration",
            "question_quality_orchestration_missing",
        ),
    ],
)
def test_primary_strict_classifier_rejects_missing_quality_metadata(
    missing_field: str,
    expected_code: str,
) -> None:
    result = _quality_result(_model_strategy(), passed=True)
    result.pop(missing_field)

    codes = main._institution_question_rejection_codes(
        result,
        require_quality_metadata=True,
    )

    assert expected_code in codes
    # Auxiliary endpoints still have their own official-KSA boundary and do not
    # pretend to emit the primary orchestration metadata.
    assert expected_code not in main._institution_question_rejection_codes(result)
