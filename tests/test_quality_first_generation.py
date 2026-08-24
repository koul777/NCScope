from __future__ import annotations

import json

from app.services import jd_strategy
from app.services import question_generation


def _candidate(*, slot: int, variant: int, method: str) -> dict:
    scenarios = (
        "부서 집계표와 업무시스템 수치가 다르고 결산 마감이 오늘입니다.",
        "규정 적용 범위를 두 부서가 다르게 해석하고 승인권자는 자리를 비웠습니다.",
        "민원 급증 원인이 확인되지 않았고 검증 인력은 한 명뿐입니다.",
    )
    return {
        "type": method,
        "competency": f"능력단위-{slot + 1}",
        "ncsClCd": f"020101010{slot + 1}",
        "question": (
            f"{scenarios[variant % len(scenarios)]} "
            f"자료의 적용 범위와 오류 위험을 비교해 처리 결정을 내리고, "
            f"근거와 수정 조건이 보이는 판단 기록 {slot + 1}을 제시해 주세요."
        ),
        "follow_ups": [
            "방금 고른 근거가 불완전하다면 무엇을 추가 확인하시겠습니까?",
            "앞서 정한 처리 결정이 틀렸다면 어느 조건에서 수정하시겠습니까?",
            "결과를 어떤 기록으로 검증하시겠습니까?",
        ],
        "evaluation_points": [
            "근거 구분",
            "오류 위험 판단",
            "권한 내 처리",
            "검증과 수정 조건",
        ],
        "question_evidence_id": f"ksa-slot-{slot + 1}",
        "question_focus_surface": f"업무 초점 {slot + 1}",
        "question_focus": f"공식 KSA {slot + 1}",
        "ksa_refs": [f"공식 KSA {slot + 1}"],
    }


def test_primary_strategy_uses_max_reasoning_and_three_choice_candidate_pool(
    monkeypatch,
) -> None:
    captured: list[dict] = []
    monkeypatch.delenv("OPENAI_FORCE_FALLBACK", raising=False)
    monkeypatch.setenv("OPENAI_STRATEGY_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("OPENAI_STRATEGY_CANDIDATE_MULTIPLIER", "3")
    monkeypatch.setenv("OPENAI_STRATEGY_REASONING_EFFORT", "max")
    monkeypatch.setattr(
        type(jd_strategy.settings),
        "resolve_openai_key",
        lambda _self, _override: "request-key",
    )
    monkeypatch.setattr(jd_strategy, "_check_openai_connectivity", lambda **_: (True, ""))

    def fake_chat(**kwargs):
        captured.append(kwargs["payload"])
        choices = []
        for variant in range(3):
            content = {
                "interview_questions": [
                    _candidate(slot=slot, variant=variant, method=("상황면접", "직무지식면접")[slot])
                    for slot in range(2)
                ],
                "ncs_link": [],
            }
            choices.append(
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(content, ensure_ascii=False)},
                }
            )
        return {"choices": choices}

    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", fake_chat)

    result = jd_strategy.build_strategy_with_openai(
        jd_text="예산 검증과 규정 적용",
        notice_text="행정직 채용",
        strengths="",
        region="",
        ncs_matches=[],
        ncs_ksa=[],
        target_count_override=2,
        api_key_override="request-key",
        interview_methods=["상황면접", "직무지식면접"],
    )

    assert len(captured) == 1
    assert captured[0]["model"] == "gpt-5.6-sol"
    assert captured[0]["reasoning_effort"] == "max"
    assert captured[0]["n"] == 3
    assert "temperature" not in captured[0]
    assert captured[0]["max_completion_tokens"] >= 14_000
    assert len(result["interview_questions"]) == 2
    assert result["provider_reasoning_effort"] == "max"
    assert result["provider_candidate_variant_count"] == 3
    assert result["question_candidate_selection"]["candidate_pool_count"] == 6


def test_auxiliary_generation_uses_one_draft_before_independent_review(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("OPENAI_QUESTION_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("OPENAI_QUESTION_CANDIDATE_MULTIPLIER", "3")
    monkeypatch.setenv("OPENAI_QUESTION_VARIANT_ATTEMPTS", "3")
    monkeypatch.setenv("OPENAI_QUESTION_REASONING_EFFORT", "max")
    monkeypatch.setattr(
        type(question_generation.settings),
        "resolve_openai_key",
        lambda _self, _override="": "request-key",
    )

    def fake_chat(**kwargs):
        calls.append(kwargs["payload"])
        variant = len(calls) - 1
        content = {
            "interview_questions": [
                _candidate(slot=slot, variant=variant, method=("상황면접", "직무지식면접")[slot])
                for slot in range(2)
            ]
        }
        return {
            "choices": [
                {"message": {"content": json.dumps(content, ensure_ascii=False)}}
            ]
        }

    monkeypatch.setattr(
        question_generation,
        "post_chat_completions_with_retries",
        fake_chat,
    )

    selected = question_generation._generate_questions_with_openai_from_ncs(
        ncs_matches=[],
        ncs_ksa=[],
        target_count=2,
        api_key_override="request-key",
    )

    assert len(calls) == 1
    assert all(payload["reasoning_effort"] == "max" for payload in calls)
    assert all("temperature" not in payload for payload in calls)
    assert "interview_questions를 정확히 2개" in calls[0]["messages"][1]["content"]
    assert len(selected) == 1
    assert all(row["candidate_pool_count"] == 2 for row in selected)
    assert all(row["provider_candidate_variant_count"] == 1 for row in selected)
    assert all(row["candidate_quality_score"] > 0 for row in selected)
    assert all(row["candidate_selection_score"] > 0 for row in selected)
    assert all(row["candidate_diversity_axes"] for row in selected)


def test_planned_sequence_assigns_all_five_diversity_dimensions() -> None:
    question_plan = {
        "question_sequence": [
            {"detail": "Finance operations", "follow_up_count": 3}
            for _ in range(6)
        ]
    }
    ncs_matches = [
        {
            "ncsClCd": "0201010101",
            "compeUnitName": "Budget review",
            "compeUnitDef": "Review budget evidence and resolve exceptions.",
            "ncsSubdCdnm": "Finance",
        }
    ]
    ncs_ksa = [
        {
            "ncsClCd": "0201010101",
            "factorName": "Policy knowledge",
            "ksaTypeName": "Knowledge",
            "elementName": "Evidence review",
        },
        {
            "ncsClCd": "0201010101",
            "factorName": "Variance analysis",
            "ksaTypeName": "Skill",
            "elementName": "Evidence review",
        },
        {
            "ncsClCd": "0201010101",
            "factorName": "Accountability",
            "ksaTypeName": "Attitude",
            "elementName": "Evidence review",
        },
    ]

    sequence = jd_strategy._planned_question_sequence_for_prompt(
        question_plan,
        ["behavioral", "situational", "knowledge"],
        6,
        ncs_matches,
        ncs_ksa,
    )

    assert len(sequence) == 6
    assert {row["required_job_context"] for row in sequence} == {"Budget review"}
    assert all("required_scenario_frame" not in row for row in sequence)
    assert all("required_difficulty" not in row for row in sequence)
    assert all("required_task_statement" not in row for row in sequence)
    assert all("required_observable_behavior" not in row for row in sequence)
    assert {row["required_ksa_type"] for row in sequence} == {
        "Knowledge",
        "Skill",
        "Attitude",
    }
    assert len({row["type"] for row in sequence}) == 3
