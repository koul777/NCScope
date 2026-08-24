from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app import main
from app.services import ai_question_quality_review as review_service
from app.services import question_generation
from app.services.question_surface import stable_ksa_evidence_id


DIMENSIONS = review_service.AI_QUALITY_DIMENSIONS


def _ksa() -> dict[str, Any]:
    return {
        "ncsClCd": "0202020103_25v1",
        "compeUnitName": "입사자 적응 지원",
        "compeUnitDef": "신규 입사자가 조직과 직무에 적응하도록 필요한 정보를 제공하고 지원한다.",
        "elementName": "입사자 적응 지원하기",
        "factorName": "신규 입사자의 적응 단계와 어려움을 파악하여 지원하려는 태도",
        "ksaTypeName": "태도",
        "factorSource": "ncs-mcp",
    }


def _question(text: str, *, evidence_id: str) -> dict[str, Any]:
    return {
        "type": "경험면접",
        "competency": "입사자 적응 지원",
        "ncsClCd": "0202020103_25v1",
        "question_evidence_id": evidence_id,
        "question": text,
        "follow_ups": [
            "처음 대화에서 어떤 어려움을 확인했습니까?",
            "상대의 반응에 따라 지원 방식을 어떻게 조정했습니까?",
            "적응 여부는 무엇으로 확인했습니까?",
        ],
        "evaluation_points": [
            "어려움을 파악한 구체적 근거",
            "상대 반응에 맞춘 지원 행동",
            "본인의 역할과 협업 범위",
            "적응 결과를 확인한 방법",
        ],
        "question_source": "openai_api",
    }


def _review_response(*, passed: bool, count: int = 1) -> dict[str, Any]:
    score = 4 if passed else 2
    reviews = []
    for index in range(1, count + 1):
        reviews.append(
            {
                "index": index,
                "scores": {dimension: score for dimension in DIMENSIONS},
                "reason_codes": [] if passed else ["grammar_unnatural"],
                "regeneration_guidance_codes": [] if passed else ["fix_korean_grammar"],
            }
        )
    return {
        "choices": [
            {"message": {"content": json.dumps({"reviews": reviews}, ensure_ascii=False)}}
        ]
    }


def _passed_review() -> dict[str, Any]:
    return {
        "policy": review_service.AI_QUALITY_REVIEW_POLICY,
        "status": "passed",
        "reviewed_count": 1,
        "scores": [{"index": 1, **{dimension: 4 for dimension in DIMENSIONS}}],
        "reason_codes": [],
        "items": [
            {
                "index": 1,
                "passed": True,
                "scores": {dimension: 4 for dimension in DIMENSIONS},
                "reason_codes": [],
                "regeneration_guidance_codes": [],
            }
        ],
        "model": "gpt-5.6-sol",
        "provider": "openai_api",
    }


def _failed_review() -> dict[str, Any]:
    result = _passed_review()
    result["status"] = "failed"
    result["reason_codes"] = ["grammar_unnatural"]
    result["items"][0] = {
        "index": 1,
        "passed": False,
        "scores": {dimension: 2 for dimension in DIMENSIONS},
        "reason_codes": ["grammar_unnatural"],
        "regeneration_guidance_codes": ["fix_korean_grammar"],
    }
    return result


def test_review_parser_accepts_minor_editorial_three_as_advisory() -> None:
    response = _review_response(passed=True)
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["reviews"][0]["scores"]["korean_naturalness"] = 3
    payload["reviews"][0]["reason_codes"] = ["grammar_unnatural"]
    payload["reviews"][0]["regeneration_guidance_codes"] = [
        "fix_korean_grammar"
    ]
    response["choices"][0]["message"]["content"] = json.dumps(payload)

    parsed = review_service._parse_review(response, 1)

    assert parsed[0]["passed"] is True
    assert parsed[0]["blocking_reason_codes"] == []
    assert parsed[0]["advisory_reason_codes"] == ["grammar_unnatural"]


