from __future__ import annotations

import json

from app.services.question_candidate_selection import select_question_candidates


def _candidate(
    question: str,
    *,
    method: str = "상황면접",
    focus: str = "업무 우선순위 조정",
    evidence_id: str = "ksa_111111111111111111111111",
    scenario: str = "budget",
    difficulty: str = "중",
    slot: str | None = None,
) -> dict:
    item = {
        "type": method,
        "competency": "문제해결능력",
        "ncsClCd": "0201010101_24v1",
        "compeUnitName": "업무계획 수립",
        "question": question,
        "question_focus": focus,
        "question_evidence_id": evidence_id,
        "ksa_refs": [focus],
        "scenario_signature": scenario,
        "difficulty": difficulty,
        "task_conditions": ["제한된 시간", "이해관계자 의견 충돌"],
        "follow_ups": [
            "먼저 확인할 사실과 판단 기준은 무엇입니까?",
            "선택한 대안의 위험을 어떻게 줄이겠습니까?",
            "조치 결과를 어떤 지표로 보고하겠습니까?",
        ],
        "evaluation_points": [
            "핵심 사실 확인",
            "판단 기준의 타당성",
            "실행 순서의 구체성",
            "결과 지표와 보고 방식",
        ],
    }
    if slot is not None:
        item["_candidate_slot"] = slot
    return item


def test_selection_prefers_complete_high_quality_candidate() -> None:
    sparse = {
        "question": "문제를 어떻게 해결하겠습니까?",
        "type": "상황면접",
        "follow_ups": ["왜입니까?"],
        "evaluation_points": ["문제해결"],
    }
    complete = _candidate(
        "예산이 20% 삭감되고 마감이 일주일 남은 사업 현장에서, 어떤 근거로 "
        "업무 우선순위를 결정하고 실행계획 보고서를 작성하시겠습니까?"
    )

    selected, metadata = select_question_candidates([sparse, complete], 1)

    assert selected == [complete]
    assert metadata["selected"][0]["quality_score"] > 90
    assert metadata["selected"][0]["quality_components"]["follow_ups"] == 15
    assert metadata["selected"][0]["quality_components"]["evaluation_points"] == 15
    json.dumps(metadata, ensure_ascii=False)


def test_selection_uses_method_ksa_scenario_and_constraint_diversity() -> None:
    same_axis_a = _candidate(
        "예산 부족 상황에서 사업 범위를 조정할 기준과 실행계획을 제시해 주세요.",
        method="상황면접",
        focus="예산 배분",
        evidence_id="ksa_budget_a",
        scenario="budget",
        difficulty="중",
    )
    same_axis_b = _candidate(
        "예산 집행 자료의 오류를 발견했을 때 검증 순서와 보고 방식을 설명해 주세요.",
        method="상황면접",
        focus="예산 배분",
        evidence_id="ksa_budget_a",
        scenario="budget",
        difficulty="중",
    )
    diverse = _candidate(
        "고객정보 시스템 장애가 발생한 현장에서 복구 대안을 비교하고 30분 내 "
        "대응 보고서를 제시해 주세요.",
        method="발표면접",
        focus="정보시스템 장애 대응",
        evidence_id="ksa_system_b",
        scenario="system",
        difficulty="상",
    )

    selected, metadata = select_question_candidates([same_axis_a, same_axis_b, diverse], 2)

    assert {item["type"] for item in selected} == {"상황면접", "발표면접"}
    assert len({row["axes"]["scenario_signature"] for row in metadata["selected"]}) == 2
    assert metadata["selected"][1]["novelty_score"] > 70


def test_semantic_near_duplicates_keep_the_richer_representative() -> None:
    sparse_paraphrase = {
        "question": "사업 예산이 20퍼센트 줄어든 상황이라면 어떤 우선순위로 대응하시겠습니까?",
        "type": "상황면접",
    }
    rich = _candidate(
        "사업 예산이 20% 삭감된 상황에서 대응 우선순위를 어떻게 정하시겠습니까?"
    )
    unrelated = _candidate(
        "개인정보 시스템 장애가 난 현장에서 위험을 판단하고 복구 보고서를 어떻게 "
        "작성하시겠습니까?",
        focus="개인정보 보호",
        evidence_id="ksa_privacy_b",
        scenario="system",
    )

    selected, metadata = select_question_candidates([sparse_paraphrase, unrelated, rich], 3)

    assert rich in selected
    assert sparse_paraphrase not in selected
    assert unrelated in selected
    assert metadata["eligible_count"] == 2
    assert metadata["duplicate_count"] == 1
    assert metadata["near_duplicate_count"] == 1


