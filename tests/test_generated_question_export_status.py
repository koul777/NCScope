from __future__ import annotations

import json

from app.main import (
    _build_generated_question_text_payload,
    _build_generated_questions_payload,
    _extract_all_generated_question_items,
    _extract_generated_question_items,
    _normalize_generated_questions,
)


def test_degraded_fallback_question_is_not_exported_as_ready() -> None:
    rows = _normalize_generated_questions(
        [
            {
                "question": "해당 직무에서 자료를 확인한 경험을 말씀해 주세요.",
                "type": "경험면접",
                "question_source": "server_ksa_fallback",
                "human_review_required": True,
                "degraded": True,
            }
        ],
        expected_count=1,
    )

    assert rows[0]["question"]
    assert rows[0]["review_required"] is True
    assert rows[0]["ready"] is False


def test_model_question_without_review_flags_remains_ready() -> None:
    rows = _normalize_generated_questions(
        [
            {
                "question": "실제 경험에서 맡은 역할과 결과를 설명해 주세요.",
                "type": "경험면접",
                "question_source": "openrouter_api",
            }
        ],
        expected_count=1,
    )

    assert rows[0]["ready"] is True
    assert "review_required" not in rows[0]


def test_main_strategy_exports_keep_degraded_fallback_in_review_state() -> None:
    strategy = {
        "question_plan": {"total_main_count": 1},
        "interview_questions": [
            {
                "question": "서버가 구성한 검토용 질문입니다.",
                "type": "경험면접",
                "question_source": "server_ksa_fallback",
                "degraded": True,
                "human_review_required": True,
            }
        ],
    }

    preview = _build_generated_questions_payload(
        strategy,
        strategy["question_plan"],
        include_all=False,
    )
    all_rows = _extract_all_generated_question_items(strategy)
    text_rows, _ = _build_generated_question_text_payload(strategy)

    for rows in (preview, all_rows, text_rows):
        assert rows[0]["ready"] is False
        assert rows[0]["review_required"] is True


def test_main_strategy_exports_keep_template_fallback_in_review_state() -> None:
    strategy = {
        "interview_questions": [
            {
                "question": "템플릿 기반 검토용 질문입니다.",
                "question_source": "template_fallback",
            }
        ]
    }

    assert _extract_generated_question_items(strategy)[0]["ready"] is False
    assert _extract_generated_question_items(strategy)[0]["review_required"] is True


