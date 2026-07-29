from fastapi.testclient import TestClient
import pytest

import app.main as main


@pytest.fixture(autouse=True)
def _configured_ncs_mcp(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")


def _question(text: str, question_type: str = "경험면접") -> dict[str, object]:
    return {
        "question": text,
        "type": question_type,
        "question_type": question_type,
        "follow_ups": [],
        "eval_points": [],
    }


def test_generate_diverse_passes_request_avoid_questions_as_context(monkeypatch) -> None:
    captured_contexts: list[str] = []

    def fake_generate_diverse_interview_questions(**kwargs):
        captured_contexts.append(str(kwargs.get("extra_context") or ""))
        return {
            "ncs_ksa_available": True,
            "questions": [_question("자료 오류를 확인하고 보완한 사례를 설명해 주세요.")],
        }

    monkeypatch.setattr(main, "generate_diverse_interview_questions", fake_generate_diverse_interview_questions)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-diverse",
            json={
                "openai_api_key": "sk-test",
                "ncs_code": "0202030201_25v3",
                "competency_name": "문서작성",
                "target_count": 1,
                "avoid_questions": ["문서 요구사항 파악 경험을 설명해 주세요."],
            },
        )

    assert response.status_code == 200
    assert captured_contexts
    assert "반복 금지" in captured_contexts[0]
    assert "문서 요구사항 파악 경험" in captured_contexts[0]


def test_generate_batch_combines_request_avoid_and_current_request_questions(monkeypatch) -> None:
    captured_contexts: list[str] = []
    call_index = {"value": 0}

    def fake_generate_diverse_interview_questions(**kwargs):
        captured_contexts.append(str(kwargs.get("extra_context") or ""))
        call_index["value"] += 1
        return {
            "ncs_ksa_available": True,
            "questions": [
                _question(
                    f"자료 오류 점검 질문 {call_index['value']}: "
                    "자료 오류를 확인하고 처리 우선순위를 정한 상황을 설명해 주세요."
                )
            ]
        }

    monkeypatch.setattr(main, "generate_diverse_interview_questions", fake_generate_diverse_interview_questions)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-batch",
            json={
                "openai_api_key": "sk-test",
                "ncs_code": "0202030201_25v3",
                "competency_name": "문서작성",
                "batch_count": 10,
                "avoid_questions": ["문서 요구사항 파악 경험을 설명해 주세요."],
            },
        )

    assert response.status_code == 200
    assert len(captured_contexts) >= 2
    assert "문서 요구사항 파악 경험" in captured_contexts[0]
    assert "자료 오류 점검 질문 1" in captured_contexts[1]


def test_generate_from_text_uses_only_request_scoped_avoid_questions(monkeypatch) -> None:
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    unit = {
        "ncsClCd": "0202030201_25v3",
        "compeUnitName": "문서작성",
        "compeUnitLevel": "3",
        "ncsSubdCdnm": "사무행정",
        "compeUnitDef": "문서 요구사항을 파악한다",
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "문서 요구사항 파악",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
    }
    captured_contexts: list[str] = []

    monkeypatch.setattr(main, "fetch_ncs_ksa_by_units", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **kwargs: {})

    def fake_build_strategy_with_openai(**kwargs):
        captured_contexts.append(str(kwargs.get("extra_context") or ""))
        return {"interview_questions": []}

    monkeypatch.setattr(main, "build_jd_strategy_with_openai", fake_build_strategy_with_openai)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={
                "notice_text": "사무행정 담당업무",
                "selected_ncs": [unit],
                "avoid_questions": [{"question": "현재 화면의 중복 질문입니다."}],
                "openai_api_key": "sk-test",
            },
        )

    assert response.status_code == 200
    assert captured_contexts
    assert "현재 화면의 중복 질문" in captured_contexts[0]


