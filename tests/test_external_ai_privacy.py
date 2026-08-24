from __future__ import annotations

from app.services.ai_question_quality_review import _review_prompt
from app.services.external_ai_privacy import sanitize_external_ai_source_text
from app.services.jd_strategy import _ai_authored_generation_prompt
from app.services import jd_strategy
from app.services.question_generation import _render_question_generation_prompt


SENSITIVE_SOURCE = """담당자: 홍길동
채용 담당자 김영희
담당자 박민수 문의 010-1111-2222
담당자 이솔 연락 02-111-2222
담당자 남궁민수 이메일 people@example.org
채용 문의 recruit@example.go.kr / 02-1234-5678
담당메일hr2@example.com
이메일 hr@example.org, 휴대전화 010-9876-5432
900101-1234567
김철수 (서명)
계약 서명본을 검토하고 문서 오류를 정정하는 업무"""


def _assert_private_values_removed(prompt: str) -> None:
    for value in (
        "홍길동",
        "김영희",
        "박민수",
        "이솔",
        "남궁민수",
        "김철수",
        "recruit@example.go.kr",
        "hr@example.org",
        "hr2@example.com",
        "02-1234-5678",
        "010-9876-5432",
        "900101-1234567",
    ):
        assert value not in prompt


def test_external_ai_source_sanitizer_removes_direct_identifiers_without_erasing_work() -> None:
    cleaned = sanitize_external_ai_source_text(SENSITIVE_SOURCE)

    _assert_private_values_removed(cleaned)
    assert "[연락처 정보 제거]" in cleaned
    assert "[이메일 제거]" in cleaned
    assert "[전화번호 제거]" in cleaned
    assert "[주민등록번호 제거]" in cleaned
    assert "[서명 정보 제거]" in cleaned
    assert "계약 서명본을 검토하고 문서 오류를 정정하는 업무" in cleaned


def test_ai_authored_prompt_sanitizes_every_job_context_layer() -> None:
    prompt = _ai_authored_generation_prompt(
        planned_sequence=[
            {
                "index": 1,
                "type": "상황면접",
                "detail": "사무행정",
                "ncsClCd": "0202030203_22v4",
                "compeUnitName": "자료 관리",
                "compeUnitDef": "업무 자료를 안전하게 관리하는 능력",
                "required_element_name": "자료 보안 관리하기",
                "evidence_id": "ksa_test",
                "required_ksa_type": "지식",
                "required_factorName": "개인정보보호법",
            }
        ],
        target_count=1,
        follow_up_count=3,
        notice_text=SENSITIVE_SOURCE,
        jd_text=SENSITIVE_SOURCE,
        duty_text=SENSITIVE_SOURCE,
        evaluation_text=SENSITIVE_SOURCE,
        extra_context=SENSITIVE_SOURCE,
    )

    _assert_private_values_removed(prompt)
    assert "계약 서명본을 검토하고 문서 오류를 정정하는 업무" in prompt


def test_independent_review_prompt_sanitizes_job_context() -> None:
    prompt = _review_prompt(
        questions=[
            {
                "type": "상황면접",
                "question": "문서 오류를 발견하면 어떻게 처리하시겠습니까?",
                "follow_ups": ["무엇을 먼저 확인합니까?"],
                "evaluation_points": ["확인 근거를 설명한다"],
                "question_evidence_id": "ksa_test",
            }
        ],
        ncs_matches=[],
        ncs_ksa=[],
        interview_methods=["상황면접"],
        job_context={"notice": SENSITIVE_SOURCE, "job_description": SENSITIVE_SOURCE},
    )

    _assert_private_values_removed(prompt)


def test_auxiliary_generation_prompt_sanitizes_job_context() -> None:
    prompt = _render_question_generation_prompt(
        ncs_rows=[],
        ksa_rows=[],
        jd_text=SENSITIVE_SOURCE,
        strengths=SENSITIVE_SOURCE,
        mode="ksa_driven",
        target_count=1,
        extra_context=SENSITIVE_SOURCE,
    )

    _assert_private_values_removed(prompt)
    assert "[연락처 정보 제거]" in prompt


def test_ncs_ai_rerank_sanitizes_job_description_before_transport(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setenv("ENABLE_AI_RERANK", "true")
    monkeypatch.setattr(
        jd_strategy,
        "_check_openai_connectivity",
        lambda api_key, ttl_sec=60: (True, ""),
    )

    def fake_post(**kwargs):
        captured.update(kwargs["payload"])
        return {"choices": [{"message": {"content": '{"ordered_codes":["02020302","02020301"]}'}}]}

    monkeypatch.setattr(jd_strategy, "post_chat_completions_with_retries", fake_post)
    result = jd_strategy._ai_rerank_ncs_matches(
        jd_text=SENSITIVE_SOURCE,
        ranked_items=[
            {"ncsClCd": "02020302", "compeUnitName": "자료 관리", "score": 2.0},
            {"ncsClCd": "02020301", "compeUnitName": "문서 작성", "score": 1.0},
        ],
        top_k=2,
        api_key_override="test-key",
        generation_provider="openai_api",
    )

    assert result
    user_content = captured["messages"][1]["content"]
    _assert_private_values_removed(user_content)
    assert "계약 서명본을 검토하고 문서 오류를 정정하는 업무" in user_content
