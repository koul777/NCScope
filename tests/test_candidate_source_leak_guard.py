from __future__ import annotations

from app.main import (
    _adjust_generated_questions,
    _normalize_ncs_detail_term,
)


POLLUTED_DETAIL = (
    "공고·직무기술서상 (전기안전관리) 01.전기작업 안전관리 "
    "02.정전기 위험관리 03.전기 화재 위험관리 04.전기 방폭 관리 05.감전 위험관리"
)
POLLUTED_QUESTION = (
    f"{POLLUTED_DETAIL} ※ 국가유공자 등 예우 및 지원에 관한 법률 제31조제3항 "
    "- 1 - [NCS 기반 채용 직무 설명자료 :…을 수행하던 실제 상황에서 "
    "본인이 겪은 경험 사례 하나를 골라 어떤 판단과 행동을 했는지 말씀해 주세요."
)


def _plan(method: str) -> dict:
    return {
        "total_main_count": 1,
        "follow_up_count": 3,
        "selected_items": [
            {"detail": "전기안전관리", "enabled": True, "main_count": 1, "follow_up_count": 3}
        ],
        "question_sequence": [
            {"detail": "전기안전관리", "type": method, "follow_up_count": 3}
        ],
    }


def test_long_numbered_detail_row_is_reduced_to_one_ncs_label() -> None:
    assert _normalize_ncs_detail_term(POLLUTED_DETAIL) == "전기안전관리"


def test_all_public_methods_remove_notice_boilerplate_from_candidate_text() -> None:
    methods = [
        "경험면접",
        "상황면접",
        "토론면접",
        "인바스켓면접",
        "직무지식면접",
        "창의적 문제해결력면접",
    ]
    ncs_matches = [
        {
            "ncsClCd": "23060102",
            "compeUnitName": "전기설비 안전관리",
            "ncsSubdCdnm": "전기안전관리",
            "matchedDetailName": "전기안전관리",
            "compeUnitDef": "전기설비의 위험요인을 확인하고 안전조치를 수행하는 능력",
        }
    ]
    ncs_ksa = [
        {
            "ncsClCd": "23060102",
            "factorName": "안전 기준 적용 능력",
            "ksaTypeName": "기술",
            "factorSource": "ncs-mcp",
            "ksaStatus": "official",
        }
    ]
    for method in methods:
        result = _adjust_generated_questions(
            {
                "interview_questions": [
                    {
                        "type": method,
                        "question": POLLUTED_QUESTION,
                        "follow_ups": [POLLUTED_QUESTION] * 3,
                        "evaluation_points": [POLLUTED_QUESTION] * 4,
                    }
                ]
            },
            _plan(method),
            [method],
            ncs_matches=ncs_matches,
            ncs_ksa=ncs_ksa,
        )
        item = result["interview_questions"][0]
        visible = " ".join(
            [
                str(item.get("question") or ""),
                *(str(value or "") for value in item.get("follow_ups") or []),
                *(str(value or "") for value in item.get("evaluation_points") or []),
            ]
        )
        assert "국가유공자" not in visible
        assert "NCS 기반 채용 직무 설명자료" not in visible
        assert "공고·직무기술서상" not in visible
        assert "- 1 -" not in visible
