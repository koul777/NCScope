from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services.ncs_match_gate import build_ncs_match_bundle
from app.services.ncs_mcp_client import NcsMcpError


DETAIL_CODE = "02020301"
DETAIL_NAME = "비서"
REQUEST_KEY = "sk-request-scoped-ncs-match-gate-integration-key"


def _search_result(
    *,
    expected: int = 11,
    verified: int = 11,
    complete: bool = True,
    cap_limited: bool = False,
) -> dict[str, Any]:
    units = []
    for number in range(1, verified + 1):
        base_code = f"{DETAIL_CODE}{number:02d}"
        full_code = f"{base_code}_25v1"
        units.append(
            {
                "ncsClCd": full_code,
                "compeUnitName": f"비서 능력단위 {number}",
                "ncsSubdCdnm": DETAIL_NAME,
                "officialUnitBaseCode": base_code,
                "officialUnitName": f"비서 능력단위 {number}",
                "officialDetailCode": DETAIL_CODE,
                "officialDetailName": DETAIL_NAME,
                "unitCatalogVerified": True,
                "detailPathCodeVerified": True,
                "unitResolutionKind": "catalog_full_code_exact",
                "catalogUnitCodes": [full_code],
            }
        )
    return {
        "items": units,
        "exactCoverage": {
            "details": [
                {
                    "sourceDetailName": DETAIL_NAME,
                    "mappingState": "official_detail_resolved",
                    "officialDetailCode": DETAIL_CODE,
                    "officialDetailName": DETAIL_NAME,
                    "detailExpectedUnitBaseCount": expected,
                    "detailVerifiedUnitBaseCount": verified,
                    "detailRetrievalComplete": complete,
                    "detailRetrievalCapLimited": cap_limited,
                }
            ]
        },
    }


def _bundle(**overrides: Any) -> dict[str, Any]:
    result = _search_result(**overrides)
    return build_ncs_match_bundle(
        [DETAIL_NAME],
        search_fn=lambda *_args, **_kwargs: result,
    )


def _selected_unit() -> dict[str, Any]:
    return dict(_search_result()["items"][0])


def _payload() -> dict[str, Any]:
    unit = _selected_unit()
    return {
        "generation_provider": "openai_api",
        "openai_api_key": REQUEST_KEY,
        "notice_text": "한국마사회 비서 직무 채용 공고",
        "duty_text": "임원 일정 관리, 회의 준비, 대내외 연락을 담당한다.",
        "selected_ncs": [
            {
                "ncsClCd": unit["ncsClCd"],
                "compeUnitName": unit["compeUnitName"],
                "ncsSubdCdnm": unit["ncsSubdCdnm"],
            }
        ],
        "question_plan": {
            "items": [
                {
                    "detail": DETAIL_NAME,
                    "enabled": True,
                    "main_count": 1,
                    "follow_up_count": 3,
                }
            ]
        },
        "interview_methods": ["경험면접"],
    }


def _patch_manual_boundary(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    selected = _selected_unit()
    monkeypatch.setattr(
        main,
        "resolve_official_unit_selection",
        lambda *_args, **_kwargs: dict(selected),
    )
    ksa = AsyncMock()
    generator = AsyncMock()
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502_off_loop", ksa)
    monkeypatch.setattr(main, "_generate_quality_gated_institution_strategy", generator)
    return ksa, generator


def _upload_request_data() -> tuple[bytes, dict[str, Any]]:
    content = "비서 직무기술서".encode("utf-8")
    reviewed = {
        "document": {"markdown": "비서 직무기술서"},
        "fields": {"ncs_detail_candidates": [DETAIL_NAME]},
    }
    session = main._create_review_session(content, reviewed, "job.txt")
    return content, {
        **reviewed,
        "review_confirmed": True,
        "review_session_id": session["id"],
        "review_session": session,
    }


def _patch_upload_boundary(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.setattr(
        main,
        "resolve_sclass_candidates_bundle",
        lambda **_kwargs: {
            "csv_sclass_candidates": [],
            "verified_sclass": [{"sclass_name": DETAIL_NAME, "ncs_code_no": DETAIL_CODE}],
        },
    )
    ksa = AsyncMock()
    generator = AsyncMock()
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502_off_loop", ksa)
    monkeypatch.setattr(main, "_generate_quality_gated_institution_strategy", generator)
    return ksa, generator


@pytest.mark.parametrize(
    ("bundle", "expected_reasons"),
    [
        (
            _bundle(expected=11, verified=10, complete=False),
            {"detail_verified_count_mismatch", "unit_count_mismatch"},
        ),
        (
            _bundle(cap_limited=True),
            {"detail_retrieval_cap_limited"},
        ),
    ],
    ids=("ten-of-eleven", "cap-limited"),
)
def test_manual_generation_blocks_incomplete_detail_before_ksa_or_generation(
    monkeypatch: pytest.MonkeyPatch,
    bundle: dict[str, Any],
    expected_reasons: set[str],
) -> None:
    ksa, generator = _patch_manual_boundary(monkeypatch)
    monkeypatch.setattr(main, "build_ncs_match_bundle", lambda *_args, **_kwargs: bundle)

    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json=_payload())

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ncs_exact_complete_match_required"
    assert detail["match_status"] == "incomplete"
    assert expected_reasons <= set(detail["reason_codes"])
    ksa.assert_not_awaited()
    generator.assert_not_awaited()


def test_manual_generation_blocks_unavailable_detail_before_ksa_or_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ksa, generator = _patch_manual_boundary(monkeypatch)

    def unavailable(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise NcsMcpError("private upstream detail must not leak")

    unavailable_bundle = build_ncs_match_bundle([DETAIL_NAME], search_fn=unavailable)
    monkeypatch.setattr(
        main,
        "build_ncs_match_bundle",
        lambda *_args, **_kwargs: unavailable_bundle,
    )

    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json=_payload())

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "ncs_mcp_unavailable"
    assert detail["match_status"] == "unavailable"
    assert detail["retryable"] is True
    assert "private upstream detail" not in response.text
    ksa.assert_not_awaited()
    generator.assert_not_awaited()


def test_manual_generation_accepts_secretary_eleven_of_eleven_and_keeps_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ksa, generator = _patch_manual_boundary(monkeypatch)
    exact_bundle = _bundle()
    selected = _selected_unit()
    factor = {
        "ncsClCd": selected["ncsClCd"],
        "compeUnitName": selected["compeUnitName"],
        "factorName": "일정 변경 우선순위 판단",
        "elementName": "경영진 일정관리",
        "ksaTypeName": "기술",
        "ksaNo": "S-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    ksa.return_value = [factor]
    generator.return_value = {
        "interview_questions": [
            {
                "question": "서로 충돌하는 임원 일정을 조정했던 경험을 설명해 주세요.",
                "question_source": "openai_api",
                "ncsClCd": selected["ncsClCd"],
                "question_focus": factor["factorName"],
                "follow_ups": [
                    "우선순위 판단 기준은 무엇이었습니까?",
                    "이해관계자에게 어떻게 설명했습니까?",
                    "결과를 어떻게 확인했습니까?",
                ],
                "evaluation_points": [
                    "판단 기준의 명확성",
                    "관계자 조율 행동",
                    "보안과 정확성",
                    "결과 확인 방식",
                ],
            }
        ],
        "question_quality_report": {"passed": True},
        "question_quality_orchestration": {"status": "passed"},
    }
    monkeypatch.setattr(main, "build_ncs_match_bundle", lambda *_args, **_kwargs: exact_bundle)
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **_kwargs: [factor])
    monkeypatch.setattr(main, "_supplement_ksa_for_question_plan_off_loop", ksa)
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **_kwargs: {})
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_record_audit_event", lambda *_args, **_kwargs: None)

    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json=_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ncs_match_bundle"]["status"] == "exact_complete"
    assert len(body["ncs_match_bundle"]["units"]) == 11
    assert [row["ncsClCd"] for row in body["ncs_matches"]] == [selected["ncsClCd"]]
    assert generator.await_count == 1


