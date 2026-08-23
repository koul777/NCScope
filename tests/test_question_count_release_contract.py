from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services import jd_strategy
from app.services.question_surface import stable_ksa_evidence_id
from tests import test_institution_quality_retry as quality_retry


PRIMARY_PATH = "/api/questions/generate-from-text"


def _three_question_fixture() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    methods = ["경험면접", "상황면접", "발표면접"]
    ksa_rows = [
        quality_retry._ksa("첫 번째 KSA", "1"),
        quality_retry._ksa("두 번째 KSA", "2"),
        quality_retry._ksa("세 번째 KSA", "3"),
    ]
    questions = [
        quality_retry._question_row(
            question=f"검증 질문 {index}",
            method=methods[index - 1],
            evidence_id=stable_ksa_evidence_id(ksa_rows[index - 1]),
            focus=ksa_rows[index - 1]["factorName"],
        )
        for index in range(1, 4)
    ]
    return ksa_rows, questions, methods


def _quality_snapshot(
    strategy: dict[str, Any],
    *,
    failed_indexes: list[int],
) -> dict[str, Any]:
    questions = [
        dict(item)
        for item in (strategy.get("interview_questions") or [])
        if isinstance(item, dict)
    ]
    failed = set(failed_indexes)
    items = [
        {
            "index": index,
            "ready": index not in failed,
            "issues": ["ksa_grounded"] if index in failed else [],
            "realism_issue_codes": [],
            "precision_grounding_issue_codes": [],
        }
        for index in range(1, len(questions) + 1)
    ]
    passed = not failed
    return {
        **strategy,
        "question_quality_report": {
            "passed": passed,
            "summary": {
                "question_count": len(questions),
                "expected_question_count": len(questions),
                "count_matches_plan": True,
                "ready_count": len(questions) - len(failed),
                "needs_review_count": len(failed),
            },
            "items": items,
        },
        "question_quality_orchestration": {
            "status": "passed" if passed else "needs_review",
            "unresolved_count": len(failed),
            "items": [
                {
                    "index": index,
                    "final_issues": (
                        ["full_quality_ksa_grounded"] if index in failed else []
                    ),
                }
                for index in range(1, len(questions) + 1)
            ],
        },
    }


def _post_three_question_request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    builder: Any,
    quality: Any,
):
    ksa_rows, _questions, methods = _three_question_fixture()
    quality_retry._patch_pipeline(monkeypatch, builder, ksa_rows=ksa_rows)
    monkeypatch.setattr(main, "_run_runtime_question_quality_orchestration", quality)
    monkeypatch.setenv("GENERATION_MAX_MAIN_QUESTIONS", "1")
    payload = quality_retry._generation_payload_with_plan(
        main_count=3,
        interview_methods=[methods[0]],
    )
    with TestClient(main.app, client=quality_retry.REMOTE_CLIENT) as client:
        return quality_retry._post_primary_with_payload(client, PRIMARY_PATH, payload)


