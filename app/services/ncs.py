"""
NCS (National Competency Standards) 데이터 및 유틸리티
"""

from typing import Any

# NCS 샘플 데이터 (카테고리별)
NCS_SAMPLE: dict[str, list[dict[str, Any]]] = {
    "R6000_OFFICE": [
        {
            "ncsClCd": "020101",
            "compeUnitName": "사무행정",
            "compeUnitLevel": "4",
            "ncsSclasCdnm": "사무행정",
            "keywords": ["사무", "행정", "문서", "기록"],
        },
        {
            "ncsClCd": "020102",
            "compeUnitName": "회계",
            "compeUnitLevel": "4",
            "ncsSclasCdnm": "회계",
            "keywords": ["회계", "재무", "세무", "결산"],
        },
        {
            "ncsClCd": "020201",
            "compeUnitName": "총무",
            "compeUnitLevel": "5",
            "ncsSclasCdnm": "총무",
            "keywords": ["총무", "행사", "시설", "차량"],
        },
        {
            "ncsClCd": "020301",
            "compeUnitName": "인사",
            "compeUnitLevel": "5",
            "ncsSclasCdnm": "인사",
            "keywords": ["인사", "채용", "인력", "급여"],
        },
    ],
    "R6000_MANAGEMENT": [
        {
            "ncsClCd": "020401",
            "compeUnitName": "경영기획",
            "compeUnitLevel": "6",
            "ncsSclasCdnm": "경영기획",
            "keywords": ["경영", "기획", "전략", "예산", "관리"],
        },
        {
            "ncsClCd": "020402",
            "compeUnitName": "예산관리",
            "compeUnitLevel": "5",
            "ncsSclasCdnm": "경영기획",
            "keywords": ["예산", "관리", "재무", "비용"],
        },
    ],
}


def map_ncs(
    category: str = "",
    text: str = "",
    top_k: int = 5,
    # 구 인터페이스 호환성 유지
    keyword: str = "",
    name: str = "",
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """
    카테고리 내에서 텍스트 키워드로 NCS 능력단위 검색.

    Args:
        category: NCS 카테고리 코드 (e.g., "R6000_OFFICE", "R6000_MANAGEMENT")
        text: 검색 텍스트 (빈 문자열이면 전체 반환)
        top_k: 최대 반환 개수

    Returns:
        List of dicts with: ncsClCd, compeUnitName, compeUnitLevel, score, reason
    """
    # 구 인터페이스 호환
    if not text and (keyword or name):
        text = keyword or name
    if not top_k and max_results:
        top_k = max_results

    # 카테고리 선택 (없으면 전체)
    if category in NCS_SAMPLE:
        candidates = NCS_SAMPLE[category]
    else:
        # 알 수 없는 카테고리면 전체 샘플에서 검색
        candidates = [item for group in NCS_SAMPLE.values() for item in group]

    results: list[dict[str, Any]] = []

    for item in candidates:
        unit_name = str(item.get("compeUnitName", ""))
        sclass_name = str(item.get("ncsSclasCdnm", ""))
        unit_keywords = item.get("keywords", [])

        score = 0.2  # baseline

        if text:
            # 정확히 이름이 포함되면 높은 점수
            if unit_name in text or sclass_name in text:
                score = 0.9
            else:
                # 키워드 매칭
                matches = sum(1 for kw in unit_keywords if kw in text)
                if matches > 0:
                    score = min(0.5 + matches * 0.15, 0.85)

            reason_parts = []
            if unit_name in text:
                reason_parts.append(f"'{unit_name}' 명칭 일치")
            matched_kws = [kw for kw in unit_keywords if kw in text]
            if matched_kws:
                reason_parts.append(f"키워드 매칭: {', '.join(matched_kws)}")
            reason = "; ".join(reason_parts) if reason_parts else "기본 매칭"
        else:
            # 텍스트 없으면 baseline 점수로 전체 반환
            reason = "기본 카테고리 항목"

        results.append({
            "ncsClCd": item["ncsClCd"],
            "compeUnitName": unit_name,
            "compeUnitLevel": item.get("compeUnitLevel", "4"),
            "ncsSclasCdnm": sclass_name,
            "score": score,
            "reason": reason,
        })

    # 점수 내림차순 정렬
    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:top_k]
