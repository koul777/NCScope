from __future__ import annotations

import json

from app.services import jd_strategy


def _sample_catalog() -> list[dict[str, str]]:
    return [
        {
            "ncs_code_no": "020203",
            "ncs_lclass_code": "02",
            "ncs_mclass_code": "02",
            "ncs_sclass_code": "03",
            "ncs_sclass_name": "일반사무",
        },
        {
            "ncs_code_no": "110101",
            "ncs_lclass_code": "11",
            "ncs_mclass_code": "01",
            "ncs_sclass_code": "01",
            "ncs_sclass_name": "경비·경호",
        },
    ]


def test_reverse_dictionary_prefers_anchor_synonym(monkeypatch):
    monkeypatch.setattr(jd_strategy, "load_sclass_catalog_from_csv", lambda *args, **kwargs: _sample_catalog())
    monkeypatch.setattr(
        jd_strategy,
        "load_sclass_synonym_dictionary",
        lambda *args, **kwargs: {
            "by_code_no": {"020203": ["행정지원직", "행정지원"]},
            "by_name": {"일반사무": ["행정지원직", "행정지원"]},
        },
    )

    jd_text = "\n".join(
        [
            "채용 직무기술서",
            "소분류: 행정지원직",
            "본 직무는 민원 및 문서 행정지원 업무를 수행한다",
            "시설 경비 업무와 협업 가능",
        ]
    )
    out = jd_strategy.infer_sclass_candidates_reverse_dictionary(jd_text=jd_text, max_items=3)

    assert out, "reverse dictionary candidates should not be empty"
    assert out[0]["ncs_code_no"] == "020203"
    assert "anchor=" in str(out[0].get("evidence", ""))


def test_reverse_dictionary_uses_synonym_without_exact_name(monkeypatch):
    monkeypatch.setattr(jd_strategy, "load_sclass_catalog_from_csv", lambda *args, **kwargs: _sample_catalog())
    monkeypatch.setattr(
        jd_strategy,
        "load_sclass_synonym_dictionary",
        lambda *args, **kwargs: {
            "by_code_no": {"020203": ["행정지원직", "행정사무"]},
            "by_name": {"일반사무": ["행정지원직", "행정사무"]},
        },
    )

    jd_text = "본 채용은 행정지원직 중심으로 문서관리와 행정사무를 수행한다."
    out = jd_strategy.infer_sclass_candidates_reverse_dictionary(jd_text=jd_text, max_items=2)

    assert out
    assert any(row.get("ncs_code_no") == "020203" for row in out)


def test_rerank_ncs_matches_ai_success(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_RERANK", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_ALLOW_SERVER_KEY_FALLBACK", "true")
    monkeypatch.setattr(jd_strategy, "_check_openai_connectivity", lambda api_key, ttl_sec=60: (True, ""))
    monkeypatch.setattr(
        jd_strategy,
        "rank_ncs_matches_by_jd",
        lambda jd_text, ncs_items, top_k=8: [
            {"ncsClCd": "02020302", "compeUnitName": "사무행정", "score": 3.5},
            {"ncsClCd": "02020101", "compeUnitName": "총무", "score": 2.7},
        ],
    )

    monkeypatch.setattr(
        jd_strategy,
        "post_chat_completions_with_retries",
        lambda **kwargs: {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"ordered_codes": ["02020101", "02020302"]},
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        },
    )

    ranked, mode = jd_strategy.rerank_ncs_matches(
        jd_text="총무 업무 중심의 채용",
        ncs_items=[{"ncsClCd": "02020302"}, {"ncsClCd": "02020101"}],
        top_k=2,
    )

    assert mode == "ai"
    assert [row.get("ncsClCd") for row in ranked] == ["02020101", "02020302"]
    assert ranked[0].get("rerank_method") == "ai"


