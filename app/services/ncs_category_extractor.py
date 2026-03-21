"""
ncs_category_extractor.py
NCS 소분류 추출기 — 완전 결정적(deterministic), 외부 API 호출 없음

사용법:
    from app.services.ncs_category_extractor import extract_small_category
    result = extract_small_category(text_lines, tables, ncs_small_categories)
    print(result["best"]["label"])   # 예: "총무"
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    "window_chars": 30,               # anchor 같은 줄 오른쪽 탐색 창 (글자 수)
    "window_cells": 2,                # 표에서 anchor 옆/아래 탐색 칸 수
    "next_line_window": 2,            # anchor 다음 줄 탐색 수
    "top_k": 5,                       # 상위 후보 수
    "min_score_threshold": 3,         # best label 최소 점수
    "reverse_near_anchor_window": 5,  # reverse dict: anchor ±N 라인
    "reverse_near_ncs_window": 3,     # reverse dict: NCS 구간 키워드 ±N 라인
    "max_reverse_score_cap": 6,       # reverse dict: 동일 label 누적 점수 상한
    "max_candidate_length": 30,       # 후보 문자열 이 길이 초과 시 감점
}


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# anchor: "소분류", "NCS 소분류", "NCS소분류", "소 분 류" (OCR 노이즈 흡수)
_ANCHOR_RE = re.compile(r"(NCS\s*)?소\s*분\s*류")

# NCS 분류 구간 식별자 (reverse dict 가산점)
_NCS_SECTION_RE = re.compile(r"(분류체계|NCS분류체계|NCS\s*분류|직무분류)")

# anchor 뒤 구분자 ( : - | 공백)
_AFTER_ANCHOR_SEP = re.compile(r"^[\s\:\-\|]+")

# 후보 앞의 노이즈: 숫자/점/콜론/중점/불릿/특수문자
_NOISE_PREFIX = re.compile(r"^[\d\s\.\:\-·•▶◆▷○●★☆※>\|/\\]+")
_NOISE_SUFFIX = re.compile(r"[\s\.\:\-·•▶◆▷○●★☆※>\|/\\]+$")

# 헤더성 단어 집합 (후보에서 제외)
_HEADER_WORDS: frozenset[str] = frozenset({
    "대분류", "중분류", "소분류", "세분류", "분류체계",
    "NCS분류", "NCS", "직무분류", "능력단위", "직무수행",
    "직무명", "직무", "필요지식", "필요기술", "직종",
})


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

# 유니코드 중간점/구분자 → U+00B7 (MIDDLE DOT) 통합 정규화
# PDF마다 U+2027(‧), U+22C5(⋅), U+2219(∙), U+FF65(･) 등 혼용
_DOT_CHARS = re.compile(r"[\u2027\u22c5\u2219\uff65\u00b7\u2024]")


def _norm(text: str) -> str:
    """공백/탭 정리 + 연속 공백 → 단일 공백."""
    return re.sub(r"\s+", " ", text.replace("\t", " ")).strip()


def _unify_dots(text: str) -> str:
    """PDF/CSV 간 유니코드 구분자 차이 흡수 (모두 U+00B7로 통일)."""
    return _DOT_CHARS.sub("\u00b7", text)


def _clean_candidate(raw: str) -> str:
    """후보 문자열 노이즈 제거."""
    text = _norm(raw)
    text = _unify_dots(text)
    text = _NOISE_PREFIX.sub("", text)
    text = _NOISE_SUFFIX.sub("", text)
    return text.strip()


def _is_anchor(text: str) -> bool:
    """text에 anchor keyword(소분류 변형)가 포함되면 True."""
    return bool(_ANCHOR_RE.search(_norm(text)))


def _is_header(text: str) -> bool:
    """헤더성 단어 또는 빈 문자열이면 True."""
    c = _clean_candidate(text)
    return not c or c in _HEADER_WORDS


def _extract_after_anchor(line: str, window_chars: int) -> Optional[str]:
    """
    "소분류 : 총무" / "소분류:총무" / "소분류 - 총무" / "소분류 총무" 등에서
    anchor 이후 window_chars 내 값 후보를 반환.
    """
    normalized = _norm(line)
    m = _ANCHOR_RE.search(normalized)
    if not m:
        return None
    rest = normalized[m.end():]
    rest = _AFTER_ANCHOR_SEP.sub("", rest)
    if not rest:
        return None
    candidate = rest[:window_chars].strip()
    return candidate or None


def _canonicalize(
    raw: str,
    ncs_small_categories: list[str],
) -> tuple[str | None, int]:
    """
    raw → ncs_small_categories 중 가장 잘 맞는 항목 탐색.

    반환: (canonical_label, match_bonus)
        match_bonus: 5=정확히 일치, 3=부분문자열/포함, 0=매칭 없음
    """
    cleaned = _clean_candidate(raw)
    if not cleaned:
        return None, 0

    # dot 통일된 사전으로 매칭 (PDF의 U+2027 vs CSV의 U+00B7 차이 흡수)
    unified = _unify_dots(cleaned)
    unified_map = {_unify_dots(s): s for s in ncs_small_categories}

    # 1. 정확 일치 (dot 통일 후)
    if unified in unified_map:
        return unified_map[unified], 5

    # 2. 대소문자 무관 정확 일치
    lower_unified_map = {k.lower(): v for k, v in unified_map.items()}
    if unified.lower() in lower_unified_map:
        return lower_unified_map[unified.lower()], 5

    # 3. ncs label이 unified에 포함 (예: "총무 업무" → "총무")
    best_label: str | None = None
    best_len = 0
    for u_label, orig_label in unified_map.items():
        if u_label in unified and len(u_label) > best_len:
            best_label = orig_label
            best_len = len(u_label)
    if best_label:
        return best_label, 3

    # 4. unified가 ncs label에 포함 (예: "총무" ⊂ "총무·인사")
    for u_label, orig_label in unified_map.items():
        if unified in u_label and len(unified) >= 2 and len(unified) > best_len:
            best_label = orig_label
            best_len = len(unified)
    if best_label:
        return best_label, 3

    # 매칭 없음 → cleaned 그대로 반환 (점수 0)
    return cleaned, 0


# ---------------------------------------------------------------------------
# Strategy 1 + 2-A: Anchor from text_lines
# ---------------------------------------------------------------------------

def _anchor_from_text_lines(
    text_lines: list[str],
    ncs_small_categories: list[str],
    config: dict,
) -> tuple[list[dict], list[int]]:
    """
    text_lines에서 anchor를 찾아 후보 추출.

    반환:
        candidates  — [{"label", "score", "evidence": {...}}, ...]
        anchor_lines — anchor가 발견된 줄 인덱스 리스트
    """
    candidates: list[dict] = []
    anchor_lines: list[int] = []
    window_chars = config["window_chars"]
    next_line_window = config["next_line_window"]

    for i, raw_line in enumerate(text_lines):
        line = _norm(raw_line)
        if not _is_anchor(line):
            continue

        anchor_lines.append(i)
        found_same_line = False

        # A. 같은 줄 추출 (anchor_same_line)
        after = _extract_after_anchor(line, window_chars)
        if after and not _is_header(after):
            label, bonus = _canonicalize(after, ncs_small_categories)
            if label:
                candidates.append({
                    "label": label,
                    "score": 1 + bonus,
                    "evidence": {
                        "method": "anchor_same_line",
                        "where": i,
                        "snippet": line[:80],
                    },
                })
                found_same_line = True

        # C. 다음 줄 추출 (anchor_next_line) — 같은 줄에서 못 찾았을 때
        if not found_same_line:
            for j in range(1, next_line_window + 1):
                if i + j >= len(text_lines):
                    break
                next_raw = _norm(text_lines[i + j])
                if not next_raw:
                    continue
                if _is_anchor(next_raw):  # 다른 anchor 줄이면 중단
                    break
                if _is_header(next_raw):
                    continue
                label, bonus = _canonicalize(next_raw, ncs_small_categories)
                if label:
                    candidates.append({
                        "label": label,
                        "score": 1 + bonus,
                        "evidence": {
                            "method": "anchor_next_line",
                            "where": i + j,
                            "snippet": next_raw[:80],
                        },
                    })
                    break  # 첫 유효 줄만

    return candidates, anchor_lines


# ---------------------------------------------------------------------------
# Strategy 2-B/C: Anchor from tables
# ---------------------------------------------------------------------------

def _anchor_from_tables(
    tables: list[list[list[str]]],
    ncs_small_categories: list[str],
    config: dict,
) -> list[dict]:
    """
    표(tables)에서 anchor cell을 찾아 오른쪽 / 아래 후보 추출.
    """
    candidates: list[dict] = []
    window_cells = config["window_cells"]
    max_len = config["max_candidate_length"]

    for t_idx, table in enumerate(tables or []):
        for r_idx, row in enumerate(table):
            for c_idx, cell in enumerate(row):
                if not _is_anchor(_norm(cell)):
                    continue

                # B-1. 오른쪽 탐색 (anchor_table_right)
                for dc in range(1, window_cells + 1):
                    if c_idx + dc >= len(row):
                        break
                    rval = _norm(row[c_idx + dc])
                    if not rval or _is_header(rval):
                        continue
                    penalty = -1 if len(rval) > max_len else 0
                    label, bonus = _canonicalize(rval, ncs_small_categories)
                    if label:
                        candidates.append({
                            "label": label,
                            "score": 1 + bonus + penalty,
                            "evidence": {
                                "method": "anchor_table_right",
                                "where": f"t{t_idx}r{r_idx}c{c_idx + dc}",
                                "snippet": rval[:80],
                            },
                        })
                    break  # 첫 유효 셀만

                # B-2. 아래 탐색 (anchor_table_down)
                for dr in range(1, window_cells + 1):
                    if r_idx + dr >= len(table):
                        break
                    drow = table[r_idx + dr]
                    if c_idx >= len(drow):
                        break
                    dval = _norm(drow[c_idx])
                    if not dval or _is_header(dval):
                        continue
                    penalty = -1 if len(dval) > max_len else 0
                    label, bonus = _canonicalize(dval, ncs_small_categories)
                    if label:
                        candidates.append({
                            "label": label,
                            "score": 1 + bonus + penalty,
                            "evidence": {
                                "method": "anchor_table_down",
                                "where": f"t{t_idx}r{r_idx + dr}c{c_idx}",
                                "snippet": dval[:80],
                            },
                        })
                    break  # 첫 유효 행만

    return candidates


# ---------------------------------------------------------------------------
# Strategy 3: Reverse Dictionary
# ---------------------------------------------------------------------------

def _reverse_dict_scan(
    text_lines: list[str],
    ncs_small_categories: list[str],
    anchor_line_indices: list[int],
    config: dict,
) -> list[dict]:
    """
    전체 text_lines에서 ncs_small_categories 항목을 역방향으로 탐색.
    anchor 근처나 NCS 분류 구간에서 발견된 label에 가산점.
    """
    near_anchor_w = config["reverse_near_anchor_window"]
    near_ncs_w = config["reverse_near_ncs_window"]
    max_cap = config["max_reverse_score_cap"]

    # NCS 분류 구간 라인 인덱스 사전 계산
    ncs_section_set: set[int] = set()
    for i, raw_line in enumerate(text_lines):
        if _NCS_SECTION_RE.search(_norm(raw_line)):
            for off in range(-near_ncs_w, near_ncs_w + 1):
                idx = i + off
                if 0 <= idx < len(text_lines):
                    ncs_section_set.add(idx)

    # anchor 근처 라인 셋
    anchor_near_set: set[int] = set()
    for ai in anchor_line_indices:
        for off in range(-near_anchor_w, near_anchor_w + 1):
            idx = ai + off
            if 0 <= idx < len(text_lines):
                anchor_near_set.add(idx)

    # label별 누적 점수 + evidence
    label_scores: dict[str, int] = {}
    label_evidence: dict[str, list[dict]] = {}

    for i, raw_line in enumerate(text_lines):
        line = _unify_dots(_norm(raw_line))
        for label in ncs_small_categories:
            u_label = _unify_dots(label)
            if u_label not in line:
                continue
            if i in anchor_near_set:
                score = 3
            elif i in ncs_section_set:
                score = 2
            else:
                score = 1
            prev = label_scores.get(label, 0)
            label_scores[label] = min(prev + score, max_cap)
            if label not in label_evidence:
                label_evidence[label] = []
            if len(label_evidence[label]) < 3:
                label_evidence[label].append({
                    "method": "reverse_dict",
                    "where": i,
                    "snippet": line[:80],
                })

    return [
        {
            "label": label,
            "score": score,
            "evidence": label_evidence.get(label, []),
        }
        for label, score in label_scores.items()
    ]


# ---------------------------------------------------------------------------
# Merge & finalize
# ---------------------------------------------------------------------------

def _merge_candidates(
    anchor_candidates: list[dict],
    reverse_candidates: list[dict],
) -> list[dict]:
    """
    Anchor + Reverse 결과를 label 단위로 통합.
    양쪽 모두 등장한 label에 시너지 보너스 +2.
    """
    merged: dict[str, dict] = {}

    def _add(cand: dict) -> None:
        lbl = cand.get("label")
        if not lbl:
            return
        if lbl not in merged:
            merged[lbl] = {"label": lbl, "score": 0, "evidence": []}
        merged[lbl]["score"] += cand["score"]
        ev = cand.get("evidence")
        if isinstance(ev, list):
            merged[lbl]["evidence"].extend(ev)
        elif isinstance(ev, dict):
            merged[lbl]["evidence"].append(ev)

    anchor_labels: set[str] = set()
    for c in anchor_candidates:
        _add(c)
        if c.get("label"):
            anchor_labels.add(c["label"])

    reverse_labels: set[str] = set()
    for c in reverse_candidates:
        _add(c)
        if c.get("label"):
            reverse_labels.add(c["label"])

    # 시너지 보너스
    for lbl in anchor_labels & reverse_labels:
        merged[lbl]["score"] += 2

    return list(merged.values())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_small_category(
    text_lines: list[str],
    tables: Optional[list[list[list[str]]]] = None,
    ncs_small_categories: Optional[list[str]] = None,
    config: Optional[dict] = None,
) -> dict:
    """
    NCS 소분류 추출기.

    Args:
        text_lines            : PDF 추출 텍스트, 줄 단위 리스트
        tables                : 표 추출 결과 [table][row][col] (없으면 None)
        ncs_small_categories  : 표준 소분류명 리스트
        config                : 설정값 overrides (없으면 DEFAULT_CONFIG 사용)

    Returns:
        {
          "best":  {"label": str|None, "score": float, "evidence": [...]},
          "topk":  [{"label": str, "score": float, "evidence": [...]}, ...],
          "debug": {
              "anchor_hits": int,
              "reverse_hits": int,
              "anchor_line_indices": [int, ...],
              "reason": str
          }
        }
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    cats: list[str] = ncs_small_categories or []
    tbls: list[list[list[str]]] = tables or []

    # ── 3가지 전략 병렬 실행 ──────────────────────────────────────────────
    anchor_text_cands, anchor_lines = _anchor_from_text_lines(text_lines, cats, cfg)
    anchor_table_cands = _anchor_from_tables(tbls, cats, cfg)
    anchor_candidates = anchor_text_cands + anchor_table_cands

    reverse_candidates = _reverse_dict_scan(text_lines, cats, anchor_lines, cfg)

    # ── 결과 병합 및 정렬 ─────────────────────────────────────────────────
    merged = _merge_candidates(anchor_candidates, reverse_candidates)
    merged.sort(key=lambda x: x["score"], reverse=True)

    top_k = cfg["top_k"]
    topk = merged[:top_k]

    # ── best 결정 ─────────────────────────────────────────────────────────
    threshold = cfg["min_score_threshold"]
    if topk and topk[0]["score"] >= threshold:
        best = topk[0]
        reason = "ok"
    else:
        best = {
            "label": None,
            "score": topk[0]["score"] if topk else 0,
            "evidence": topk[0]["evidence"] if topk else [],
        }
        reason = (
            f"best score {topk[0]['score'] if topk else 0} "
            f"< threshold {threshold}"
        )

    return {
        "best": best,
        "topk": topk,
        "debug": {
            "anchor_hits": len(anchor_candidates),
            "reverse_hits": len(reverse_candidates),
            "anchor_line_indices": anchor_lines,
            "reason": reason,
        },
    }