def test_upload_generation_blocks_ten_of_eleven_before_ksa_or_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ksa, generator = _patch_upload_boundary(monkeypatch)
    list_only_search = AsyncMock()
    monkeypatch.setattr(main, "search_units_by_detail", list_only_search)
    monkeypatch.setattr(
        main,
        "build_ncs_match_bundle",
        lambda *_args, **_kwargs: _bundle(expected=11, verified=10, complete=False),
    )
    content, review = _upload_request_data()

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("job.txt", content, "text/plain")},
            data={
                "openai_api_key": REQUEST_KEY,
                "jd_review_json": json.dumps(review, ensure_ascii=False),
            },
        )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "ncs_exact_complete_match_required"
    list_only_search.assert_not_awaited()
    ksa.assert_not_awaited()
    generator.assert_not_awaited()


def test_upload_generation_uses_all_eleven_exact_units_before_ranking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ksa, generator = _patch_upload_boundary(monkeypatch)
    exact_bundle = _bundle()
    selected = _selected_unit()
    factor = {
        "ncsClCd": selected["ncsClCd"],
        "compeUnitName": selected["compeUnitName"],
        "factorName": "일정 변경 우선순위 판단",
        "elementName": "경영진 일정관리",
        "ksaTypeName": "기술",
        "ksaNo": "S-01",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    ksa.return_value = [factor]
    generator.return_value = {
        "interview_questions": [
            {
                "question": "서로 충돌하는 임원 일정을 조정했던 경험을 설명해 주세요.",
                "question_source": "openai_api",
                "ncsClCd": selected["ncsClCd"],
                "question_focus": factor["factorName"],
                "follow_ups": ["판단 기준은 무엇입니까?", "누구와 협의했습니까?", "결과는 어땠습니까?"],
                "evaluation_points": ["판단 기준", "조율 행동", "정확성", "결과 확인"],
            }
        ],
        "question_quality_report": {"passed": True},
        "question_quality_orchestration": {"status": "passed"},
    }
    captured: dict[str, Any] = {}

    def rerank(**kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        captured["unit_count"] = len(kwargs["ncs_items"])
        return [dict(kwargs["ncs_items"][0])], "rule"

    monkeypatch.setattr(main, "build_ncs_match_bundle", lambda *_args, **_kwargs: exact_bundle)
    monkeypatch.setattr(main, "rerank_ncs_matches", rerank)
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **_kwargs: [factor])
    monkeypatch.setattr(main, "_supplement_ksa_for_question_plan_off_loop", ksa)
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **_kwargs: {})
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_record_audit_event", lambda *_args, **_kwargs: None)
    content, review = _upload_request_data()

    with TestClient(main.app) as client:
        response = client.post(
            "/api/jd/strategy/upload",
            files={"jd_file": ("job.txt", content, "text/plain")},
            data={
                "openai_api_key": REQUEST_KEY,
                "jd_review_json": json.dumps(review, ensure_ascii=False),
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert captured["unit_count"] == 11
    assert body["ncs_match_bundle"]["status"] == "exact_complete"
    assert len(body["ncs_match_bundle"]["units"]) == 11
    assert len(body["ncs_matches"]) == 11
    assert {
        str(row["ncsClCd"]).split("_", 1)[0]
        for row in body["ncs_matches"]
    } == {
        str(row["ncsClCd"]).split("_", 1)[0]
        for row in exact_bundle["units"]
    }
    assert generator.await_count == 1