def test_default_plan_is_one_main_question_with_three_follow_ups_per_selected_item() -> None:
    plan = main._parse_question_plan_json("", ["사무행정"])

    assert plan["selected_items"] == [
        {
            "detail": "사무행정",
            "enabled": True,
            "main_count": 1,
            "follow_up_count": 3,
        }
    ]
    assert plan["total_main_count"] == 1
    assert len(plan["question_sequence"]) == 1
    assert all(row["follow_up_count"] == 3 for row in plan["question_sequence"])

    html = (Path(main.__file__).parent / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    assert re.search(r'id="defaultMainCount"[^>]*\bvalue="1"', html)
    assert re.search(r'id="defaultFollowCount"[^>]*\bvalue="3"', html)


def test_generation_capacity_accepts_five_and_rejects_six() -> None:
    accepted = main._parse_question_plan_json(
        '{"items":[{"detail":"경영기획","enabled":true,"main_count":5,"follow_up_count":3}]}',
        ["경영기획"],
    )
    main._enforce_question_plan_capacity(accepted)

    rejected = main._parse_question_plan_json(
        '{"items":[{"detail":"경영기획","enabled":true,"main_count":6,"follow_up_count":3}]}',
        ["경영기획"],
    )
    with pytest.raises(main.HTTPException) as exc_info:
        main._enforce_question_plan_capacity(rejected)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {
        "code": "question_plan_capacity_exceeded",
        "message": (
            "AI 안정성을 위해 주질문은 한 번에 최대 5개까지 생성할 수 있습니다. "
            "5개 이하로 줄인 뒤, 결과 화면의 다른 질문 생성 기능으로 이어서 생성해 주세요."
        ),
        "requested_main_questions": 6,
        "max_main_questions": 5,
        "retryable": False,
    }


def test_production_generation_capacity_accepts_one_and_rejects_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_MAX_MAIN_QUESTIONS", "1")
    accepted = main._parse_question_plan_json(
        '{"items":[{"detail":"전기기기유지보수","enabled":true,"main_count":1,"follow_up_count":3}]}',
        ["전기기기유지보수"],
    )
    main._enforce_question_plan_capacity(accepted)

    rejected = main._parse_question_plan_json(
        '{"items":[{"detail":"전기기기유지보수","enabled":true,"main_count":2,"follow_up_count":3}]}',
        ["전기기기유지보수"],
    )
    with pytest.raises(main.HTTPException) as exc_info:
        main._enforce_question_plan_capacity(rejected)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["requested_main_questions"] == 2
    assert exc_info.value.detail["max_main_questions"] == 1
    assert exc_info.value.detail["retryable"] is False


@pytest.mark.parametrize(
    ("path", "count_field"),
    [
        ("/api/questions/generate-personalized", "target_count"),
        ("/api/questions/generate-by-ncs-code", "target_count"),
        ("/api/questions/generate-batch", "batch_count"),
        ("/api/questions/generate-diverse", "target_count"),
    ],
)
def test_production_auxiliary_capacity_rejects_two_before_external_work(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    count_field: str,
) -> None:
    monkeypatch.setenv("GENERATION_MAX_MAIN_QUESTIONS", "1")

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("capacity rejection must precede NCS and model work")

    monkeypatch.setattr(main, "_require_ncs_mcp_url", unexpected)
    monkeypatch.setattr(main, "generate_personalized_interview_questions", unexpected)
    monkeypatch.setattr(main, "generate_interview_questions_by_ncs_code", unexpected)
    monkeypatch.setattr(main, "generate_diverse_interview_questions", unexpected)
    payload = {"ncs_code": "0202030201_25v3", count_field: 2}

    with TestClient(main.app, client=quality_retry.REMOTE_CLIENT) as client:
        response = client.post(path, json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "question_plan_capacity_exceeded"
    assert detail["requested_main_questions"] == 2
    assert detail["max_main_questions"] == 1
    assert detail["retryable"] is False


def test_single_ncs_code_generation_does_not_top_up_an_empty_provider_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    monkeypatch.setenv("NCS_AI_TOPUP_ATTEMPTS", "5")

    def generate_once(**_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(jd_strategy, "_generate_questions_with_openai_from_ncs", generate_once)
    monkeypatch.setattr(
        jd_strategy,
        "fetch_ncs_ksa_by_units",
        lambda **_kwargs: [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "factorName": "문서 요구사항 파악",
            }
        ],
    )

    result = jd_strategy.generate_interview_questions_by_ncs_code(
        ncs_code="0202030201_25v3",
        competency_name="문서작성",
        target_count=1,
    )

    assert calls == 1
    assert result["main_questions"] == []


@pytest.mark.parametrize("path", quality_retry.PRIMARY_PATHS)
def test_oversized_plan_is_rejected_before_any_external_work(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("oversized plans must not reach Kordoc, NCS, or the model")

    monkeypatch.setattr(main, "_parse_upload_document", unexpected)
    monkeypatch.setattr(main, "_require_ncs_mcp_url", unexpected)
    monkeypatch.setattr(main, "_fetch_ncs_ksa_or_502", unexpected)
    monkeypatch.setattr(main, "build_jd_strategy_with_openai", unexpected)
    payload = quality_retry._generation_payload_with_plan(
        main_count=6,
        interview_methods=["상황면접"],
    )

    with TestClient(main.app, client=quality_retry.REMOTE_CLIENT) as client:
        response = quality_retry._post_primary_with_payload(client, path, payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "question_plan_capacity_exceeded"
    assert detail["requested_main_questions"] == 6
    assert detail["max_main_questions"] == 5
    assert detail["retryable"] is False


def test_frontend_starts_with_the_production_safe_five_question_capacity() -> None:
    html = (Path(main.__file__).parent / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    assert re.search(r'id="defaultMainCount"[^>]*\bmax="5"', html)
    assert 'id="questionPlanCapacity"' in html
    assert "const DEFAULT_GENERATION_MAX_MAIN_QUESTIONS = 5" in html
    assert "function questionPlanCapacityState()" in html
    assert "function generationCapacityBlockReason()" in html
    assert "const capacityBlock = generationCapacityBlockReason()" in html
    assert "currentQuestionPlanDetails().map(detail =>" in html
    assert "question_plan_capacity_exceeded" not in html


def test_requested_three_questions_are_rejected_before_provider_success_can_bypass_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ksa_rows, questions, _methods = _three_question_fixture()
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        return quality_retry._strategy_with_questions(questions)

    response = _post_three_question_request(
        monkeypatch,
        builder=builder,
        quality=lambda strategy, **_kwargs: _quality_snapshot(
            strategy,
            failed_indexes=[],
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "question_plan_capacity_exceeded"
    assert response.json()["detail"]["requested_main_questions"] == 3
    assert response.json()["detail"]["max_main_questions"] == 1
    assert build_calls == []


def test_requested_three_questions_are_rejected_before_quality_retry_can_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ksa_rows, questions, _methods = _three_question_fixture()
    build_calls: list[dict[str, Any]] = []
    quality_calls = 0

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        if len(build_calls) == 1:
            return quality_retry._strategy_with_questions(questions)
        return quality_retry._strategy_with_questions([questions[1]])

    def quality(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        nonlocal quality_calls
        quality_calls += 1
        failed_by_call = {1: [2], 2: [1], 3: [2]}
        return _quality_snapshot(
            strategy,
            failed_indexes=failed_by_call.get(quality_calls, [2]),
        )

    response = _post_three_question_request(
        monkeypatch,
        builder=builder,
        quality=quality,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "question_plan_capacity_exceeded"
    assert build_calls == []
    assert quality_calls == 0


def test_requested_three_questions_are_rejected_before_server_fallback_can_expand_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ksa_rows, questions, _methods = _three_question_fixture()
    build_calls: list[dict[str, Any]] = []

    def builder(**kwargs: Any) -> dict[str, Any]:
        build_calls.append(copy.deepcopy(kwargs))
        return quality_retry._strategy_with_questions(questions)

    def all_hard_failed(strategy: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        question_count = len(strategy.get("interview_questions") or [])
        return _quality_snapshot(
            strategy,
            failed_indexes=list(range(1, question_count + 1)),
        )

    response = _post_three_question_request(
        monkeypatch,
        builder=builder,
        quality=all_hard_failed,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "question_plan_capacity_exceeded"
    assert build_calls == []
