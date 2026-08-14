from fastapi.testclient import TestClient
import pytest

import app.main as main


REQUEST_OPENAI_KEY = "sk-request-scoped-question-history-test"


@pytest.fixture(autouse=True)
def _configured_request_scoped_boundaries(monkeypatch):
    monkeypatch.setenv("NCS_MCP_URL", "http://mcp.example/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # These tests exercise request-local history and deduplication only.  The
    # public quality boundary has dedicated endpoint tests with production-like
    # evidence metadata.
    monkeypatch.setattr(main, "_require_official_ksa_result", lambda _result: None)


def _question(text: str, question_type: str = "면접질문") -> dict[str, object]:
    return {
        "question": text,
        "type": question_type,
        "question_type": question_type,
        "ncsClCd": "0202030201_25v3",
        "question_focus": text,
        "question_focus_source": "official_ksa",
        "ksa_refs": [text],
        "question_source": "openai_api",
        "follow_ups": [],
        "eval_points": [],
    }


def _ready_openai_model_question(
    unit: dict[str, str],
    ksa: dict[str, str],
    *,
    scenario: str,
) -> dict[str, object]:
    focus = ksa["factorName"]
    if "요구사항" in focus:
        question = (
            f"{scenario} 실제 경험을 말씀해 주세요. 본인이 필수 조건과 조정 가능한 "
            "조건을 어떻게 구분했으며, 최종 요구사항 대조표가 승인된 결과를 설명해 주세요."
        )
        follow_ups = [
            "방금 필수로 분류한 조건에서 빠뜨린 요청 주체가 있다면 누구이며 어떻게 다시 확인하겠습니까?",
            "앞서 선택한 요구 조건과 원문이 다르다는 반증이 나오면 대조표의 어느 항목부터 고치겠습니까?",
            "말씀한 대조표가 최종 초안에 반영됐음을 어떤 승인 기록으로 확인했습니까?",
        ]
        evaluation_points = [
            "필수 조건과 조정 가능 조건의 구분 근거",
            "요청 주체와 원문을 대조한 행동",
            "반증 발견 시 수정할 대조표 항목",
            "최종 초안 반영을 확인한 승인 기록",
        ]
    else:
        question = (
            f"운영 보고서에서 {scenario} 실제 경험을 말씀해 주세요. 마감 준수와 "
            "오류 정정 중 무엇을 우선할지 본인이 내린 결정, 직접 점검한 자료, "
            "수정본이 승인되기까지의 결과를 설명해 주세요."
        )
        follow_ups = [
            "방금 설명한 점검에서 빠뜨린 원자료가 있다면 무엇이며 왜 먼저 확인하지 않았습니까?",
            "앞서 선택한 원자료의 신뢰성을 뒤집는 반증이 나왔다면 어느 오류부터 다시 점검하겠습니까?",
            "말씀한 수정 결과를 확인할 승인 기록이나 정정 전후 수치는 무엇입니까?",
        ]
        evaluation_points = [
            "마감과 오류 정정 사이의 선택 근거",
            "지원자가 직접 수행한 원자료 점검",
            "반증 발견 시 재점검 범위",
            "승인 기록 또는 정정 전후 결과",
        ]
    return {
        "type": "경험면접",
        "method": "경험면접",
        "ncsClCd": unit["ncsClCd"],
        "competency": unit["compeUnitName"],
        "ncs_detail": unit["ncsSubdCdnm"],
        "question_focus": focus,
        "question_focus_source": "official_ksa",
        "question_evidence_id": main.stable_ksa_evidence_id(ksa),
        "question_evidence_required": True,
        "ksa_refs": [focus],
        "question_source": "openai_api",
        "model_question_preserved": True,
        "question": question,
        "follow_ups": follow_ups,
        "evaluation_points": evaluation_points,
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
                "openai_api_key": REQUEST_OPENAI_KEY,
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
                "openai_api_key": REQUEST_OPENAI_KEY,
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


def test_generate_from_text_uses_only_current_request_avoid_questions(monkeypatch) -> None:
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
        return {
            "interview_questions": [
                _ready_openai_model_question(
                    unit,
                    ksa,
                    scenario="두 부서의 요구가 충돌한 문서 초안을 조정한",
                )
            ]
        }

    monkeypatch.setattr(main, "build_jd_strategy_with_openai", fake_build_strategy_with_openai)

    with TestClient(main.app) as client:
        response = client.post(
            "/api/questions/generate-from-text",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
                "notice_text": "사무행정 담당업무",
                "selected_ncs": [unit],
                "question_plan": {
                    "items": [
                        {
                            "detail": "사무행정",
                            "enabled": True,
                            "main_count": 1,
                            "follow_up_count": 3,
                        }
                    ]
                },
                "interview_methods": ["경험면접"],
                "avoid_questions": [{"question": "현재 화면의 중복 질문입니다."}],
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
            "generation_mode": "openai_api",
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
                "openai_api_key": REQUEST_OPENAI_KEY,
                "ncs_code": "0202030201_25v3",
                "competency_name": "문서작성",
                "target_count": 1,
            },
        )
        second = client.post(
            "/api/questions/generate-by-ncs-code",
            json={
                "openai_api_key": REQUEST_OPENAI_KEY,
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
            "generation_mode": "openai_api",
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
                "openai_api_key": REQUEST_OPENAI_KEY,
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
            "generation_mode": "openai_api",
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
                "openai_api_key": REQUEST_OPENAI_KEY,
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


def test_generate_from_text_rejects_deterministic_repairs_across_history_cycles(
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
    captured_contexts: list[str] = []
    captured_offsets: list[int | None] = []

    def low_realism_model_strategy(**kwargs):
        captured_contexts.append(str(kwargs.get("extra_context") or ""))
        return {
            "interview_questions": [
                {
                    "type": "경험면접",
                    "method": "경험면접",
                    "ncsClCd": unit["ncsClCd"],
                    "competency": unit["compeUnitName"],
                    "question_focus": ksa["factorName"],
                    "question_focus_source": "official_ksa",
                    "question_evidence_id": main.stable_ksa_evidence_id(ksa),
                    "question_evidence_required": True,
                    "ksa_refs": [ksa["factorName"]],
                    "question_source": "openai_api",
                    "model_question_preserved": True,
                    "question": "문서 오류 점검 능력과 관련하여 실제 경험이 있으십니까? 말씀해 주세요.",
                    "follow_ups": ["어떤 경험입니까?", "무엇을 했습니까?", "결과는 어땠습니까?"],
                    "evaluation_points": ["성실성", "태도", "열정", "표현력"],
                }
            ]
        }

    real_orchestrator = main._run_runtime_question_quality_orchestration

    def capture_orchestration_offset(strategy, **kwargs):
        captured_offsets.append(kwargs.get("generation_offset"))
        result = real_orchestrator(strategy, **kwargs)
        result["interview_questions"][0]["question"] = "결정론적으로 교체된 질문"
        result["interview_questions"][0]["question_source"] = (
            "quality_orchestrator_repair"
        )
        return result

    monkeypatch.setattr(main, "build_jd_strategy_with_openai", low_realism_model_strategy)
    monkeypatch.setattr(
        main,
        "_run_runtime_question_quality_orchestration",
        capture_orchestration_offset,
    )

    base_payload = {
        "openai_api_key": REQUEST_OPENAI_KEY,
        "notice_text": "사무행정 문서작성 담당자는 문서 오류를 확인하고 기록 품질을 관리합니다.",
        "duty_text": "문서 오류 확인과 보완",
        "selected_ncs": [unit],
        "question_plan": {
            "items": [
                {"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}
            ]
        },
        "interview_methods": ["경험면접"],
    }
    history: list[str] = []

    with TestClient(main.app) as client:
        for cycle in range(25):
            payload = {
                **base_payload,
                "avoid_questions": list(history),
                "generation_offset": cycle,
            }
            response = client.post("/api/questions/generate-from-text", json=payload)
            assert response.status_code == 502
            assert response.json() == {
                "detail": {
                    "code": "openai_api_quality_rejected",
                    "provider": "openai_api",
                    "message": "생성된 질문이 NCS/KSA 품질 검사를 통과하지 못했습니다. 문항 수나 면접기법 범위를 줄여 다시 시도해 주세요.",
                    "retryable": True,
                }
            }
            assert "경험이 있으십니까" not in response.text
            assert "template_fallback" not in response.text
            assert "strategy" not in response.json()
            history.append(f"이전 화면 질문 {cycle}")

    # A valid model response that fails the final quality boundary receives one
    # bounded full-set retry. Both attempts preserve the request's rotation
    # offset; no third attempt is made.
    assert captured_offsets == [cycle for cycle in range(25) for _ in range(2)]
    assert len(captured_contexts) == 50
    for cycle in range(1, 25):
        primary_context = captured_contexts[cycle * 2]
        retry_context = captured_contexts[(cycle * 2) + 1]
        assert f"이전 화면 질문 {cycle - 1}" in primary_context
        assert f"이전 화면 질문 {cycle - 1}" in retry_context
        assert "[서버 품질 재생성 지침]" in retry_context
        assert REQUEST_OPENAI_KEY not in retry_context


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
        scenario = (
            "승인본과 원자료의 수치 불일치를 찾아 관계 부서와 수정한"
            if len(contexts) == 1
            else "이관 문서의 필수 항목 누락을 찾아 담당 부서와 보완한"
        )
        return {
            "interview_questions": [
                _ready_openai_model_question(
                    unit,
                    ksa,
                    scenario=scenario,
                )
            ]
        }

    monkeypatch.setattr(main, "build_jd_strategy_with_openai", fake_strategy)
    payload = {
        "openai_api_key": REQUEST_OPENAI_KEY,
        "notice_text": "운영지원 담당자는 운영 문서 오류를 확인하고 보완합니다.",
        "duty_text": "운영 문서 오류 확인",
        "selected_ncs": [unit],
        "question_plan": {
            "items": [
                {"detail": "운영지원", "enabled": True, "main_count": 1, "follow_up_count": 3}
            ]
        },
        "interview_methods": ["경험면접"],
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


def test_generate_from_text_repair_exception_returns_sanitized_502_without_fallback(
    monkeypatch,
) -> None:
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

    repair_failures = 0

    def fail_only_runtime_repair(*args, **kwargs):
        nonlocal repair_failures
        if int(kwargs.get("variation_index") or 0) > 0:
            repair_failures += 1
            raise RuntimeError("simulated item repair failure")
        return real_question_for_method(*args, **kwargs)

    monkeypatch.setattr(main, "_question_for_method", fail_only_runtime_repair)
    payload = {
        "openai_api_key": REQUEST_OPENAI_KEY,
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
    }

    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json=payload)

    assert repair_failures > 0
    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "openai_api_quality_rejected",
            "provider": "openai_api",
            "message": "생성된 질문이 NCS/KSA 품질 검사를 통과하지 못했습니다. 문항 수나 면접기법 범위를 줄여 다시 시도해 주세요.",
            "retryable": True,
        }
    }
    assert repeated_question not in response.text
    assert "simulated item repair failure" not in response.text
    assert "template_fallback" not in response.text
    assert "strategy" not in response.json()


def test_generate_from_text_surfaces_adjustment_failure_without_rule_fallback(
    monkeypatch,
) -> None:
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
    builder_called = False

    def model_strategy(**kwargs):
        nonlocal builder_called
        builder_called = True
        return {
            "interview_questions": [
                {
                    "type": method,
                    "ncsClCd": code,
                    "competency": unit["compeUnitName"],
                    "question_focus": focus,
                    "question_focus_source": "official_ksa",
                    "ksa_refs": [focus],
                    "question_source": "openai_api",
                    "question": "문서 오류 검증 기술을 적용해 문제를 해결한 경험을 설명해 주세요.",
                    "follow_ups": ["판단 기준은 무엇이었습니까?"],
                    "evaluation_points": ["판단근거", "행동", "결과"],
                }
            ]
        }

    monkeypatch.setattr(main, "fetch_ncs_ksa_by_units", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "rank_ksa_factors_by_query", lambda **kwargs: [ksa])
    monkeypatch.setattr(main, "build_ncs_context_pack", lambda **kwargs: {})
    monkeypatch.setattr(main, "build_jd_strategy_with_openai", model_strategy)
    monkeypatch.setattr(
        main,
        "_adjust_generated_questions",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated adjustment defect")),
    )
    monkeypatch.setattr(main, "_register_question_quality_evidence", lambda *args, **kwargs: {})
    payload = {
        "openai_api_key": REQUEST_OPENAI_KEY,
        "notice_text": "사무행정 담당자는 문서 요구사항과 오류를 확인합니다.",
        "duty_text": "문서 오류 확인과 검증",
        "selected_ncs": [unit],
        "question_plan": {
            "items": [
                {"detail": "사무행정", "enabled": True, "main_count": 1, "follow_up_count": 3}
            ]
        },
        "interview_methods": [method],
    }

    with TestClient(main.app) as client:
        response = client.post("/api/questions/generate-from-text", json=payload)

    assert builder_called is True
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "openai_api_generation_failed",
        "provider": "openai_api",
        "message": "OpenAI API에서 질문을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        "retryable": True,
    }
    assert "simulated adjustment defect" not in response.text
