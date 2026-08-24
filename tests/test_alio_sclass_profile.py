from __future__ import annotations

from app.services.alio_sclass_profile import build_sclass_profile, suggest_sclass_from_profile


def _examples():
    return [
        {
            "ncs_code_no": "020101",
            "sclass_name": "경영기획",
            "text": "사업환경 분석 중장기 전략 경영계획 사업계획 수립 성과지표 예산 연계",
        },
        {
            "ncs_code_no": "020101",
            "sclass_name": "경영기획",
            "text": "경영계획 수립 사업환경 분석 전략과제 성과지표 관리 경영실적 분석",
        },
        {
            "ncs_code_no": "020203",
            "sclass_name": "일반사무",
            "text": "문서 작성 문서 관리 자료 정리 회의 운영 행정 지원 사무자동화",
        },
        {
            "ncs_code_no": "020203",
            "sclass_name": "일반사무",
            "text": "행정 지원 문서 작성 자료 관리 회의 지원 사무자동화 문서 보관",
        },
    ]


def test_profile_is_built_only_from_repeated_verified_classes():
    profile = build_sclass_profile(_examples(), min_class_documents=2, min_token_documents=2)

    assert profile["review_only"] is True
    assert profile["training_documents"] == 4
    assert {row["sclass_name"] for row in profile["classes"]} == {"경영기획", "일반사무"}


def test_profile_suggestions_are_explainable_and_review_only():
    profile = build_sclass_profile(_examples(), min_class_documents=2, min_token_documents=2)
    suggestions = suggest_sclass_from_profile(
        "중장기 경영계획을 수립하고 사업환경 분석 결과로 전략과제와 성과지표를 관리한다.",
        profile=profile,
        max_items=3,
    )

    assert suggestions
    assert suggestions[0]["sclass_name"] == "경영기획"
    assert suggestions[0]["review_required"] is True
    assert suggestions[0]["source"] == "alio_corpus_profile"
    assert "경영계획" in suggestions[0]["matched_tokens"]


def test_profile_does_not_guess_from_one_generic_token():
    profile = build_sclass_profile(_examples(), min_class_documents=2, min_token_documents=2)
    assert suggest_sclass_from_profile("문서", profile=profile) == []
