from fastapi.testclient import TestClient
import pytest

import app.main as main


@pytest.fixture(autouse=True)
def _configured_ncs_mcp(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")


def _question(text: str, question_type: str = "면접질문") -> dict[str, object]:
    return {
        "question": text,
        "type": question_type,
        "question_type": question_type,
        "ncsClCd": "0202030201_25v3",
        "question_focus": text,
        "question_focus_source": "official_ksa",
        "ksa_refs": [text],
        "follow_ups": [],
        "eval_points": [],
    }


def test_extract_question_texts_keeps_only_latest_bounded_history() -> None:
    history = [f"반복 문항 {index}" for index in range(505)]

    extracted = main._extract_question_texts(history)

    assert len(extracted) == main._MAX_AVOID_QUESTION_ITEMS
    assert extracted[0] == "반복 문항 5"
    assert extracted[-1] == "반복 문항 504"


def test_extract_question_texts_bounds_item_length_and_prefers_latest_duplicate() -> None:
    oversized = "가" * (main._MAX_AVOID_QUESTION_CHARS + 50)
    extracted = main._extract_question_texts(
        [
            {"question": "동일한 반복 문항"},
            {"text": oversized},
            {"question": "동일한 반복 문항"},
        ]
    )

    assert extracted[-1] == "동일한 반복 문항"
    assert len(extracted[0]) == main._MAX_AVOID_QUESTION_CHARS
    assert extracted.count("동일한 반복 문항") == 1


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
                _question("새로운 문서 요구사항 판단 질문입니다."),
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


def test_generate_from_text_twenty_five_operational_cycles_repair_shallow_and_repeated_questions(
    monkeypatch,
) -> None:
    unit = {
        "ncsClCd": "0202030201_25v3",
        "compeUnitName": "문서작성",
        "compeUnitLevel": "3",
        "ncsSubdCdnm": "사무행정",
        "compeUnitDef": "문서 요구사항을 파악하고 오류를 점검한다",
    }
    ksa = {
        "ncsClCd": unit["ncsClCd"],
        "compeUnitName": unit["compeUnitName"],
        "factorName": "문서 오류 점검 능력",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
        "ksaTypeName": "기술",
    }
    monkeypatch.setattr(main, "fetch_ncs_ksa_by_units", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **kwargs: {})
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        main,
        "build_jd_strategy_with_openai",
        lambda **kwargs: {
            "interview_questions": [
                {
                    "type": "경험면접",
                    "ncsClCd": unit["ncsClCd"],
                    "competency": unit["compeUnitName"],
                    "question_focus": ksa["factorName"],
                    "ksa_refs": [ksa["factorName"]],
                    "question": "문서 오류 점검 능력과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
                    "follow_ups": ["어떤 경험입니까?", "무엇을 했습니까?", "결과는 어땠습니까?"],
                    "evaluation_points": ["성실성", "태도", "열정", "표현력"],
                }
            ]
        },
    )

    base_payload = {
        "notice_text": "사무행정 문서작성 담당자는 문서 오류를 확인하고 기록 품질을 관리합니다.",
        "duty_text": "문서 오류 확인과 보완",
        "selected_ncs": [unit],
        "question_plan": {
            "items": [
                {"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}
            ]
        },
        "interview_methods": ["경험면접"],
        "openai_api_key": "sk-test",
    }
    history: list[str] = []
    generated: list[str] = []

    with TestClient(main.app) as client:
        for cycle in range(25):
            payload = {
                **base_payload,
                "avoid_questions": list(history),
                "generation_offset": cycle,
            }
            response = client.post("/api/questions/generate-from-text", json=payload)
            assert response.status_code == 200, response.text
            body = response.json()
            questions = body["strategy"]["interview_questions"]
            orchestration = body["strategy"]["question_quality_orchestration"]
            assert len(questions) == 1
            assert orchestration["status"] == "passed"
            assert orchestration["unresolved_count"] == 0
            assert orchestration["generation_offset"] == cycle
            assert body["strategy"]["question_quality_report"]["passed"] is True
            assert "경험이 있으십니까" not in questions[0]["question"]
            generated.append(questions[0]["question"])
            history.extend(generated[-1:])

    assert len(set(generated)) == 25


def test_review_then_regenerate_uses_feedback_and_rotates_quality_run(monkeypatch) -> None:
    code = "SIM-OPER-REVIEW-001"
    unit = {
        "ncsClCd": code,
        "compeUnitName": "운영문서검토",
        "compeUnitLevel": "3",
        "ncsSubdCdnm": "운영지원",
        "compeUnitDef": "운영 문서의 오류와 기준 불일치를 점검한다",
    }
    ksa = {
        "ncsClCd": code,
        "compeUnitName": unit["compeUnitName"],
        "factorName": "운영 문서 오류 점검 능력",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
        "ksaTypeName": "기술",
    }
    contexts: list[str] = []

    monkeypatch.setattr(main, "fetch_ncs_ksa_by_units", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **kwargs: {})

    def fake_strategy(**kwargs):
        contexts.append(str(kwargs.get("extra_context") or ""))
        return {
            "interview_questions": [
                {
                    "type": "경험면접",
                    "ncsClCd": code,
                    "competency": unit["compeUnitName"],
                    "question_focus": ksa["factorName"],
                    "ksa_refs": [ksa["factorName"]],
                    "question": "운영 문서 오류 점검 능력과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
                    "follow_ups": ["어떤 경험입니까?", "무엇을 했습니까?", "결과는 어땠습니까?"],
                    "evaluation_points": ["성실성", "태도", "열정", "표현력"],
                }
            ]
        }

    monkeypatch.setattr(main, "build_jd_strategy_with_openai", fake_strategy)
    payload = {
        "notice_text": "운영지원 담당자는 운영 문서 오류를 확인하고 보완합니다.",
        "duty_text": "운영 문서 오류 확인",
        "selected_ncs": [unit],
        "question_plan": {
            "items": [
                {"detail": "운영지원", "enabled": True, "main_count": 1, "follow_up_count": 3}
            ]
        },
        "interview_methods": ["경험면접"],
        "openai_api_key": "sk-test",
    }

    with TestClient(main.app) as client:
        first = client.post("/api/questions/generate-from-text", json=payload)
        assert first.status_code == 200, first.text
        first_strategy = first.json()["strategy"]
        first_question = first_strategy["interview_questions"][0]
        first_control = first_strategy["quality_control"]
        review = client.post(
            f"/api/quality/runs/{first_control['run_id']}/review",
            json={
                "review_token": first_control["review_token"],
                "question_hash": first_question["question_hash"],
                "question_text": first_question["question"],
                "question_index": 1,
                "ncs_code": code,
                "method": first_question["type"],
                "verdict": "needs_edit",
                "issue_codes": ["too_generic"],
                "reviewer_ref": "operational-regression-test",
            },
        )
        assert review.status_code == 200, review.text

        second = client.post(
            "/api/questions/generate-from-text",
            json={**payload, "avoid_questions": [first_question["question"]]},
        )
        assert second.status_code == 200, second.text

    second_strategy = second.json()["strategy"]
    second_question = second_strategy["interview_questions"][0]
    second_control = second_strategy["quality_control"]
    assert len(contexts) == 2
    assert "이전 거절·수정 피드백" in contexts[1]
    assert "too_generic" in contexts[1]
    assert second_question["question"] != first_question["question"]
    assert second_control["run_id"] != first_control["run_id"]
    assert second_control["review_token"] != first_control["review_token"]


def test_generate_from_text_isolates_repair_exception_instead_of_http_500(monkeypatch) -> None:
    code = "SIM-REPAIR-ERROR-001"
    unit = {
        "ncsClCd": code,
        "compeUnitName": "문서작성",
        "compeUnitLevel": "3",
        "ncsSubdCdnm": "사무행정",
        "compeUnitDef": "문서 요구사항을 파악하고 오류를 점검한다",
    }
    ksa = {
        "ncsClCd": code,
        "compeUnitName": unit["compeUnitName"],
        "factorName": "문서 오류 점검 능력",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
        "ksaTypeName": "기술",
    }
    real_question_for_method = main._question_for_method
    repeated_question = real_question_for_method(
        "경험면접",
        unit["compeUnitName"],
        ksa["factorName"],
        unit["ncsSubdCdnm"],
        unit["compeUnitDef"],
        "기술",
    )

    monkeypatch.setattr(main, "fetch_ncs_ksa_by_units", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **kwargs: {})
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        main,
        "build_jd_strategy_with_openai",
        lambda **kwargs: {
            "interview_questions": [
                {
                    "type": "경험면접",
                    "ncsClCd": code,
                    "competency": unit["compeUnitName"],
                    "question_focus": ksa["factorName"],
                    "ksa_refs": [ksa["factorName"]],
                    "question": "문서 오류 점검 능력과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
                    "follow_ups": ["어떤 상황입니까?", "무엇을 했습니까?", "결과는 무엇입니까?"],
                    "evaluation_points": ["상황", "행동", "결과", "학습"],
                }
            ]
        },
    )

    def fail_only_runtime_repair(*args, **kwargs):
        if int(kwargs.get("variation_index") or 0) > 0:
            raise RuntimeError("simulated item repair failure")
        return real_question_for_method(*args, **kwargs)

    monkeypatch.setattr(main, "_question_for_method", fail_only_runtime_repair)
    payload = {
        "notice_text": "사무행정 문서작성 담당자는 문서 오류를 확인하고 기록 품질을 관리합니다.",
        "duty_text": "문서 오류 확인과 보완",
        "selected_ncs": [unit],
        "question_plan": {
            "items": [
                {"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}
            ]
        },
        "interview_methods": ["경험면접"],
        "avoid_questions": [repeated_question],
        "openai_api_key": "sk-test",
    }

    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json=payload)

    assert response.status_code == 200, response.text
    strategy = response.json()["strategy"]
    metadata = strategy["question_quality_orchestration"]
    assert strategy["interview_questions"][0]["question"] == repeated_question
    assert metadata["status"] == "needs_review"
    assert metadata["repair_error_count"] == 36
    assert metadata["unresolved_count"] == 1
    assert metadata["items"][0]["errors"]