def test_review_parser_accepts_usable_critical_three_as_advisory() -> None:
    response = _review_response(passed=True)
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["reviews"][0]["scores"]["ksa_semantic_connection"] = 3
    payload["reviews"][0]["reason_codes"] = ["scenario_action_ksa_disconnect"]
    payload["reviews"][0]["regeneration_guidance_codes"] = [
        "rewrite_from_official_ksa"
    ]
    response["choices"][0]["message"]["content"] = json.dumps(payload)

    parsed = review_service._parse_review(response, 1)

    assert parsed[0]["passed"] is True
    assert parsed[0]["blocking_reason_codes"] == []
    assert parsed[0]["advisory_reason_codes"] == [
        "scenario_action_ksa_disconnect"
    ]


def test_review_parser_blocks_severe_critical_two() -> None:
    response = _review_response(passed=True)
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["reviews"][0]["scores"]["ksa_semantic_connection"] = 2
    payload["reviews"][0]["reason_codes"] = ["scenario_action_ksa_disconnect"]
    payload["reviews"][0]["regeneration_guidance_codes"] = [
        "rewrite_from_official_ksa"
    ]
    response["choices"][0]["message"]["content"] = json.dumps(payload)

    parsed = review_service._parse_review(response, 1)

    assert parsed[0]["passed"] is False
    assert parsed[0]["reason_codes"] == ["scenario_action_ksa_disconnect"]
    assert parsed[0]["blocking_reason_codes"] == [
        "scenario_action_ksa_disconnect"
    ]


def test_review_parser_allows_two_editorial_threes_at_average_floor() -> None:
    response = _review_response(passed=True)
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["reviews"][0]["scores"]["korean_naturalness"] = 3
    payload["reviews"][0]["scores"]["follow_up_coherence"] = 3
    payload["reviews"][0]["reason_codes"] = [
        "grammar_unnatural",
        "follow_up_disconnected",
    ]
    payload["reviews"][0]["regeneration_guidance_codes"] = [
        "fix_korean_grammar",
        "deepen_answer_linked_followups",
    ]
    response["choices"][0]["message"]["content"] = json.dumps(payload)

    assert review_service._parse_review(response, 1)[0]["passed"] is True


def test_review_parser_treats_duplicate_code_as_blocking_set_failure() -> None:
    response = _review_response(passed=True)
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["reviews"][0]["reason_codes"] = ["duplicate_question"]
    payload["reviews"][0]["regeneration_guidance_codes"] = [
        "diversify_scenario"
    ]
    response["choices"][0]["message"]["content"] = json.dumps(payload)

    parsed = review_service._parse_review(response, 1)

    assert parsed[0]["passed"] is False
    assert parsed[0]["blocking_reason_codes"] == ["duplicate_question"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["reviews"][0].update(
            {"reason_codes": "grammar_unnatural"}
        ),
        lambda payload: payload["reviews"][0].update(
            {"reason_codes": ["unknown_review_code"]}
        ),
        lambda payload: payload["reviews"][0].update({"index": "1"}),
        lambda payload: payload["reviews"][0].update({"debug": "provider text"}),
        lambda payload: payload.update({"debug": "provider text"}),
    ],
)
def test_review_parser_rejects_non_schema_fields_and_types(mutate) -> None:
    response = _review_response(passed=True)
    payload = json.loads(response["choices"][0]["message"]["content"])
    mutate(payload)
    response["choices"][0]["message"]["content"] = json.dumps(payload)

    with pytest.raises(review_service.AIQuestionQualityReviewError) as exc_info:
        review_service._parse_review(response, 1)

    assert str(exc_info.value) == "ai_quality_review_invalid_shape"