def _structured_interviewer_question() -> dict:
    return {
        "question": "자료 충돌 상황에서 일정과 보고 우선순위를 어떻게 정하시겠습니까?",
        "type": "상황면접",
        "ncsClCd": "0202030103_17v3",
        "competency": "경영진 일정관리",
        "question_source": "claude_code_quality_repaired_fields",
        "follow_ups": ["가장 먼저 확인할 자료는 무엇입니까?"],
        "structured_follow_ups": [
            {
                "trigger": "always",
                "answer_slot": "facts_and_constraints",
                "evidence_target": "사실 확인",
                "prompt": "가장 먼저 확인할 자료는 무엇입니까?",
                "ask_limit": 1,
                "internal": "must-not-leak",
            }
        ],
        "evaluation_points": ["사실 확인", "보고 순서"],
        "task_conditions": {
            "candidate_instruction": "제시된 사실만 사용해 답변하십시오.",
            "time_plan": [{"phase": "개별 답변", "minutes": 5, "secret": "drop"}],
            "provided_materials": ["일정표", "보고 규정"],
            "required_outputs": ["우선순위", "후속조치"],
            "case_facts": ["회의 시간이 30분 앞당겨졌습니다."],
            "case_materials": [
                {
                    "source": "일정표",
                    "field": "변경 시간",
                    "value": "14:00에서 13:30으로 변경",
                    "api_key": "must-not-leak",
                }
            ],
            "standardization": "모든 지원자에게 동일한 조건을 적용합니다.",
            "internal_prompt": "must-not-leak",
        },
        "assessment_guide": {
            "scale": "5단계 행동기반 평정",
            "focus": "일정 충돌 조정",
            "focus_type": "기술",
            "dimensions": ["사실 확인", "우선순위 판단"],
            "dimension_rubrics": [
                {
                    "dimension": "사실 확인",
                    "observable_evidence": "사실 확인",
                    "weight": 25,
                    "critical_floor": 1,
                    "no_answer_rule": "추가 질문 뒤에도 근거가 없으면 1점으로 평정합니다.",
                    "anchors": {
                        "5": "복수 자료를 교차 확인하고 충돌 조건까지 구체화합니다.",
                        "3": "기본 사실은 확인하지만 출처와 제약은 부분적으로 불명확합니다.",
                        "1": "직감이나 추정만 제시하여 확인 가능한 근거가 없습니다.",
                    },
                    "secret": "must-not-leak",
                }
            ],
            "dimension_weight_total": 100,
            "critical_floor": {"score": 1, "rule": "한 차원이 1점이면 자동 합격하지 않습니다."},
            "no_answer_rule": {"score": 1, "rule": "추가 질문 뒤에도 답변이 없으면 1점입니다."},
            "rating_levels": [
                {"score": 5, "label": "탁월", "anchor": "사실과 위험을 연결해 행동 순서를 제시한다.", "token": "drop"}
            ],
            "interviewer_instruction": "관찰 가능한 행동 근거로 평정하십시오.",
            "reviewer_secret": "must-not-leak",
        },
        "ncs_traceability": {
            "ncs_code": "0202030103_17v3",
            "ability_unit_name": "경영진 일정관리",
            "element_id": "E1",
            "element_name": "일정 조정",
            "performance_criteria": ["일정 우선순위를 판단할 수 있다."],
            "ksa_type": "기술",
            "ksa_factor": "일정 조정 능력",
            "ksa_factor_no": "S1",
            "evidence_id": "ksa_1234567890abcdef12345678",
            "source": "NCS_MCP",
            "raw_response": "must-not-leak",
        },
        "ksa_refs": ["일정 조정 능력"],
        "ksa_evidence": [
            {
                "evidence_id": "ksa_1234567890abcdef12345678",
                "ncsClCd": "0202030103_17v3",
                "compeUnitName": "경영진 일정관리",
                "factorName": "일정 조정 능력",
                "ksaTypeName": "기술",
                "elementName": "일정 조정",
                "factorSource": "NCS_MCP",
                "performanceCriteria": ["일정 우선순위를 판단할 수 있다."],
                "provider_token": "must-not-leak",
            }
        ],
        "evidence_ids": ["ksa_1234567890abcdef12345678"],
        "question_hash": "legacy-hash",
        "question_artifact_hash": "artifact-hash",
        "question_text_hash": "text-hash",
        "match_bundle_fingerprint": "bundle-fingerprint",
        "question_evidence_id": "ksa_1234567890abcdef12345678",
        "question_task_frame": {"provider_prompt": "must-not-leak"},
        "openai_api_key": "must-not-leak",
    }


def test_interviewer_kit_survives_every_public_question_export() -> None:
    question = _structured_interviewer_question()
    strategy = {
        "question_plan": {"total_main_count": 1},
        "interview_questions": [question],
    }
    exports = [
        _extract_generated_question_items(strategy),
        _extract_all_generated_question_items(strategy),
        _build_generated_question_text_payload(strategy)[0],
        _normalize_generated_questions([question], expected_count=1),
    ]

    for rows in exports:
        row = rows[0]
        assert row["ready"] is True
        assert row["follow_ups"] == question["follow_ups"]
        assert row["structured_follow_ups"][0]["trigger"] == "always"
        assert row["evaluation_points"] == question["evaluation_points"]
        assert row["task_conditions"]["case_materials"][0]["value"] == "14:00에서 13:30으로 변경"
        assert row["assessment_guide"]["rating_levels"][0]["score"] == 5
        assert row["assessment_guide"]["dimension_rubrics"][0]["weight"] == 25
        assert row["assessment_guide"]["critical_floor"]["score"] == 1
        assert row["ncs_traceability"]["element_name"] == "일정 조정"
        assert row["ksa_refs"] == ["일정 조정 능력"]
        assert row["ksa_evidence"][0]["evidence_id"] == "ksa_1234567890abcdef12345678"
        assert row["evidence_ids"] == ["ksa_1234567890abcdef12345678"]
        assert row["question_artifact_hash"] == "artifact-hash"
        assert row["match_bundle_fingerprint"] == "bundle-fingerprint"
        serialized = json.dumps(row, ensure_ascii=False)
        assert "must-not-leak" not in serialized
        assert "openai_api_key" not in serialized
        assert "question_task_frame" not in serialized


def test_known_reviewed_provider_sources_remain_publicly_ready() -> None:
    for source in (
        "openai_api",
        "openrouter_api_quality_repaired_fields",
        "codex_cli",
        "claude_code_quality_repaired_fields",
    ):
        question = {**_structured_interviewer_question(), "question_source": source}
        row = _normalize_generated_questions([question], expected_count=1)[0]
        assert row["question_source"] == source
        assert row["ready"] is True