def test_rerank_ncs_matches_fallback_on_invalid_ai(monkeypatch):
    monkeypatch.setenv("ENABLE_AI_RERANK", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_ALLOW_SERVER_KEY_FALLBACK", "true")
    monkeypatch.setattr(jd_strategy, "_check_openai_connectivity", lambda api_key, ttl_sec=60: (True, ""))
    monkeypatch.setattr(
        jd_strategy,
        "rank_ncs_matches_by_jd",
        lambda jd_text, ncs_items, top_k=8: [
            {"ncsClCd": "02020302", "compeUnitName": "사무행정", "score": 3.5},
            {"ncsClCd": "02020101", "compeUnitName": "총무", "score": 2.7},
        ],
    )

    monkeypatch.setattr(
        jd_strategy,
        "post_chat_completions_with_retries",
        lambda **kwargs: {"choices": [{"message": {"content": "{\"ordered_codes\": []}"}}]},
    )

    ranked, mode = jd_strategy.rerank_ncs_matches(
        jd_text="사무행정 채용",
        ncs_items=[{"ncsClCd": "02020302"}, {"ncsClCd": "02020101"}],
        top_k=2,
    )

    assert mode == "keyword"
    assert [row.get("ncsClCd") for row in ranked] == ["02020302", "02020101"]
    assert all(row.get("rerank_method") == "keyword" for row in ranked)


def test_ncs_code_template_fallback_rotates_question_methods(monkeypatch):
    monkeypatch.setenv("NCS_ALLOW_TEMPLATE_FALLBACK", "true")
    monkeypatch.setenv("NCS_AI_TOPUP_ATTEMPTS", "0")
    monkeypatch.setattr(jd_strategy, "_generate_questions_with_openai_from_ncs", lambda **kwargs: [])
    monkeypatch.setattr(
        jd_strategy,
        "fetch_ncs_ksa_by_units",
        lambda **kwargs: [
            {"ncsClCd": "U1", "factorName": "문서 요구사항 파악"},
            {"ncsClCd": "U1", "factorName": "일정 계획 수립"},
            {"ncsClCd": "U1", "factorName": "자료 오류 점검"},
        ],
    )

    result = jd_strategy.generate_interview_questions_by_ncs_code(
        ncs_code="U1",
        competency_name="문서작성",
        target_count=5,
        include_followups=True,
    )

    question_types = [row["question_type"] for row in result["main_questions"]]
    followup_sets = {tuple(row["follow_ups"]) for row in result["main_questions"]}

    assert result["template_fallback_used"] is True
    assert question_types[:5] == ["경험면접", "상황면접", "직무지식면접", "인바스켓면접", "발표면접"]
    assert len(followup_sets) == 5
    assert all(row["question_focus"] for row in result["main_questions"])
    assert all(row["question_intent"] for row in result["main_questions"])
    assert all(row["question_repeat_signature"] for row in result["main_questions"])
    assert all(row["question_repeat_duplicate"] is False for row in result["main_questions"])


def test_sclass_template_topup_keeps_ksa_with_its_own_unit(monkeypatch):
    units = [
        {"ncsClCd": "A", "compeUnitName": "A 능력단위", "ncsSclasCdnm": "테스트 세분류"},
        {"ncsClCd": "B", "compeUnitName": "B 능력단위", "ncsSclasCdnm": "테스트 세분류"},
    ]
    factors = {
        "A": ["A-factor-1", "A-factor-2"],
        "B": ["B-factor-1"],
    }

    def fake_fetch_ksa(*, ncs_matches, **kwargs):
        return [
            {
                "ncsClCd": unit["ncsClCd"],
                "compeUnitName": unit["compeUnitName"],
                "factorName": factor,
            }
            for unit in ncs_matches
            for factor in factors.get(unit["ncsClCd"], [])
        ]

    monkeypatch.setenv("NCS_ALLOW_TEMPLATE_FALLBACK", "true")
    monkeypatch.setenv("NCS_AI_TOPUP_ATTEMPTS", "0")
    monkeypatch.setattr(jd_strategy, "fetch_ncs_units_hrdk_by_sclass_code", lambda **kwargs: units)
    monkeypatch.setattr(jd_strategy, "fetch_ncs_ksa_by_units", fake_fetch_ksa)
    monkeypatch.setattr(jd_strategy, "_generate_questions_with_openai_from_ncs", lambda **kwargs: [])

    result = jd_strategy.generate_interview_questions_by_ncs_code(
        ncs_code="020203",
        competency_name="테스트 세분류",
        target_count=5,
        include_followups=False,
    )

    assert len(result["main_questions"]) == 5
    for question in result["main_questions"]:
        code = question["ncsClCd"]
        assert question["question_focus"] in factors[code]
        assert question["question_focus_source"] == "official_ksa"
        assert question["ksa_refs"]
        assert all(ref in factors[code] for ref in question["ksa_refs"])


def test_ncs_code_template_fallback_survives_missing_ksa_mcp(monkeypatch):
    monkeypatch.setenv("NCS_ALLOW_TEMPLATE_FALLBACK", "true")
    monkeypatch.setenv("NCS_AI_TOPUP_ATTEMPTS", "0")
    monkeypatch.delenv("NCS_MCP_URL", raising=False)
    monkeypatch.setattr(jd_strategy, "_generate_questions_with_openai_from_ncs", lambda **kwargs: [])
    monkeypatch.setattr(
        jd_strategy,
        "fetch_ncs_ksa_by_units",
        lambda **kwargs: (_ for _ in ()).throw(jd_strategy.NcsMcpError("NCS_MCP_URL is required")),
    )

    result = jd_strategy.generate_interview_questions_by_ncs_code(
        ncs_code="U1",
        competency_name="문서작성",
        target_count=3,
        include_followups=True,
    )

    assert result["template_fallback_used"] is True
    assert len(result["main_questions"]) == 3
    assert all(row["question_focus"] for row in result["main_questions"])
    assert all(row["question_focus_source"] == "synthetic_template" for row in result["main_questions"])
    assert all(row["ksa_refs"] == [] for row in result["main_questions"])
    assert all(row["question_repeat_signature"] for row in result["main_questions"])


def test_ncs_code_main_questions_attach_repeat_metadata(monkeypatch):
    focus = "문서 요구사항 파악"
    monkeypatch.setenv("NCS_AI_TOPUP_ATTEMPTS", "0")
    monkeypatch.setenv("NCS_ALLOW_TEMPLATE_FALLBACK", "false")
    monkeypatch.setattr(
        jd_strategy,
        "fetch_ncs_ksa_by_units",
        lambda **kwargs: [{"ncsClCd": "U1", "factorName": focus}],
    )
    monkeypatch.setattr(
        jd_strategy,
        "_generate_questions_with_openai_from_ncs",
        lambda **kwargs: [
            {
                "type": "경험면접",
                "ncsClCd": "U1",
                "question_focus": focus,
                "ksa_refs": [focus],
                "question": (
                    f"문서작성 업무에서 {focus}을 적용해 문제를 해결한 경험을 말씀해 주세요. "
                    "당시 상황, 본인 역할, 선택한 행동, 결과를 포함해 설명해 주세요."
                ),
                "follow_ups": ["판단 근거는 무엇입니까?"],
            },
            {
                "type": "경험면접",
                "ncsClCd": "U1",
                "question_focus": focus,
                "ksa_refs": [focus],
                "question": (
                    f"문서작성 업무에서 {focus}을 활용해 문제를 해결한 경험을 말씀해 주세요. "
                    "당시 상황과 본인 역할, 선택한 행동, 결과를 포함해 설명해 주세요."
                ),
                "follow_ups": ["행동 근거는 무엇입니까?"],
            },
        ],
    )

    result = jd_strategy.generate_interview_questions_by_ncs_code(
        ncs_code="U1",
        competency_name="문서작성",
        target_count=2,
        include_followups=False,
    )

    questions = result["main_questions"]

    assert [q["question_focus"] for q in questions] == [focus, focus]
    assert [q["question_intent"] for q in questions] == ["experience_behavior", "experience_behavior"]
    assert questions[0]["question_repeat_signature"] == questions[1]["question_repeat_signature"]
    assert questions[0]["question_repeat_signature"].startswith("experience_behavior|경험면접|focus:")
    assert [q["question_repeat_duplicate"] for q in questions] == [False, True]


def test_personalized_questions_preserve_generation_metadata(monkeypatch):
    monkeypatch.setattr(
        jd_strategy,
        "fetch_ncs_ksa_by_units",
        lambda **kwargs: [
            {
                "ncsClCd": "0202030201_25v3",
                "compeUnitName": "문서작성",
                "factorName": "문서 요구사항 파악",
            }
        ],
    )
    monkeypatch.setattr(
        jd_strategy,
        "_generate_questions_with_openai_from_ncs",
        lambda **kwargs: [
            {
                "type": "경험면접",
                "competency": "문서작성",
                "ncsClCd": "0202030201_25v3",
                "question": "문서 요구사항 파악 경험을 말씀해 주세요.",
                "question_focus": "문서 요구사항 파악",
                "ksa_refs": ["문서 요구사항 파악"],
                "follow_ups": ["근거는 무엇입니까?"],
                "evaluation_points": ["상황 파악", "행동 근거"],
            }
        ],
    )

    result = jd_strategy.generate_personalized_interview_questions(
        ncs_code="0202030201_25v3",
        competency_name="문서작성",
        job_posting="사무행정 담당",
        user_profile="문서관리 경험",
        target_count=1,
    )

    question = result["questions"][0]

    assert question["question_focus"] == "문서 요구사항 파악"
    assert question["question_focus_source"] == "official_ksa"
    assert question["ksa_refs"] == ["문서 요구사항 파악"]
    assert question["follow_ups"] == ["근거는 무엇입니까?"]
    assert question["evaluation_points"] == ["상황 파악", "행동 근거"]
    assert question["ncsClCd"] == "0202030201_25v3"
    assert question["question_intent"]
    assert question["question_repeat_signature"]
    assert question["question_repeat_duplicate"] is False
    assert result["ncs_ksa_available"] is True
