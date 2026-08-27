from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import main


def _forbid_generation_work(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("input capacity validation must precede provider and NCS work")

    monkeypatch.setattr(main, "_resolve_request_generation", unexpected)
    monkeypatch.setattr(main, "_require_ncs_mcp_url", unexpected)
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502", unexpected)


def _assert_capacity_error(response: Any, field: str) -> None:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "generation_input_capacity_exceeded"
    assert detail["field"] == field
    assert detail["retryable"] is False


def test_unknown_ncs_unit_returns_actionable_422_before_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(**_kwargs: Any) -> list[dict[str, Any]]:
        raise main.NcsMcpError("NCS MCP returned no official KSA rows")

    monkeypatch.setattr(main, "fetch_ncs_ksa_by_units", unavailable)
    with pytest.raises(main.HTTPException) as exc_info:
        main._fetch_ncs_ksa_or_502(
            ncs_matches=[{"ncsClCd": "0201010101_25v3"}],
            max_units=1,
            max_factors_per_unit=2,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "code": "ncs_ksa_unavailable",
        "message": "선택한 NCS 세분류에서 공식 KSA 근거를 찾지 못했습니다. 목록에서 다른 세분류를 선택해 주세요.",
        "retryable": False,
    }


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("notice_text", 12000),
        ("duty_text", 3000),
        ("qualification_text", 2400),
        ("preference_text", 2400),
        ("evaluation_text", 2400),
        ("strengths", 2000),
    ],
)
def test_from_text_rejects_oversized_direct_text_before_generation_work(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    limit: int,
) -> None:
    _forbid_generation_work(monkeypatch)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={field: "가" * (limit + 1)},
        )
    _assert_capacity_error(response, field)


@pytest.mark.parametrize(
    ("selected_ncs", "field"),
    [
        ([{"ncsClCd": f"02020{index}"} for index in range(6)], "selected_ncs"),
        ([{"ncsClCd": "1" * 33}], "selected_ncs[0].ncsClCd"),
        ([{"ncsClCd": "0202/../../etc"}], "selected_ncs[0].ncsClCd"),
        ([{"ncsClCd": "02020302", "compeUnitName": "가" * 121}], "selected_ncs[0].compeUnitName"),
        ([{"ncsClCd": "02020302", "ncsSubdCdnm": "가" * 121}], "selected_ncs[0].ncsSubdCdnm"),
        ([{"ncsClCd": "02020302", "compeUnitDef": "가" * 1001}], "selected_ncs[0].compeUnitDef"),
    ],
)
def test_from_text_rejects_oversized_or_unsafe_selected_ncs_before_generation_work(
    monkeypatch: pytest.MonkeyPatch,
    selected_ncs: list[dict[str, str]],
    field: str,
) -> None:
    _forbid_generation_work(monkeypatch)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={"notice_text": "공고", "selected_ncs": selected_ncs},
        )
    _assert_capacity_error(response, field)


def test_from_text_rejects_multiple_interview_methods_before_generation_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_generation_work(monkeypatch)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={
                "notice_text": "공고",
                "selected_ncs": [{"ncsClCd": "02020302", "ncsSubdCdnm": "경영기획"}],
                "interview_methods": ["경험면접", "상황면접"],
            },
        )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "interview_method_capacity_exceeded"
    assert detail["max_interview_methods"] == 1
    assert detail["retryable"] is False


def test_from_text_rejects_multiple_ncs_details_before_ncs_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("multiple NCS details must be rejected before NCS work")

    monkeypatch.setattr(main, "_require_ncs_mcp_url", unexpected)
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502", unexpected)
    monkeypatch.setattr(
        main,
        "_resolve_request_generation",
        lambda **_kwargs: ("openrouter_api", "stealth/ox-alpha", "sk-or-test"),
    )
    monkeypatch.setattr(main, "_require_allowed_openai_key", lambda *_args, **_kwargs: None)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={
                "notice_text": "공고",
                "selected_ncs": [
                    {
                        "ncsClCd": "0201010103_22v2",
                        "compeUnitName": "경영계획 수립",
                        "ncsSubdCdnm": "경영기획",
                    },
                    {
                        "ncsClCd": "0202030201_22v3",
                        "compeUnitName": "문서 작성",
                        "ncsSubdCdnm": "사무행정",
                    },
                ],
                "question_plan": {
                    "items": [
                        {"detail": "경영기획", "enabled": True, "main_count": 1},
                        {"detail": "사무행정", "enabled": True, "main_count": 1},
                    ]
                },
                "interview_methods": ["경험면접"],
            },
        )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "ncs_detail_capacity_exceeded"
    assert detail["max_ncs_details"] == 1
    assert detail["retryable"] is False


