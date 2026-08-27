from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services.ai_question_quality_review import AI_QUALITY_DIMENSIONS


REQUEST_KEY = "sk-request-scoped-aux-review"
NCS_CODE = "0202020101_25v1"


def _evidence() -> dict[str, Any]:
    row = {
        "ncsClCd": NCS_CODE,
        "compeUnitName": "인사 운영",
        "compeUnitDef": "구성원의 배치와 조직 적응을 지원하는 업무",
        "elementName": "신규 입사자 적응 지원",
        "factorName": "신규 입사자의 조직 적응 지원 방법",
        "ksaTypeName": "지식",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
        "isOfficialKsa": True,
        "unitCatalogVerified": True,
        "unitResponsePathVerified": True,
    }
    return {**row, "evidence_id": main.stable_ksa_evidence_id(row)}


def _question(text: str) -> dict[str, Any]:
    evidence = _evidence()
    frame = main.build_question_task_frame(
        evidence_row=evidence,
        factor_name=evidence["factorName"],
        ksa_type=evidence["ksaTypeName"],
        element_name=evidence["elementName"],
        competency_name=evidence["compeUnitName"],
        competency_definition=evidence["compeUnitDef"],
    )
    points = [
        "초기 적응 상태를 파악한 근거",
        "지원 우선순위를 정한 판단",
        "본인이 실행한 지원 행동",
        "적응 변화를 확인한 방법",
    ]
    return {
        "question": text,
        "type": "경험면접",
        "question_type": "경험면접",
        "competency": evidence["compeUnitName"],
        "ncsClCd": NCS_CODE,
        "question_focus": evidence["factorName"],
        "question_focus_source": "official_ksa",
        "question_focus_surface": frame["task_object"],
        "question_evidence_id": evidence["evidence_id"],
        "question_evidence_required": True,
        "question_task_frame": frame,
        "ksa_refs": [evidence["factorName"]],
        "question_source": "openai_api",
        "follow_ups": [
            "방금 말씀한 사례에서 적응에 어려움이 있다고 판단한 근거는 무엇이었습니까?",
            "여러 지원 방법 가운데 그 행동을 먼저 선택한 이유는 무엇입니까?",
            "지원 뒤 적응 변화를 무엇으로 확인했고, 변화가 없었다면 무엇을 조정했습니까?",
        ],
        "evaluation_points": points,
        "eval_points": list(points),
    }


def _result(question: dict[str, Any], *, key: str = "questions") -> dict[str, Any]:
    return {
        "generation_mode": "ai_only",
        "ncs_ksa_available": True,
        "official_ksa_evidence": [_evidence()],
        key: [deepcopy(question)],
        "follow_up_questions": [],
    }


def _review(*, passed: bool) -> dict[str, Any]:
    scores = {dimension: 5 if passed else 2 for dimension in AI_QUALITY_DIMENSIONS}
    return {
        "policy": "independent-ai-question-review-v1",
        "status": "passed" if passed else "failed",
        "reviewed_count": 1,
        "scores": [{"index": 1, **scores}],
        "reason_codes": [] if passed else ["grammar_unnatural"],
        "items": [
            {
                "index": 1,
                "passed": passed,
                "scores": scores,
                "reason_codes": [] if passed else ["grammar_unnatural"],
                "regeneration_guidance_codes": [] if passed else ["fix_korean_grammar"],
            }
        ],
        "model": "gpt-5.6-sol",
        "provider": "openai_api",
    }


@pytest.mark.parametrize(
    ("field", "count", "expected_code"),
    [
        ("follow_ups", 0, "follow_up_count_invalid"),
        ("follow_ups", 6, "follow_up_count_invalid"),
        ("evaluation_points", 0, "evaluation_point_count_invalid"),
        ("evaluation_points", 6, "evaluation_point_count_invalid"),
    ],
)
def test_auxiliary_structure_keeps_only_one_to_five_boundary(
    field: str,
    count: int,
    expected_code: str,
) -> None:
    question = _question("신규 입사자의 초기 적응을 지원한 판단과 결과를 말씀해 주세요.")
    question[field] = [f"AI 작성 항목 {index}" for index in range(count)]

    codes = main._auxiliary_question_structure_codes(
        _result(question),
        expected_count=1,
    )

    assert expected_code in codes


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


