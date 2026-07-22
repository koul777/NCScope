from app.services.jd_strategy import _ensure_diverse_question_set, _normalize_question_key


def _q(text: str, follow: str = "", q_type: str = "상황면접") -> dict:
    return {
        "type": q_type,
        "competency": "예산관리",
        "ncsClCd": "0202010203_19v2",
        "question": text,
        "follow_up": follow,
        "evaluation_points": ["우선순위", "리스크대응"],
    }


def test_normalize_question_key_ignores_followup_variation():
    q1 = _q(
        "예산 초과 위기 상황이 발생했을 때 어떤 우선순위로 대응하겠습니까?",
        "당시 보고 체계도 설명해 주세요.",
    )
    q2 = _q(
        "예산 초과 위기 상황이 발생했을 때 어떤 우선순위로 대응하겠습니까?",
        "재발 방지 대책까지 말해 주세요.",
    )

    assert _normalize_question_key(q1) == _normalize_question_key(q2)


def test_ensure_diverse_question_set_filters_near_duplicates():
    q1 = _q("예산 초과 위기 상황이 발생했을 때 어떤 우선순위로 대응하겠습니까?")
    q2 = _q("예산 초과 위기 상황에서 어떤 우선순위로 먼저 대응하시겠습니까?")
    q3 = _q("협업 부서가 자료 제출을 거부할 때 설득 기준을 어떻게 정하겠습니까?")

    merged = _ensure_diverse_question_set(
        generated=[q1, q2, q3],
        fallback_pool=[],
        target_count=10,
    )

    merged_questions = [item["question"] for item in merged]
    assert q1["question"] in merged_questions
    assert q2["question"] not in merged_questions
    assert q3["question"] in merged_questions


def test_ensure_diverse_question_set_prefers_generated_first():
    generated = [_q("결재 지연이 반복될 때 첫 30분 조치를 어떻게 설계하겠습니까?")]
    fallback = [_q("업무 표준화가 필요한 영역을 무엇으로 판단하겠습니까?")]

    merged = _ensure_diverse_question_set(
        generated=generated,
        fallback_pool=fallback,
        target_count=2,
    )

    assert len(merged) == 2
    assert merged[0]["question"] == generated[0]["question"]
    assert merged[1]["question"] == fallback[0]["question"]