def test_generate_from_text_degrades_second_adjustment_failure_instead_of_http_500(monkeypatch) -> None:
    code = "SIM-SECOND-ADJUST-001"
    unit = {
        "ncsClCd": code,
        "compeUnitName": "문서작성",
        "compeUnitLevel": "3",
        "ncsSubdCdnm": "사무행정",
        "compeUnitDef": "문서 요구사항을 확인하고 오류를 검증한다.",
    }
    ksa = {
        "ncsClCd": code,
        "compeUnitName": unit["compeUnitName"],
        "factorName": "문서 오류 검증 기술",
        "factorSource": "ncs-mcp",
        "ksaStatus": "official",
        "ksaTypeName": "기술",
    }
    method = "경험면접"
    focus = ksa["factorName"]
    evaluation_points = main._method_evaluation_points(method, [focus], "기술")
    fallback_question = {
        "type": method,
        "method": method,
        "ncsClCd": code,
        "competency": unit["compeUnitName"],
        "ncs_detail": unit["ncsSubdCdnm"],
        "ncsSubdCdnm": unit["ncsSubdCdnm"],
        "compeUnitDef": unit["compeUnitDef"],
        "question_focus": focus,
        "question_focus_type": "기술",
        "question_focus_source": "official_ksa",
        "ksa_refs": [focus],
        "question": main._question_for_method(
            method,
            unit["compeUnitName"],
            focus,
            unit["ncsSubdCdnm"],
            unit["compeUnitDef"],
            "기술",
        ),
        "follow_ups": main._followups_for_method(
            method,
            unit["compeUnitName"],
            focus,
            3,
            focus_type="기술",
        ),
        "evaluation_points": evaluation_points,
        "question_source": "template_fallback",
        "model_question_preserved": False,
        "task_conditions": main._task_conditions_for_method(
            method,
            unit["compeUnitName"],
            focus,
            unit["ncsSubdCdnm"],
            unit["compeUnitDef"],
        ),
        "assessment_guide": main._behavior_anchored_evaluation(
            method,
            focus,
            evaluation_points,
            "기술",
        ),
    }

    monkeypatch.setattr(main, "fetch_ncs_ksa_by_units", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **kwargs: {})
    monkeypatch.setattr(main, "build_jd_strategy_with_openai", lambda **kwargs: {})
    monkeypatch.setattr(
        main,
        "build_strategy_with_rule_fallback",
        lambda **kwargs: {
            "interview_questions": [dict(fallback_question)],
            "question_plan_used": {"total_main_count": 1, "follow_up_count": 3},
        },
    )
    monkeypatch.setattr(
        main,
        "_adjust_generated_questions",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated adjustment defect")),
    )
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *args, **kwargs: {})
    payload = {
        "notice_text": "사무행정 담당자는 문서 요구사항과 오류를 확인합니다.",
        "duty_text": "문서 오류 확인과 검증",
        "selected_ncs": [unit],
        "question_plan": {
            "items": [
                {"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}
            ]
        },
        "interview_methods": [method],
        "openai_api_key": "sk-test",
    }

    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json=payload)

    assert response.status_code == 200, response.text
    strategy = response.json()["strategy"]
    assert strategy["fallback_adjustment_status"] == "degraded_to_runtime_recheck"
    orchestration = strategy["question_quality_orchestration"]
    assert orchestration["status"] == "needs_review"
    assert orchestration["unresolved_count"] == 0
    assert orchestration["operational_warnings"] == ["fallback_adjustment_degraded"]
    assert orchestration["stages"][0]["status"] == "degraded"
    assert strategy["question_quality_report"]["passed"] is True
