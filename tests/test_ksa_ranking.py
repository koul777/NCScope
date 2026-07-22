from __future__ import annotations

from app.services.jd_strategy import rank_ksa_factors_by_query


def test_rank_ksa_prefers_query_relevant_factor() -> None:
    rows = [
        {"ncsClCd": "0202010101", "compeUnitName": "문서관리", "factorName": "문서보관 절차 수립", "factorSource": "xlsx-qs"},
        {"ncsClCd": "0202010101", "compeUnitName": "문서관리", "factorName": "차량 운행일지 관리", "factorSource": "xlsx-qs"},
        {"ncsClCd": "0202010102", "compeUnitName": "자산관리", "factorName": "자산 실사 계획 수립", "factorSource": "xlsx-qs"},
    ]
    ranked = rank_ksa_factors_by_query(
        ksa_rows=rows,
        query_text="담당업무: 문서보관 절차 운영",
        unit_scores={"0202010101": 1.0, "0202010102": 0.6},
        target_count=2,
        per_unit_limit=2,
    )

    assert len(ranked) == 2
    assert ranked[0]["factorName"] == "문서보관 절차 수립"
    assert float(ranked[0].get("finalScore", 0.0)) >= float(ranked[1].get("finalScore", 0.0))


def test_rank_ksa_respects_per_unit_limit() -> None:
    rows = [
        {"ncsClCd": "A", "compeUnitName": "총무", "factorName": "문서관리", "factorSource": "xlsx-qs"},
        {"ncsClCd": "A", "compeUnitName": "총무", "factorName": "문서작성", "factorSource": "xlsx-qs"},
        {"ncsClCd": "A", "compeUnitName": "총무", "factorName": "문서보안", "factorSource": "xlsx-qs"},
        {"ncsClCd": "B", "compeUnitName": "회계", "factorName": "예산편성", "factorSource": "xlsx-qs"},
        {"ncsClCd": "B", "compeUnitName": "회계", "factorName": "결산처리", "factorSource": "xlsx-qs"},
    ]
    ranked = rank_ksa_factors_by_query(
        ksa_rows=rows,
        query_text="담당업무: 문서관리와 예산업무",
        unit_scores={"A": 1.0, "B": 0.8},
        target_count=10,
        per_unit_limit=2,
    )

    by_code: dict[str, int] = {}
    for row in ranked:
        code = str(row.get("ncsClCd", ""))
        by_code[code] = by_code.get(code, 0) + 1

    assert by_code.get("A", 0) <= 2
    assert by_code.get("B", 0) <= 2