def test_avoid_questions_excludes_exact_and_near_matches() -> None:
    avoided = _candidate(
        "납기가 이틀 남았는데 핵심 인력이 부족한 상황에서 업무 우선순위를 어떻게 "
        "정하시겠습니까?",
        scenario="schedule",
    )
    safe = _candidate(
        "고객 민원 자료를 분석해 재발 방지 대안과 성과지표를 제시해 주세요.",
        focus="고객 민원 분석",
        evidence_id="ksa_customer_b",
        scenario="customer",
    )

    selected, metadata = select_question_candidates(
        [avoided, safe],
        2,
        avoid_questions=[
            "핵심 인력이 부족하고 납기가 이틀 남은 경우, 어떤 우선순위로 업무를 대응하시겠습니까?"
        ],
    )

    assert selected == [safe]
    assert metadata["avoid_duplicate_count"] == 1
    assert metadata["duplicate_count"] == 1


def test_selection_is_deterministic() -> None:
    candidates = [
        _candidate(
            "감사 지적사항의 위험도를 판단해 개선계획 보고서를 제시해 주세요.",
            focus="감사 대응",
            evidence_id="ksa_audit_a",
            scenario="compliance",
        ),
        _candidate(
            "생산 설비 오류 자료를 검증하고 품질 복구 계획을 설명해 주세요.",
            method="직무지식면접",
            focus="설비 품질 관리",
            evidence_id="ksa_quality_b",
            scenario="production",
        ),
        _candidate(
            "협력사 계약 일정이 지연될 때 대안을 비교하고 조정안을 제시해 주세요.",
            method="토론면접",
            focus="협력사 계약 관리",
            evidence_id="ksa_contract_c",
            scenario="procurement",
        ),
    ]

    first_selected, first_metadata = select_question_candidates(candidates, 2)
    second_selected, second_metadata = select_question_candidates(candidates, 2)

    assert first_selected == second_selected
    assert first_metadata == second_metadata


def test_candidate_shortage_returns_every_eligible_candidate() -> None:
    first = _candidate("고객 불만 자료를 분석하고 개선안 보고서를 제시해 주세요.")
    second = _candidate(
        "생산 일정 지연의 원인을 판단하고 복구 계획서를 작성해 주세요.",
        focus="생산 일정 관리",
        evidence_id="ksa_production_b",
        scenario="production",
    )

    selected, metadata = select_question_candidates([first, second, {"question": "  "}], 7)

    assert len(selected) == 2
    assert metadata["candidate_count"] == 3
    assert metadata["eligible_count"] == 2
    assert metadata["selected_count"] == 2
    assert metadata["empty_count"] == 1


def test_candidate_slots_are_covered_once_when_target_allows() -> None:
    slot_a_best = _candidate(
        "예산 삭감 상황에서 판단 기준과 실행계획 보고서를 제시해 주세요.",
        slot="slot-a",
    )
    slot_a_other = _candidate(
        "예산 자료 검토 후 사업 조정 기준과 보고 방식을 설명해 주세요.",
        focus="예산 검토",
        evidence_id="ksa_budget_other",
        scenario="budget-review",
        slot="slot-a",
    )
    slot_b = _candidate(
        "고객 시스템 장애 시 위험을 판단하고 복구 결과 보고서를 제시해 주세요.",
        method="발표면접",
        focus="시스템 장애 복구",
        evidence_id="ksa_system_b",
        scenario="system",
        slot="slot-b",
    )

    selected, metadata = select_question_candidates([slot_a_best, slot_a_other, slot_b], 3)

    assert {item["_candidate_slot"] for item in selected} == {"slot-a", "slot-b"}
    assert metadata["slot_coverage"]["covered_count"] == 2
    assert metadata["slot_coverage"]["complete"] is True
    assert metadata["selected_count"] == 2