@pytest.mark.parametrize(
    ("path", "payload", "field"),
    [
        ("/api/questions/generate-personalized", {"ncs_code": "0202/unsafe"}, "ncs_code"),
        ("/api/questions/generate-by-ncs-code", {"ncs_code": "1" * 33}, "ncs_code"),
        ("/api/questions/generate-batch", {"competency_name": "가" * 121}, "competency_name"),
        ("/api/questions/generate-diverse", {"job_posting": "가" * 12001}, "job_posting"),
        ("/api/questions/generate-personalized", {"user_profile": "가" * 2001}, "user_profile"),
    ],
)
def test_auxiliary_routes_reject_unsafe_generation_input_before_generation_work(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, Any],
    field: str,
) -> None:
    _forbid_generation_work(monkeypatch)
    payload.setdefault("target_count", 1)
    if path.endswith("generate-batch"):
        payload.pop("target_count", None)
        payload["batch_count"] = 1
    with TestClient(main.app) as client:
        response = client.post(path, json=payload)
    _assert_capacity_error(response, field)


@pytest.mark.parametrize(
    ("path", "field", "value", "expected_field"),
    [
        ("/api/questions/generate-from-text", "avoid_questions", ["질문"] * 51, "avoid_questions"),
        ("/api/questions/generate-by-ncs-code", "current_questions", ["가" * 301], "current_questions[0]"),
        ("/api/questions/generate-batch", "currentQuestions", ["질문"] * 51, "currentQuestions"),
        (
            "/api/questions/generate-diverse",
            "avoid_questions_json",
            json.dumps(["가" * 301], ensure_ascii=False),
            "avoid_questions_json[0]",
        ),
    ],
)
def test_question_history_limits_are_rejected_before_generation_work(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    field: str,
    value: Any,
    expected_field: str,
) -> None:
    _forbid_generation_work(monkeypatch)
    payload: dict[str, Any] = {field: value}
    if path.endswith("generate-from-text"):
        payload.update({"notice_text": "공고", "selected_ncs": [{"ncsClCd": "02020302"}]})
    else:
        payload["ncs_code"] = "02020302"
    if path.endswith("generate-batch"):
        payload["batch_count"] = 1
    else:
        payload["target_count"] = 1
    with TestClient(main.app) as client:
        response = client.post(path, json=payload)
    _assert_capacity_error(response, expected_field)


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("strengths", 2000),
        ("duty_text", 3000),
        ("qualification_text", 2400),
        ("preference_text", 2400),
        ("evaluation_text", 2400),
    ],
)
def test_upload_rejects_oversized_direct_form_text_before_generation_work(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    limit: int,
) -> None:
    _forbid_generation_work(monkeypatch)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("jd.txt", "직무기술서", "text/plain")},
            data={field: "가" * (limit + 1)},
        )
    _assert_capacity_error(response, field)


def test_upload_rejects_oversized_question_history_before_generation_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_generation_work(monkeypatch)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("jd.txt", "직무기술서", "text/plain")},
            data={"avoid_questions_json": json.dumps(["질문"] * 51, ensure_ascii=False)},
        )
    _assert_capacity_error(response, "avoid_questions_json")


@pytest.mark.parametrize("path", ["/api/alio/recommend", "/api/alio/strategy"])
def test_legacy_ai_routes_reject_oversized_strengths_before_external_work(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("oversized strengths must not reach an external service")

    monkeypatch.setattr(main, "fetch_recruitment", unexpected)
    monkeypatch.setattr(main, "rank_postings_with_openai", unexpected)
    monkeypatch.setattr(main, "build_strategy_with_openai", unexpected)
    with TestClient(main.app) as client:
        if path.endswith("recommend"):
            response = client.get(
                path,
                params={"desired_job": "전기", "strengths": "가" * 2001},
            )
        else:
            response = client.post(
                path,
                json={"desired_job": "전기", "posting_id": "1", "strengths": "가" * 2001},
            )
    _assert_capacity_error(response, "strengths")


def test_legacy_alio_strategy_uses_supported_builder_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posting = {
        "posting_id": "posting-1",
        "title": "행정직",
        "institution_name": "공공기관",
        "region": "서울",
        "r6000": "R6000_MANAGEMENT",
        "jd_text": "행정 기획 및 운영",
    }
    captured: dict[str, Any] = {}

    def fake_builder(
        desired_job: str,
        strengths: str,
        posting_data: dict[str, Any],
    ) -> dict[str, Any]:
        captured.update(
            desired_job=desired_job,
            strengths=strengths,
            posting_data=posting_data,
        )
        return {"job_title": desired_job}

    monkeypatch.setitem(main._ALIO_CACHE, "posting-1", posting)
    monkeypatch.setattr(main, "build_strategy_with_openai", fake_builder)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/alio/strategy",
            json={
                "desired_job": "행정직",
                "desired_region": "서울",
                "strengths": "공공기관 행정 기획 경험",
                "posting_id": "posting-1",
            },
        )

    assert response.status_code == 200
    assert response.json()["strategy"] == {"job_title": "행정직"}
    assert captured == {
        "desired_job": "행정직",
        "strengths": "공공기관 행정 기획 경험",
        "posting_data": posting,
    }
