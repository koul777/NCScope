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