# ---------------------------------------------------------------------------
# Built-in unit tests (python -m pytest app/services/ncs_category_extractor.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _CATS = ["총무", "인사·조직", "경영기획", "사무행정", "재무", "마케팅", "홍보·광고"]

    def _best(result: dict) -> str | None:
        return result["best"]["label"]

    # ── Test 1: same-line ──────────────────────────────────────────────────
    lines1 = ["직무기술서", "소분류 : 총무", "능력단위"]
    r1 = extract_small_category(lines1, None, _CATS)
    assert _best(r1) == "총무", f"T1 fail: {r1['best']}"
    assert r1["debug"]["anchor_line_indices"] == [1]
    print("T1 PASS  (same-line):", _best(r1))

    # ── Test 2: next-line ──────────────────────────────────────────────────
    lines2 = ["직무기술서", "소분류", "총무", "능력단위"]
    r2 = extract_small_category(lines2, None, _CATS)
    assert _best(r2) == "총무", f"T2 fail: {r2['best']}"
    print("T2 PASS  (next-line):", _best(r2))

    # ── Test 3: table right — 헤더 행 + 값 행 ─────────────────────────────
    tables3 = [
        [
            ["대분류", "중분류", "소분류"],   # 헤더 행
            ["02",    "01",    "총무"],       # 값 행
        ]
    ]
    # 표만 제공, text_lines는 비어 있음
    r3 = extract_small_category([], tables3, _CATS)
    # "소분류" 셀 오른쪽에 값이 없고, 아래 행(r=1, c=2)은 "총무"
    assert _best(r3) == "총무", f"T3 fail: {r3['best']}"
    print("T3 PASS  (table right / down):", _best(r3))

    # ── Test 4: table down ─────────────────────────────────────────────────
    tables4 = [
        [
            ["소분류"],
            ["총무"],
        ]
    ]
    r4 = extract_small_category([], tables4, _CATS)
    assert _best(r4) == "총무", f"T4 fail: {r4['best']}"
    print("T4 PASS  (table down):", _best(r4))

    # ── Test 5: anchor 없음 + reverse만 (NCS 분류 구간, 2회 등장 → 점수 누적) ──
    # NCS 구간 1회 = +2, 2회 = +4 (≥ threshold 3) → best 확정
    lines5 = [
        "NCS 분류체계",
        "경영회계사무 > 총무인사 > 총무",   # NCS 구간 근처 +2
        "해당 직무: 총무 담당",               # NCS 구간 근처 +2  → 누계 4
        "직무수행 내용",
    ]
    r5 = extract_small_category(lines5, None, _CATS)
    assert _best(r5) == "총무", f"T5 fail: {r5['best']}"
    print("T5 PASS  (no anchor, reverse only):", _best(r5))

    # ── Test 6: 복수 소분류 → top_k ───────────────────────────────────────
    lines6 = [
        "NCS 분류체계: 경영기획 / 총무 / 인사·조직 / 사무행정",
        "소분류 : 총무",
    ]
    r6 = extract_small_category(lines6, None, _CATS)
    topk_labels = [c["label"] for c in r6["topk"]]
    assert "총무" in topk_labels, f"T6 fail: topk={topk_labels}"
    assert len(topk_labels) > 1, f"T6: 복수 후보 없음: {topk_labels}"
    print("T6 PASS  (multiple candidates):", topk_labels)

    # ── Test 7: NCS 소분류 변형 anchor ────────────────────────────────────
    lines7 = ["NCS소분류 : 사무행정"]
    r7 = extract_small_category(lines7, None, _CATS)
    assert _best(r7) == "사무행정", f"T7 fail: {r7['best']}"
    print("T7 PASS  (NCS소분류 variant):", _best(r7))

    # ── Test 8: 노이즈 prefix 제거 ────────────────────────────────────────
    lines8 = ["소분류: 01. 총무"]
    r8 = extract_small_category(lines8, None, _CATS)
    assert _best(r8) == "총무", f"T8 fail: {r8['best']}"
    print("T8 PASS  (noise prefix strip):", _best(r8))

    # ── Test 9: threshold 미달 → best.label = None ─────────────────────
    lines9 = ["완전히 다른 내용의 문서입니다."]
    r9 = extract_small_category(lines9, None, _CATS, config={"min_score_threshold": 99})
    assert _best(r9) is None, f"T9 fail: {r9['best']}"
    print("T9 PASS  (below threshold → None):", r9["debug"]["reason"])

    # ── Test 10: 표 복합 (anchor cell이 값 행과 교차) ─────────────────────
    tables10 = [
        [
            ["항목",   "내용"],
            ["소분류", "재무"],
        ]
    ]
    r10 = extract_small_category([], tables10, _CATS)
    assert _best(r10) == "재무", f"T10 fail: {r10['best']}"
    print("T10 PASS (table right, anchor in body row):", _best(r10))

    print("\nOK - 모든 테스트 통과")