@pytest.mark.parametrize(
    ("path", "service_name", "question_key", "count_field"),
    [
        ("/api/questions/generate-personalized", "generate_personalized_interview_questions", "questions", "target_count"),
        ("/api/questions/generate-by-ncs-code", "generate_interview_questions_by_ncs_code", "main_questions", "target_count"),
        ("/api/questions/generate-batch", "generate_diverse_interview_questions", "questions", "batch_count"),
        ("/api/questions/generate-diverse", "generate_diverse_interview_questions", "questions", "target_count"),
    ],
)
def test_each_auxiliary_endpoint_regenerates_only_after_ai_review_failure(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    service_name: str,
    question_key: str,
    count_field: str,
) -> None:
    monkeypatch.setenv("OPENAI_STRATEGY_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("OPENAI_QUALITY_REGENERATION_MODEL", "gpt-5.6-sol")
    bad = _question("지원가 신규 입사자의 적응을 도운 경험을 말씀해 주세요.")
    good = _question(
        "새로 합류한 구성원이 업무 절차를 익히는 데 어려움을 겪었던 사례를 말씀해 주세요. "
        "당시 어떤 신호를 확인했고 본인은 어떤 지원을 했습니까?"
    )
    generation_calls: list[dict[str, Any]] = []
    review_calls = 0

    def fake_generate(**kwargs: Any) -> dict[str, Any]:
        generation_calls.append(kwargs)
        selected = bad if len(generation_calls) == 1 else good
        return _result(selected, key=question_key)

    def fake_review(**_kwargs: Any) -> dict[str, Any]:
        nonlocal review_calls
        review_calls += 1
        return _review(passed=review_calls == 2)

    monkeypatch.setattr(main, service_name, fake_generate)
    monkeypatch.setattr(main, "review_interview_questions_with_ai", fake_review)
    monkeypatch.setattr(main, "is_similar_question_text", lambda *_args: False)

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={
                "openai_api_key": REQUEST_KEY,
                "ncs_code": NCS_CODE,
                count_field: 1,
            },
        )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    rows = data[question_key]
    assert rows[0]["question"] == good["question"]
    assert data["ai_quality_review"]["status"] == "passed"
    assert data["ai_quality_review"]["attempt_count"] == 2
    assert len(generation_calls) == 2
    assert generation_calls[0]["generation_model"] == "gpt-5.6-terra"
    assert generation_calls[1]["generation_model"] == "gpt-5.6-sol"
    assert review_calls == 2
    assert "server_ksa_fallback" not in response.text
    assert "template_fallback" not in response.text
    assert REQUEST_KEY not in response.text


def test_auxiliary_review_rejection_returns_no_question_or_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _question("지원와 신규 입사자 적응을 지원한 경험을 말씀해 주세요.")
    calls = 0

    def fake_generate(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _result(bad)

    monkeypatch.setattr(main, "generate_diverse_interview_questions", fake_generate)
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **_kwargs: _review(passed=False),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-diverse",
            json={
                "openai_api_key": REQUEST_KEY,
                "ncs_code": NCS_CODE,
                "target_count": 1,
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "openai_api_quality_rejected"
    assert calls == 2
    assert REQUEST_KEY not in response.text
    assert bad["question"] not in response.text


@pytest.mark.parametrize(
    ("path", "service_name", "question_key", "count_field"),
    [
        ("/api/questions/generate-personalized", "generate_personalized_interview_questions", "questions", "target_count"),
        ("/api/questions/generate-by-ncs-code", "generate_interview_questions_by_ncs_code", "main_questions", "target_count"),
        ("/api/questions/generate-batch", "generate_diverse_interview_questions", "questions", "batch_count"),
        ("/api/questions/generate-diverse", "generate_diverse_interview_questions", "questions", "target_count"),
    ],
)
@pytest.mark.parametrize("follow_up_count", [1, 4, 5])
def test_auxiliary_gate_accepts_one_to_five_ai_authored_followups(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    service_name: str,
    question_key: str,
    count_field: str,
    follow_up_count: int,
) -> None:
    short = _question("신규 입사자의 초기 적응을 지원한 실제 판단과 결과를 말씀해 주세요.")
    model_follow_ups = [
        f"방금 확인한 적응 신호 {index}을 선택한 근거는 무엇입니까?"
        for index in range(1, follow_up_count + 1)
    ]
    short["follow_ups"] = model_follow_ups
    calls = 0

    def fake_generate(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _result(short, key=question_key)

    monkeypatch.setattr(main, service_name, fake_generate)
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **_kwargs: _review(passed=True),
    )

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={
                "openai_api_key": REQUEST_KEY,
                "ncs_code": NCS_CODE,
                count_field: 1,
            },
        )

    assert response.status_code == 200, response.text
    assert calls == 1
    assert short["question"] in response.text
    assert all(model_follow_up in response.text for model_follow_up in model_follow_ups)


@pytest.mark.parametrize(
    ("path", "service_name", "question_key", "count_field"),
    [
        ("/api/questions/generate-personalized", "generate_personalized_interview_questions", "questions", "target_count"),
        ("/api/questions/generate-by-ncs-code", "generate_interview_questions_by_ncs_code", "main_questions", "target_count"),
        ("/api/questions/generate-batch", "generate_diverse_interview_questions", "questions", "batch_count"),
        ("/api/questions/generate-diverse", "generate_diverse_interview_questions", "questions", "target_count"),
    ],
)
@pytest.mark.parametrize("evaluation_point_count", [1, 3, 5])
def test_auxiliary_gate_accepts_one_to_five_ai_evaluation_points(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    service_name: str,
    question_key: str,
    count_field: str,
    evaluation_point_count: int,
) -> None:
    malformed = _question("신규 입사자의 초기 적응을 지원한 실제 판단과 결과를 말씀해 주세요.")
    model_points = [
        f"모델이 작성한 관찰 기준 {index}"
        for index in range(1, evaluation_point_count + 1)
    ]
    malformed["evaluation_points"] = model_points
    calls = 0

    def fake_generate(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _result(malformed, key=question_key)

    monkeypatch.setattr(main, service_name, fake_generate)
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **_kwargs: _review(passed=True),
    )

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={
                "openai_api_key": REQUEST_KEY,
                "ncs_code": NCS_CODE,
                count_field: 1,
            },
        )

    assert response.status_code == 200, response.text
    assert calls == 1
    assert malformed["question"] in response.text
    assert all(point in response.text for point in model_points)


