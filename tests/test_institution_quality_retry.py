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


def _generation_payload_with_plan(
    *,
    main_count: int,
    interview_methods: list[str],
) -> dict[str, Any]:
    return {
        **_generation_payload(),
        "question_plan": {
            "items": [
                {
                    "detail": "경영기획",
                    "enabled": True,
                    "main_count": main_count,
                    "follow_up_count": 3,
                }
            ]
        },
        "interview_methods": list(interview_methods),
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
    return _post_primary_with_payload(client, path, _generation_payload())


def _post_primary_with_payload(
    client: TestClient,
    path: str,
    payload: dict[str, Any],
):
    if path.endswith("generate-from-text"):
        return client.post(path, json=payload)

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
            "openai_api_key": payload["openai_api_key"],
            "generation_provider": payload["generation_provider"],
            "question_plan_json": json.dumps(
                payload["question_plan"],
                ensure_ascii=False,
            ),
            "interview_methods_json": json.dumps(
                payload["interview_methods"],
                ensure_ascii=False,
            ),
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


def _question_row(
    *,
    question: str,
    method: str,
    evidence_id: str,
    focus: str,
) -> dict[str, Any]:
    return {
        "type": method,
        "question": question,
        "question_source": "openai_api",
        "ncsClCd": _unit()["ncsClCd"],
        "question_focus": focus,
        "ksa_refs": [focus],
        "question_evidence_id": evidence_id,
        "follow_ups": [
            f"{question} 후속 1",
            f"{question} 후속 2",
            f"{question} 후속 3",
        ],
        "evaluation_points": [
            f"{question} 평가 1",
            f"{question} 평가 2",
            f"{question} 평가 3",
            f"{question} 평가 4",
        ],
    }


def _strategy_with_questions(questions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"interview_questions": [dict(item) for item in questions]}


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
        "question_plan",
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
    assert build_calls[1]["question_plan"]["items"] == build_calls[0]["question_plan"]["items"]
    assert build_calls[1]["question_plan"]["selected_items"] == build_calls[0]["question_plan"]["selected_items"]
    assert build_calls[1]["question_plan"]["total_main_count"] == 1
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
def test_primary_endpoint_returns_evidence_grounded_model_draft_for_soft_review_findings(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        return _model_strategy()

    def soft_review(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {
            **strategy,
            "question_quality_report": {
                "passed": False,
                "summary": {
                    "question_count": 1,
                    "expected_question_count": 1,
                    "count_matches_plan": True,
                    "ready_count": 0,
                    "needs_review_count": 1,
                },
                "items": [
                    {
                        "index": 1,
                        "ready": False,
                        "issues": ["natural_wording"],
                        "realism_issue_codes": ["candidate_checklist"],
                        "precision_grounding_issue_codes": [],
                    }
                ],
            },
            "question_quality_orchestration": {
                "status": "needs_review",
                "unresolved_count": 1,
                "items": [
                    {
                        "index": 1,
                        "final_issues": ["full_quality_natural_wording"],
                    }
                ],
            },
        }

    _patch_pipeline(monkeypatch, builder)
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", soft_review)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert response.status_code == 200
    assert len(build_calls) == 1
    strategy = response.json()["strategy"]
    assert strategy["question_release_status"] == "human_review_required"
    retry = strategy["model_quality_retry"]
    assert retry["outcome"] == "accepted_for_human_review"
    assert retry["attempted"] is False
    assert retry["attempt_count"] == 1
    assert "natural_wording" in retry["remaining_issue_codes"]
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
@pytest.mark.parametrize(
    ("hard_issue", "hard_realism_issue"),
    [
        ("ksa_grounded", None),
        ("ksa_measurement_task", None),
        ("candidate_surface_safe", None),
        ("blind_hiring_safe", None),
        (None, "instruction_injection_artifact"),
        (None, "label_like_metadata_exposure"),
    ],
)
def test_primary_endpoint_still_rejects_unresolved_hard_quality_findings(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    hard_issue: str | None,
    hard_realism_issue: str | None,
) -> None:
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        return _model_strategy()

    def hard_review(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        issues = [hard_issue] if hard_issue else []
        realism_issues = [hard_realism_issue] if hard_realism_issue else []
        return {
            **strategy,
            "question_quality_report": {
                "passed": False,
                "summary": {
                    "question_count": 1,
                    "expected_question_count": 1,
                    "count_matches_plan": True,
                    "ready_count": 0,
                    "needs_review_count": 1,
                },
                "items": [
                    {
                        "index": 1,
                        "ready": False,
                        "issues": issues,
                        "realism_issue_codes": realism_issues,
                        "precision_grounding_issue_codes": [],
                    }
                ],
            },
            "question_quality_orchestration": {
                "status": "needs_review",
                "unresolved_count": 1,
                "items": [
                    {
                        "index": 1,
                        "final_issues": [
                            f"full_quality_{hard_issue or hard_realism_issue}"
                        ],
                    }
                ],
            },
        }

    _patch_pipeline(monkeypatch, builder)
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", hard_review)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary(client, path)

    assert response.status_code == 502
    assert len(build_calls) == 2
    assert response.json()["detail"]["code"] == "openai_api_quality_rejected"
    assert "accepted_for_human_review" not in response.text
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
def test_primary_endpoint_targeted_retry_rebuilds_only_failed_slot_and_revalidates_merge(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    methods = ["경험면접", "상황면접", "발표면접"]
    payload = _generation_payload_with_plan(main_count=3, interview_methods=methods)
    ksa_rows = [
        _ksa("첫 번째 KSA", "1"),
        _ksa("두 번째 KSA", "2"),
        _ksa("세 번째 KSA", "3"),
    ]
    evidence_ids = [stable_ksa_evidence_id(row) for row in ksa_rows]
    original_questions = [
        _question_row(
            question="원본 문항 1",
            method="경험면접",
            evidence_id=evidence_ids[0],
            focus=ksa_rows[0]["factorName"],
        ),
        _question_row(
            question="원본 문항 2",
            method="상황면접",
            evidence_id=evidence_ids[1],
            focus=ksa_rows[1]["factorName"],
        ),
        _question_row(
            question="원본 문항 3",
            method="발표면접",
            evidence_id=evidence_ids[2],
            focus=ksa_rows[2]["factorName"],
        ),
    ]
    retried_question = _question_row(
        question="재생성 문항 2",
        method="상황면접",
        evidence_id=evidence_ids[1],
        focus=ksa_rows[1]["factorName"],
    )
    build_calls: list[dict[str, Any]] = []
    orchestration_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        if len(build_calls) == 1:
            return _strategy_with_questions(original_questions)
        return _strategy_with_questions([retried_question])

    def quality(strategy: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        questions = [
            dict(item)
            for item in (strategy.get("interview_questions") or [])
            if isinstance(item, dict)
        ]
        orchestration_calls.append(
            {
                "questions": [str(item.get("question") or "") for item in questions],
                "methods": [str(item.get("type") or "") for item in questions],
                "plan": copy.deepcopy(kwargs.get("question_plan") or {}),
            }
        )
        if len(orchestration_calls) == 1:
            return {
                **strategy,
                "question_quality_report": {
                    "passed": False,
                    "summary": {
                        "question_count": 3,
                        "expected_question_count": 3,
                        "count_matches_plan": True,
                        "ready_count": 2,
                        "needs_review_count": 1,
                    },
                    "items": [
                        {"index": 1, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                        {"index": 2, "ready": False, "issues": ["ksa_grounded"], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                        {"index": 3, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                    ],
                },
                "question_quality_orchestration": {
                    "status": "needs_review",
                    "unresolved_count": 1,
                    "items": [{"index": 2, "final_issues": ["full_quality_ksa_grounded"]}],
                },
            }
        if len(orchestration_calls) == 2:
            return {
                **strategy,
                "question_quality_report": {
                    "passed": True,
                    "summary": {
                        "question_count": 1,
                        "expected_question_count": 1,
                        "count_matches_plan": True,
                        "ready_count": 1,
                        "needs_review_count": 0,
                    },
                    "items": [
                        {"index": 1, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []}
                    ],
                },
                "question_quality_orchestration": {
                    "status": "passed",
                    "unresolved_count": 0,
                    "items": [{"index": 1, "final_issues": []}],
                },
            }
        return {
            **strategy,
            "question_quality_report": {
                "passed": True,
                "summary": {
                    "question_count": 3,
                    "expected_question_count": 3,
                    "count_matches_plan": True,
                    "ready_count": 3,
                    "needs_review_count": 0,
                },
                "items": [
                    {"index": 1, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                    {"index": 2, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                    {"index": 3, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                ],
            },
            "question_quality_orchestration": {
                "status": "passed",
                "unresolved_count": 0,
                "items": [
                    {"index": 1, "final_issues": []},
                    {"index": 2, "final_issues": []},
                    {"index": 3, "final_issues": []},
                ],
            },
        }

    _patch_pipeline(monkeypatch, builder, ksa_rows=ksa_rows)
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", quality)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary_with_payload(client, path, payload)

    assert response.status_code == 200
    assert len(build_calls) == 2
    retry_call = build_calls[1]
    assert retry_call["target_count_override"] == 1
    assert retry_call["interview_methods"] == ["상황면접"]
    assert retry_call["max_model_requests"] == 1
    assert retry_call["transport_max_attempts"] == 1
    retry_plan = retry_call["question_plan"]
    assert retry_plan["targeted_retry"] is True
    assert retry_plan["targeted_retry_original_indexes"] == [2]
    assert retry_plan["total_main_count"] == 1
    assert len(retry_plan["question_sequence"]) == 1
    assert retry_plan["question_sequence"][0]["index"] == 1
    assert retry_plan["question_sequence"][0]["type"] == "상황면접"
    assert evidence_ids[1] in retry_call["extra_context"]
    assert evidence_ids[0] not in retry_call["extra_context"]
    assert evidence_ids[2] not in retry_call["extra_context"]

    assert [len(call["questions"]) for call in orchestration_calls] == [3, 1, 3]
    assert orchestration_calls[1]["methods"] == ["상황면접"]
    assert orchestration_calls[2]["questions"] == [
        "원본 문항 1",
        "재생성 문항 2",
        "원본 문항 3",
    ]

    strategy = response.json()["strategy"]
    assert [item["question"] for item in strategy["interview_questions"]] == [
        "원본 문항 1",
        "재생성 문항 2",
        "원본 문항 3",
    ]
    assert [item["type"] for item in strategy["interview_questions"]] == methods
    assert strategy["question_evidence_assignment"] == {
        "policy": "planned-question-evidence-assignment-v1",
        "applicable": True,
        "passed": True,
        "expected_count": 3,
        "matched_count": 3,
        "mismatch_count": 0,
        "mismatched_indexes": [],
    }
    assert strategy["model_quality_retry"] == {
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
        "previous_candidate_count": 3,
        "evidence_lock_count": 3,
        "provider_generation_request_count": 0,
        "provider_generation_request_limit": 3,
        "transport_attempt_limit_per_generation_request": 1,
        "retry_scope": "failed_questions",
        "retried_question_count": 1,
        "retried_indexes": [2],
        "retry_evidence_lock_count": 1,
    }
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
def test_primary_endpoint_returns_partial_safe_questions_after_targeted_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    methods = ["경험면접", "상황면접", "발표면접"]
    payload = _generation_payload_with_plan(main_count=3, interview_methods=methods)
    ksa_rows = [
        _ksa("첫 번째 KSA", "1"),
        _ksa("두 번째 KSA", "2"),
        _ksa("세 번째 KSA", "3"),
    ]
    evidence_ids = [stable_ksa_evidence_id(row) for row in ksa_rows]
    original_questions = [
        _question_row(
            question="원본 문항 1",
            method="경험면접",
            evidence_id=evidence_ids[0],
            focus=ksa_rows[0]["factorName"],
        ),
        _question_row(
            question="원본 문항 2",
            method="상황면접",
            evidence_id=evidence_ids[1],
            focus=ksa_rows[1]["factorName"],
        ),
        _question_row(
            question="원본 문항 3",
            method="발표면접",
            evidence_id=evidence_ids[2],
            focus=ksa_rows[2]["factorName"],
        ),
    ]
    failed_retry_question = _question_row(
        question="재생성 실패 문항 2",
        method="상황면접",
        evidence_id=evidence_ids[1],
        focus=ksa_rows[1]["factorName"],
    )
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        if len(build_calls) == 1:
            return _strategy_with_questions(original_questions)
        return _strategy_with_questions([failed_retry_question])

    quality_calls = 0

    def quality(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal quality_calls
        quality_calls += 1
        if quality_calls == 1:
            return {
                **strategy,
                "question_quality_report": {
                    "passed": False,
                    "summary": {
                        "question_count": 3,
                        "expected_question_count": 3,
                        "count_matches_plan": True,
                        "ready_count": 2,
                        "needs_review_count": 1,
                    },
                    "items": [
                        {"index": 1, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                        {"index": 2, "ready": False, "issues": ["ksa_grounded"], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                        {"index": 3, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                    ],
                },
                "question_quality_orchestration": {
                    "status": "needs_review",
                    "unresolved_count": 1,
                    "items": [{"index": 2, "final_issues": ["full_quality_ksa_grounded"]}],
                },
            }
        if quality_calls == 2:
            return {
                **strategy,
                "question_quality_report": {
                    "passed": False,
                    "summary": {
                        "question_count": 1,
                        "expected_question_count": 1,
                        "count_matches_plan": True,
                        "ready_count": 0,
                        "needs_review_count": 1,
                    },
                    "items": [
                        {"index": 1, "ready": False, "issues": ["ksa_grounded"], "realism_issue_codes": [], "precision_grounding_issue_codes": []}
                    ],
                },
                "question_quality_orchestration": {
                    "status": "needs_review",
                    "unresolved_count": 1,
                    "items": [{"index": 1, "final_issues": ["full_quality_ksa_grounded"]}],
                },
            }
        return {
            **strategy,
            "question_quality_report": {
                "passed": False,
                "summary": {
                    "question_count": 3,
                    "expected_question_count": 3,
                    "count_matches_plan": True,
                    "ready_count": 2,
                    "needs_review_count": 1,
                },
                "items": [
                    {"index": 1, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                    {"index": 2, "ready": False, "issues": ["ksa_grounded"], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                    {"index": 3, "ready": True, "issues": [], "realism_issue_codes": [], "precision_grounding_issue_codes": []},
                ],
            },
            "question_quality_orchestration": {
                "status": "needs_review",
                "unresolved_count": 1,
                "items": [{"index": 2, "final_issues": ["full_quality_ksa_grounded"]}],
            },
        }

    _patch_pipeline(monkeypatch, builder, ksa_rows=ksa_rows)
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", quality)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary_with_payload(client, path, payload)

    assert response.status_code == 200
    assert len(build_calls) == 2
    strategy = response.json()["strategy"]
    assert [item["question"] for item in strategy["interview_questions"]] == [
        "원본 문항 1",
        "원본 문항 3",
    ]
    assert strategy["question_release_status"] == "partial_human_review_required"
    assert strategy["partial_generation"] == {
        "policy": "hard-failure-isolation-v1",
        "requested_question_count": 3,
        "returned_question_count": 2,
        "omitted_question_count": 1,
        "omitted_indexes": [2],
        "omission_reasons_by_index": {
            "2": ["ksa_grounded", "full_quality_ksa_grounded"],
        },
    }
    assert strategy["model_quality_retry"] == {
        "policy": "institution-openai-quality-retry-v1",
        "provider": "openai_api",
        "attempted": True,
        "retry_count": 1,
        "attempt_count": 2,
        "outcome": "partial_after_retry",
        "trigger_codes": [
            "question_quality_orchestration_failed",
            "question_quality_report_failed",
        ],
        "remaining_codes": [
            "question_quality_orchestration_failed",
            "question_quality_report_failed",
        ],
        "remaining_issue_codes": ["ksa_grounded", "full_quality_ksa_grounded"],
        "previous_candidate_count": 3,
        "evidence_lock_count": 3,
        "provider_generation_request_count": 0,
        "provider_generation_request_limit": 3,
        "transport_attempt_limit_per_generation_request": 1,
        "retry_scope": "failed_questions",
        "retried_question_count": 1,
        "retried_indexes": [2],
        "retry_evidence_lock_count": 1,
    }
    assert REQUEST_KEY not in response.text


@pytest.mark.parametrize("path", PRIMARY_PATHS)
def test_primary_endpoint_targeted_retry_stress_keeps_twenty_question_order_and_timing(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    total_count = 20
    failed_index = 11
    selected_methods = ["경험면접", "상황면접", "발표면접", "토론면접"]
    method_by_index = [
        selected_methods[(index - 1) % len(selected_methods)]
        for index in range(1, total_count + 1)
    ]
    payload = _generation_payload_with_plan(
        main_count=total_count,
        interview_methods=selected_methods,
    )
    ksa_rows = [
        _ksa(f"{index}번째 KSA", str(index))
        for index in range(1, total_count + 1)
    ]
    evidence_ids = [stable_ksa_evidence_id(row) for row in ksa_rows]
    original_questions = [
        _question_row(
            question=f"원본 문항 {index}",
            method=method_by_index[index - 1],
            evidence_id=evidence_ids[index - 1],
            focus=ksa_rows[index - 1]["factorName"],
        )
        for index in range(1, total_count + 1)
    ]
    retried_question = _question_row(
        question=f"재생성 문항 {failed_index}",
        method=method_by_index[failed_index - 1],
        evidence_id=evidence_ids[failed_index - 1],
        focus=ksa_rows[failed_index - 1]["factorName"],
    )
    build_calls: list[dict[str, Any]] = []
    orchestration_calls: list[dict[str, Any]] = []

    def planned_assignments(**kwargs: Any) -> tuple[dict[str, Any], list[tuple[int, str]]]:
        question_plan = dict(kwargs["question_plan"])
        source_sequence = [
            dict(item)
            for item in (question_plan.get("question_sequence") or [])
            if isinstance(item, dict)
        ]
        if source_sequence and (
            question_plan.get("generation_batch") or question_plan.get("targeted_retry")
        ):
            original_indexes = [
                int(
                    item.get("generation_original_index")
                    or item.get("retry_original_index")
                    or item.get("index")
                    or fallback_index
                )
                for fallback_index, item in enumerate(source_sequence, start=1)
            ]
        else:
            targeted_indexes = list(
                question_plan.get("targeted_retry_original_indexes") or []
            )
            original_indexes = (
                targeted_indexes
                if question_plan.get("targeted_retry")
                else list(range(1, total_count + 1))
            )
        question_sequence = []
        evidence_locks = []
        for retry_index, original_index in enumerate(original_indexes, start=1):
            question_sequence.append(
                {
                    "index": retry_index,
                    "type": method_by_index[original_index - 1],
                    "ncsClCd": _unit()["ncsClCd"],
                    "detail": "경영기획",
                    "compeUnitName": _unit()["compeUnitName"],
                    "evidence_id": evidence_ids[original_index - 1],
                    "generation_original_index": original_index,
                    "retry_original_index": original_index,
                }
            )
            evidence_locks.append((retry_index, evidence_ids[original_index - 1]))
        runtime_plan = dict(question_plan)
        runtime_plan["question_sequence"] = question_sequence
        runtime_plan["total_main_count"] = len(question_sequence)
        return runtime_plan, evidence_locks

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        question_plan = kwargs["question_plan"]
        question_sequence = [
            dict(item)
            for item in (question_plan.get("question_sequence") or [])
            if isinstance(item, dict)
        ]
        questions: list[dict[str, Any]] = []
        for fallback_index, item in enumerate(question_sequence, start=1):
            original_index = int(
                item.get("generation_original_index")
                or item.get("retry_original_index")
                or item.get("index")
                or fallback_index
            )
            if question_plan.get("targeted_retry"):
                questions.append(dict(retried_question))
                continue
            questions.append(dict(original_questions[original_index - 1]))
        return _strategy_with_questions(questions)

    def quality(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        questions = [
            dict(item)
            for item in (strategy.get("interview_questions") or [])
            if isinstance(item, dict)
        ]
        orchestration_calls.append(
            {
                "questions": [str(item.get("question") or "") for item in questions],
                "count": len(questions),
            }
        )
        if len(orchestration_calls) == 1:
            items = []
            for index in range(1, total_count + 1):
                items.append(
                    {
                        "index": index,
                        "ready": index != failed_index,
                        "issues": ["ksa_grounded"] if index == failed_index else [],
                        "realism_issue_codes": [],
                        "precision_grounding_issue_codes": [],
                    }
                )
            return {
                **strategy,
                "question_quality_report": {
                    "passed": False,
                    "summary": {
                        "question_count": total_count,
                        "expected_question_count": total_count,
                        "count_matches_plan": True,
                        "ready_count": total_count - 1,
                        "needs_review_count": 1,
                    },
                    "items": items,
                },
                "question_quality_orchestration": {
                    "status": "needs_review",
                    "unresolved_count": 1,
                    "items": [
                        {
                            "index": failed_index,
                            "final_issues": ["full_quality_ksa_grounded"],
                        }
                    ],
                },
            }
        if len(orchestration_calls) == 2:
            return {
                **strategy,
                "question_quality_report": {
                    "passed": True,
                    "summary": {
                        "question_count": 1,
                        "expected_question_count": 1,
                        "count_matches_plan": True,
                        "ready_count": 1,
                        "needs_review_count": 0,
                    },
                    "items": [
                        {
                            "index": 1,
                            "ready": True,
                            "issues": [],
                            "realism_issue_codes": [],
                            "precision_grounding_issue_codes": [],
                        }
                    ],
                },
                "question_quality_orchestration": {
                    "status": "passed",
                    "unresolved_count": 0,
                    "items": [{"index": 1, "final_issues": []}],
                },
            }
        merged_items = [
            {
                "index": index,
                "ready": True,
                "issues": [],
                "realism_issue_codes": [],
                "precision_grounding_issue_codes": [],
            }
            for index in range(1, total_count + 1)
        ]
        return {
            **strategy,
            "question_quality_report": {
                "passed": True,
                "summary": {
                    "question_count": total_count,
                    "expected_question_count": total_count,
                    "count_matches_plan": True,
                    "ready_count": total_count,
                    "needs_review_count": 0,
                },
                "items": merged_items,
            },
            "question_quality_orchestration": {
                "status": "passed",
                "unresolved_count": 0,
                "items": [
                    {"index": index, "final_issues": []}
                    for index in range(1, total_count + 1)
                ],
            },
        }

    _patch_pipeline(monkeypatch, builder, ksa_rows=ksa_rows)
    monkeypatch.setattr(
        main,
        "_planned_question_evidence_assignments",
        planned_assignments,
    )
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", quality)

    with TestClient(main.app, client=REMOTE_CLIENT) as client:
        response = _post_primary_with_payload(client, path, payload)

    assert response.status_code == 200
    assert len(build_calls) == 5
    initial_calls = sorted(
        build_calls[:4],
        key=lambda call: min(call["question_plan"]["generation_batch_original_indexes"]),
    )
    for expected_indexes, batch_call in zip(
        [
            list(range(1, 6)),
            list(range(6, 11)),
            list(range(11, 16)),
            list(range(16, 21)),
        ],
        initial_calls,
        strict=True,
    ):
        expected_batch_methods = [method_by_index[index - 1] for index in expected_indexes]
        assert batch_call["target_count_override"] == 5
        assert batch_call["question_plan"]["generation_batch"] is True
        assert batch_call["question_plan"]["generation_batch_original_indexes"] == expected_indexes
        assert batch_call["question_plan"]["total_main_count"] == 5
        assert len(batch_call["question_plan"]["question_sequence"]) == 5
        assert [
            item["type"] for item in batch_call["question_plan"]["question_sequence"]
        ] == expected_batch_methods
        assert batch_call["interview_methods"] == list(dict.fromkeys(expected_batch_methods))

    retry_call = build_calls[4]
    assert retry_call["target_count_override"] == 1
    assert retry_call["question_plan"]["targeted_retry"] is True
    assert retry_call["question_plan"]["targeted_retry_original_indexes"] == [failed_index]
    assert retry_call["question_plan"]["total_main_count"] == 1
    assert retry_call["interview_methods"] == [method_by_index[failed_index - 1]]
    assert evidence_ids[failed_index - 1] in retry_call["extra_context"]

    strategy = response.json()["strategy"]
    assert len(strategy["interview_questions"]) == total_count
    expected_questions = [
        f"원본 문항 {index}"
        for index in range(1, total_count + 1)
    ]
    expected_questions[failed_index - 1] = f"재생성 문항 {failed_index}"
    assert [item["question"] for item in strategy["interview_questions"]] == expected_questions
    assert [item["type"] for item in strategy["interview_questions"]] == method_by_index
    assert [item["question_evidence_id"] for item in strategy["interview_questions"]] == evidence_ids
    assert [call["count"] for call in orchestration_calls] == [total_count, 1, total_count]
    assert orchestration_calls[0]["questions"] == [item["question"] for item in original_questions]
    assert orchestration_calls[1]["questions"] == [retried_question["question"]]
    assert orchestration_calls[2]["questions"] == expected_questions
    assert strategy["generation_batching"] == {
        "applied": True,
        "policy": "locked-plan-parallel-batches-v1",
        "batch_count": 4,
        "batch_size_limit": 5,
        "max_concurrency": 4,
        "batch_question_counts": [5, 5, 5, 5],
    }
    assert strategy["model_quality_retry"]["provider_generation_request_count"] == 0
    assert strategy["model_quality_retry"]["provider_generation_request_limit"] == 12

    timing = strategy["generation_timing"]
    assert timing["generation_attempt_count"] >= 2
    assert timing["total_elapsed_ms"] >= 0
    assert all(value >= 0 for value in timing["generation_attempt_elapsed_ms"])
    assert REQUEST_KEY not in response.text


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