def test_generate_by_ncs_code_does_not_share_output_with_later_request(monkeypatch) -> None:
    captured_contexts: list[str] = []
    call_index = {"value": 0}

    def fake_generate_interview_questions_by_ncs_code(**kwargs):
        captured_contexts.append(str(kwargs.get("extra_context") or ""))
        call_index["value"] += 1
        return {
            "generation_mode": "template_fallback",
            "ncs_ksa_available": True,
            "main_questions": [_question(f"요청 {call_index['value']}의 질문")],
            "follow_up_questions": [],
        }

    monkeypatch.setattr(
        main,
        "generate_interview_questions_by_ncs_code",
        fake_generate_interview_questions_by_ncs_code,
    )

    with TestClient(main.app) as client:
        first = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": "sk-test",
                "ncs_code": "0202030201_25v3",
                "competency_name": "문서작성",
                "target_count": 1,
            },
        )
        second = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": "sk-test",
                "ncs_code": "0202030201_25v3",
                "competency_name": "문서작성",
                "target_count": 1,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert captured_contexts == ["", ""]


def test_generate_by_ncs_code_uses_and_filters_request_avoid_questions(monkeypatch) -> None:
    captured_contexts: list[str] = []

    def fake_generate_interview_questions_by_ncs_code(**kwargs):
        captured_contexts.append(str(kwargs.get("extra_context") or ""))
        return {
            "generation_mode": "template_fallback",
            "ncs_ksa_available": True,
            "main_questions": [
                _question("현재 화면의 중복 질문입니다."),
                _question("새로운 문서 요구사항 판단 질문입니다.", "상황면접"),
            ],
            "follow_up_questions": [],
        }

    monkeypatch.setattr(
        main,
        "generate_interview_questions_by_ncs_code",
        fake_generate_interview_questions_by_ncs_code,
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": "sk-test",
                "ncs_code": "0202030201_25v3",
                "competency_name": "문서작성",
                "target_count": 2,
                "current_questions": [{"question": "현재 화면의 중복 질문입니다."}],
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "현재 화면의 중복 질문" in captured_contexts[0]
    assert [q["question"] for q in data["main_questions"]] == [
        "새로운 문서 요구사항 판단 질문입니다."
    ]
    assert data["question_count"] == 1


def test_generate_by_ncs_code_remaps_followups_after_avoid_filter(monkeypatch) -> None:
    def fake_generate_interview_questions_by_ncs_code(**kwargs):
        return {
            "generation_mode": "template_fallback",
            "ncs_ksa_available": True,
            "main_questions": [
                _question("duplicate first question"),
                _question("kept middle question", "situational"),
                _question("duplicate last question", "technical"),
            ],
            "follow_up_questions": [
                {"follow_up": "orphaned first follow-up", "for_question_index": 0, "step": 1},
                {"follow_up": "kept follow-up one", "for_question_index": 1, "step": 1},
                {"follow_up": "kept follow-up two", "for_question_index": "1", "step": 2},
                {"follow_up": "orphaned last follow-up", "for_question_index": 2, "step": 1},
                {"follow_up": "invalid index follow-up", "for_question_index": 99, "step": 1},
            ],
            "question_count": 3,
            "follow_up_count": 5,
            "total_count": 8,
        }

    monkeypatch.setattr(
        main,
        "generate_interview_questions_by_ncs_code",
        fake_generate_interview_questions_by_ncs_code,
    )

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": "sk-test",
                "ncs_code": "0202030201_25v3",
                "competency_name": "document writing",
                "target_count": 3,
                "avoid_questions": [
                    {"question": "duplicate first question"},
                    {"question": "duplicate last question"},
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert [question["question"] for question in data["main_questions"]] == ["kept middle question"]
    assert [question["follow_up"] for question in data["follow_up_questions"]] == [
        "kept follow-up one",
        "kept follow-up two",
    ]
    assert [question["for_question_index"] for question in data["follow_up_questions"]] == [0, 0]
    assert data["question_count"] == 1
    assert data["follow_up_count"] == 2
    assert data["total_count"] == 3