def test_auxiliary_gate_rejects_unrequested_extra_question_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _question("신규 입사자의 초기 적응을 지원한 실제 판단과 결과를 말씀해 주세요.")
    second = _question("새 구성원의 업무 절차 이해를 도운 판단과 결과를 말씀해 주세요.")
    calls = 0

    def fake_generate(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        result = _result(first, key="main_questions")
        result["main_questions"].append(deepcopy(second))
        return result

    monkeypatch.setattr(main, "generate_interview_questions_by_ncs_code", fake_generate)
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **_kwargs: _review(passed=True),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": REQUEST_KEY,
                "ncs_code": NCS_CODE,
                "target_count": 1,
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "openai_api_quality_rejected"
    assert calls == 2
    assert first["question"] not in response.text
    assert second["question"] not in response.text


def test_auxiliary_gate_never_releases_degraded_openai_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    degraded = _question("신규 입사자의 초기 적응을 지원한 판단과 결과를 말씀해 주세요.")
    degraded["degraded"] = True
    calls = 0

    def fake_generate(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _result(degraded)

    monkeypatch.setattr(main, "generate_diverse_interview_questions", fake_generate)
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **_kwargs: _review(passed=True),
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-diverse",
            json={
                "openai_api_key": REQUEST_KEY,
                "ncs_code": NCS_CODE,
                "target_count": 1,
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "openai_api_quality_rejected"
    assert calls == 2
    assert degraded["question"] not in response.text


@pytest.mark.parametrize(
    ("path", "count_field"),
    [
        ("/api/questions/generate-batch", "batch_count"),
        ("/api/questions/generate-diverse", "target_count"),
    ],
)
def test_post_review_dedup_never_returns_fewer_questions_than_requested(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    count_field: str,
) -> None:
    first = _question("문서 오류를 확인하고 우선순위를 정한 경험을 말씀해 주세요.")
    second = _question("기록 오류를 검토하고 처리 순서를 정한 경험을 말씀해 주세요.")

    def fake_generate(**_kwargs: Any) -> dict[str, Any]:
        result = _result(first)
        result["questions"].append(deepcopy(second))
        return result

    review = _review(passed=True)
    review["reviewed_count"] = 2
    review["scores"] = [
        {"index": index, **{dimension: 5 for dimension in AI_QUALITY_DIMENSIONS}}
        for index in (1, 2)
    ]
    review["items"] = [
        {
            "index": index,
            "passed": True,
            "scores": {dimension: 5 for dimension in AI_QUALITY_DIMENSIONS},
            "reason_codes": [],
            "regeneration_guidance_codes": [],
        }
        for index in (1, 2)
    ]
    monkeypatch.setattr(main, "generate_diverse_interview_questions", fake_generate)
    monkeypatch.setattr(
        main,
        "review_interview_questions_with_ai",
        lambda **_kwargs: deepcopy(review),
    )
    monkeypatch.setattr(main, "is_similar_question_text", lambda *_args: True)

    with TestClient(main.app) as client:
        response = client.post(
            path,
            json={
                "openai_api_key": REQUEST_KEY,
                "ncs_code": NCS_CODE,
                count_field: 2,
            },
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "openai_api_quality_rejected"
    assert first["question"] not in response.text
    assert second["question"] not in response.text