def test_independent_review_requires_all_eight_scores_and_returns_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ksa = _ksa()
    evidence_id = stable_ksa_evidence_id(ksa)
    captured: dict[str, Any] = {}

    def fake_post(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _review_response(passed=True)

    monkeypatch.setattr(review_service, "post_chat_completions_with_retries", fake_post)
    result = review_service.review_interview_questions_with_ai(
        questions=[
            _question(
                "새로 합류한 동료가 업무 절차를 익히는 데 어려움을 겪을 때, 이를 파악하고 적응을 도운 경험을 말씀해 주세요.",
                evidence_id=evidence_id,
            )
        ],
        ncs_matches=[ksa],
        ncs_ksa=[ksa],
        interview_methods=["경험면접"],
        job_context={"duties": "신규 입사자 온보딩과 교육 운영"},
        provider="openai_api",
        api_key_override="sk-test-request-key",
        generation_model="gpt-5.6-sol",
    )

    assert result["status"] == "passed"
    assert result["reviewed_count"] == 1
    assert set(result["scores"][0]) == {"index", *DIMENSIONS}
    assert result["reason_codes"] == []
    assert "question" not in result
    payload_text = json.dumps(captured["payload"], ensure_ascii=False)
    assert "uniqueItems" not in payload_text
    assert evidence_id in payload_text
    assert "신규 입사자 온보딩과 교육 운영" in payload_text
    assert "required_scenario_frame" not in payload_text


def test_review_prompt_keeps_star_completeness_advisory_and_method_gate_narrow() -> None:
    ksa = _ksa()
    evidence_id = stable_ksa_evidence_id(ksa)
    question = _question(
        "신규 입사자의 초기 적응을 지원했던 경험을 말씀해 주세요.",
        evidence_id=evidence_id,
    )
    # Deliberately do not prescribe four STAR slots in the draft. The review
    # contract must not turn their completeness into a release gate.
    question["follow_ups"] = ["그때 본인이 실제로 한 지원 행동은 무엇이었습니까?"]

    prompt = review_service._review_prompt(
        questions=[question],
        ncs_matches=[ksa],
        ncs_ksa=[ksa],
        interview_methods=["경험면접"],
        job_context={"duties": "신규 입사자의 초기 적응 지원"},
    )

    assert "작성 가이드의 개별 요소 누락은 감점하지 않음" in prompt
    assert "핵심 응답 방식이 명백히 충돌할 때만 2점 이하" in prompt
    assert "STAR의 S/T/A/R 라벨·순서·네 요소 완결성은 어느 점수 차원의 통과 조건도 아닙니다" in prompt
    assert "네 요소 중 하나 이상이 명시되지 않았다는 이유만으로 감점하거나 실패 코드를 부여하지" in prompt
    assert "실제 상황·역할·행동·결과 증거를 끌어낼 수 있는지만 보세요" not in prompt


def test_review_prompt_deduplicates_long_context_and_keeps_only_question_units() -> None:
    ksa = _ksa()
    evidence_id = stable_ksa_evidence_id(ksa)
    duty = ("DUTY_EXACT_MARKER_" + ("project administration evidence " * 5)).strip()
    evaluation = ("EVALUATION_EXACT_MARKER_" + ("observable decision result " * 5)).strip()
    short_common = "SHORT_COMMON"
    selected_unit = dict(ksa)
    selected_unit["compeUnitName"] = "SELECTED_UNIT_MARKER"
    unrelated_unit = {
        **ksa,
        "ncsClCd": "9999999999_99v9",
        "compeUnitName": "UNRELATED_UNIT_MARKER",
    }

    prompt = review_service._review_prompt(
        questions=[_question("Tell us about the work event.", evidence_id=evidence_id)],
        ncs_matches=[selected_unit, unrelated_unit],
        ncs_ksa=[ksa],
        interview_methods=["experience"],
        job_context={
            "notice": (
                f"[duties]\n{duty}\n{short_common}\n"
                f"UNIQUE_NOTICE_MARKER\n[evaluation]\n{evaluation}"
            ),
            "job_description": f"UNIQUE_JD_MARKER\n{duty}\n{short_common}",
            "duties": duty,
            "evaluation": evaluation,
        },
    )

    assert prompt.count(duty) == 1
    assert prompt.count(evaluation) == 1
    assert prompt.count(short_common) == 2
    assert "UNIQUE_NOTICE_MARKER" in prompt
    assert "UNIQUE_JD_MARKER" in prompt
    assert "SELECTED_UNIT_MARKER" in prompt
    assert "UNRELATED_UNIT_MARKER" not in prompt


@pytest.mark.parametrize(
    "broken_text",
    [
        "지원가 어떤 기준으로 신규 입사자를 도왔습니까?",
        "지원와 협업한 경험을 말씀해 주세요.",
        "KSA가 드러난 장면을 설명해 주세요.",
        "자료가 서로 달랐던 때 어떤 행동을 했습니까?",
    ],
)
def test_fixed_broken_question_surfaces_are_hard_rejected(broken_text: str) -> None:
    question = _question(broken_text, evidence_id=stable_ksa_evidence_id(_ksa()))
    strategy = {
        "interview_questions": [question],
        "ai_quality_review": _passed_review(),
        "question_quality_report": {"passed": True, "items": []},
        "question_quality_orchestration": {"status": "passed", "items": []},
    }
    assert "unsafe_question_surface" in main._institution_question_rejection_codes(
        strategy,
        require_quality_metadata=True,
    )


def test_valid_evidence_id_does_not_make_unrelated_budget_question_pass() -> None:
    question = _question(
        "예산 원장의 잔액을 결산 전에 맞춘 경험을 말씀해 주세요.",
        evidence_id=stable_ksa_evidence_id(_ksa()),
    )
    failed = _failed_review()
    failed["reason_codes"] = ["scenario_action_ksa_disconnect"]
    failed["items"][0]["reason_codes"] = ["scenario_action_ksa_disconnect"]
    failed["items"][0]["regeneration_guidance_codes"] = ["rewrite_from_official_ksa"]
    strategy = {
        "interview_questions": [question],
        "ai_quality_review": failed,
        "question_quality_report": {"passed": True, "items": []},
        "question_quality_orchestration": {"status": "passed", "items": []},
    }
    assert "ai_quality_review_failed" in main._institution_question_rejection_codes(
        strategy,
        require_quality_metadata=True,
    )


def test_server_metadata_attachment_does_not_rewrite_ai_authored_sentences() -> None:
    ksa = _ksa()
    evidence_id = stable_ksa_evidence_id(ksa)
    original = _question(
        "새로 합류한 구성원이 업무 절차를 익히는 데 어려움을 겪었던 사례를 말씀해 주세요.",
        evidence_id=evidence_id,
    )
    original["question_focus"] = ksa["factorName"]
    original["ksa_refs"] = [ksa["factorName"]]

    normalized = question_generation._normalize_question_item(original)
    assert normalized is not None
    attached = question_generation._attach_candidate_surface_evidence(
        normalized,
        ncs_ksa=[ksa],
        ncs_matches=[ksa],
    )

    assert attached["question"] == original["question"]
    assert attached["follow_ups"] == original["follow_ups"]
    assert attached["evaluation_points"] == original["evaluation_points"]
    assert "candidate_surface_repairs" not in attached


def test_failed_draft_is_regenerated_then_independently_rechecked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ksa = _ksa()
    evidence_id = stable_ksa_evidence_id(ksa)
    bad = _question("지원가 신규 입사자를 도운 경험을 말해 주세요.", evidence_id=evidence_id)
    good = _question(
        "새로 합류한 동료가 업무 절차를 익히는 데 어려움을 겪을 때, 이를 파악하고 적응을 도운 경험을 말씀해 주세요.",
        evidence_id=evidence_id,
    )
    generated = [bad, good]
    review_results = [_failed_review(), _passed_review()]
    calls: list[str] = []

    def fake_builder(**_kwargs: Any) -> dict[str, Any]:
        calls.append("generation")
        return {
            "interview_questions": [dict(generated.pop(0))],
            "provider_generation_request_count": 1,
            "provider_generation_model": "gpt-5.6-sol",
        }

    def fake_adjust(strategy: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {**strategy, "interview_questions": [dict(strategy["interview_questions"][0])]}

    def fake_audit(strategy: dict[str, Any], _ksa_rows: list[dict[str, Any]]) -> dict[str, Any]:
        result = dict(strategy)
        result["question_quality_report"] = {
            "passed": True,
            "summary": {"count_matches_plan": True},
            "items": [{"index": 1, "ready": True, "issues": []}],
        }
        result["question_quality_orchestration"] = {
            "status": "passed",
            "items": [{"index": 1, "final_issues": []}],
        }
        return result

    def fake_review(**_kwargs: Any) -> dict[str, Any]:
        calls.append("review")
        return review_results.pop(0)

    monkeypatch.setattr(main, "build_jd_strategy_with_openai", fake_builder)
    monkeypatch.setattr(main, "_adjust_generated_questions", fake_adjust)
    monkeypatch.setattr(main, "_audit_ai_authored_strategy_without_repair", fake_audit)
    monkeypatch.setattr(main, "review_interview_questions_with_ai", fake_review)

    result = asyncio.run(
        main._generate_quality_gated_institution_strategy(
            build_kwargs={
                "generation_provider": "openai_api",
                "generation_model": "gpt-5.6-sol",
                "api_key_override": "sk-test-request-key",
                "jd_text": "신규 입사자 온보딩과 교육 운영",
                "duty_text": "신규 입사자의 초기 적응 지원",
                "notice_text": "인사 운영 담당자 채용",
                "evaluation_text": "구성원 지원 행동",
            },
            question_plan={
                "total_main_count": 1,
                "follow_up_count": 3,
                "question_sequence": [
                    {
                        "index": 1,
                        "detail": "인사·조직",
                        "type": "경험면접",
                        "ncsClCd": ksa["ncsClCd"],
                        "evidence_id": evidence_id,
                        "follow_up_count": 3,
                    }
                ],
            },
            interview_methods=["경험면접"],
            ncs_matches=[ksa],
            ncs_ksa=[ksa],
            avoid_questions=[],
            generation_offset=0,
        )
    )

    assert calls == ["generation", "review", "generation", "review"]
    assert result["interview_questions"][0]["question"] == good["question"]
    assert result["interview_questions"][0]["question_source"] == "openai_api"
    assert result["ai_quality_review"]["status"] == "passed"
    assert result["ai_quality_review"]["attempt_count"] == 2
    assert result["model_quality_retry"]["outcome"] == "passed_after_retry"
    assert "server_ksa_fallback" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize(
    ("error_code", "status_code", "public_code"),
    [
        ("ai_quality_review_invalid_json", 502, "openai_api_quality_rejected"),
        ("ai_quality_review_network_failed", 503, "openai_api_unreachable"),
        ("ai_quality_review_timeout", 504, "openai_api_timeout"),
    ],
)
def test_review_failures_map_to_safe_retryable_http_errors(
    error_code: str,
    status_code: int,
    public_code: str,
) -> None:
    error = main._institution_api_provider_http_error(
        review_service.AIQuestionQualityReviewError(error_code),
        provider="openai_api",
    )
    assert error.status_code == status_code
    assert error.detail["code"] == public_code
    assert error.detail["retryable"] is True
    assert "question" not in json.dumps(error.detail, ensure_ascii=False).casefold()


@pytest.mark.parametrize(
    ("provider_error", "status_code", "public_code", "retryable"),
    [
        ("openai_http_401", 401, "openai_api_authentication_failed", False),
        ("openai_http_429", 429, "openai_api_usage_limit_reached", True),
        ("openai_http_400", 502, "openai_api_request_rejected", False),
        ("openai_http_503", 502, "openai_api_upstream_unavailable", True),
    ],
)
def test_review_provider_failures_keep_safe_status_classification(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: str,
    status_code: int,
    public_code: str,
    retryable: bool,
) -> None:
    ksa = _ksa()
    evidence_id = stable_ksa_evidence_id(ksa)

    def fail_post(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(provider_error + ": provider body must stay private")

    monkeypatch.setattr(review_service, "post_chat_completions_with_retries", fail_post)
    with pytest.raises(review_service.AIQuestionQualityReviewError) as captured:
        review_service.review_interview_questions_with_ai(
            questions=[_question("신규 입사자의 적응을 도운 경험을 말씀해 주세요.", evidence_id=evidence_id)],
            ncs_matches=[ksa],
            ncs_ksa=[ksa],
            interview_methods=["경험면접"],
            job_context={"duties": "신규 입사자 온보딩"},
            provider="openai_api",
            api_key_override="sk-test-request-key",
        )

    assert str(captured.value) == provider_error
    public_error = main._institution_api_provider_http_error(
        captured.value,
        provider="openai_api",
    )
    assert public_error.status_code == status_code
    assert public_error.detail["code"] == public_code
    assert public_error.detail["retryable"] is retryable
    serialized = json.dumps(public_error.detail, ensure_ascii=False)
    assert "provider body" not in serialized
    assert "question" not in serialized.casefold()
