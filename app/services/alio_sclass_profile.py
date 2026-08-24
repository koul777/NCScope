"""High-precision lexical suggestions learned from verified ALIO documents.

The profile is deliberately review-only.  Training examples are accepted only
when a JOB-ALIO job description contains exactly one explicit label that maps
exactly to the local official NCS catalogue.  Predictions never become an
official NCS decision without the existing human confirmation gate.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROFILE_VERSION = 1
DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / ".local" / "alio_corpus" / "sclass_profiles.json"

_STOPWORDS = {
    "그리고",
    "그러나",
    "대한",
    "관련",
    "경우",
    "기타",
    "기관",
    "업무",
    "직무",
    "수행",
    "필요",
    "활용",
    "지원",
    "채용",
    "관리",
    "능력",
    "기술",
    "지식",
    "태도",
    "사항",
    "내용",
    "이상",
    "이하",
    "또는",
    "통해",
    "위한",
    "등을",
    "에서",
    "으로",
    "하는",
    "있음",
    "없음",
    "ncs",
}


def tokenize_profile_text(value: str) -> list[str]:
    """Return stable Korean/ASCII profile tokens without document boilerplate."""

    text = re.sub(r"\b\d{1,4}(?:[./:-]\d{1,4})+\b", " ", str(value or "").casefold())
    tokens = re.findall(r"[가-힣a-z][가-힣a-z0-9+.#-]{1,29}", text)
    output: list[str] = []
    suffixes = (
        "으로부터",
        "에서는",
        "으로써",
        "으로서",
        "에게서",
        "까지는",
        "부터는",
        "하고",
        "하며",
        "한다",
        "에서",
        "으로",
        "에게",
        "처럼",
        "보다",
        "까지",
        "부터",
        "이나",
        "라도",
        "에는",
        "와의",
        "과의",
        "을",
        "를",
        "이",
        "가",
        "은",
        "는",
        "의",
        "에",
        "와",
        "과",
        "도",
        "만",
    )
    for token in tokens:
        cleaned = token.strip("+.#-")
        for suffix in suffixes:
            if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 2:
                cleaned = cleaned[: -len(suffix)]
                break
        if len(cleaned) < 2 or cleaned in _STOPWORDS or cleaned.isdigit():
            continue
        output.append(cleaned)
    return output


def build_sclass_profile(
    examples: Iterable[dict[str, Any]],
    *,
    min_class_documents: int = 2,
    min_token_documents: int = 2,
    max_tokens_per_class: int = 120,
) -> dict[str, Any]:
    """Build a compact TF/IDF-like profile from verified single-label rows."""

    class_documents: Counter[str] = Counter()
    class_names: dict[str, str] = {}
    class_token_df: dict[str, Counter[str]] = defaultdict(Counter)
    global_token_df: Counter[str] = Counter()
    total_documents = 0

    for example in examples:
        if not isinstance(example, dict):
            continue
        code = str(example.get("ncs_code_no") or "").strip()
        name = str(example.get("sclass_name") or "").strip()
        text = str(example.get("text") or "").strip()
        if not (re.fullmatch(r"\d{6}", code) and name and text):
            continue
        unique_tokens = set(tokenize_profile_text(text))
        if len(unique_tokens) < 3:
            continue
        total_documents += 1
        class_documents[code] += 1
        class_names[code] = name
        for token in unique_tokens:
            class_token_df[code][token] += 1
            global_token_df[token] += 1

    classes: list[dict[str, Any]] = []
    for code, document_count in class_documents.most_common():
        if document_count < max(1, int(min_class_documents)):
            continue
        weighted: list[tuple[float, str, int]] = []
        for token, class_df in class_token_df[code].items():
            if class_df < max(1, int(min_token_documents)):
                continue
            global_df = max(class_df, global_token_df[token])
            idf = math.log((total_documents + 1.0) / (global_df + 1.0)) + 1.0
            prevalence = class_df / float(document_count)
            # Drop tokens appearing in nearly every class/document; they add
            # confidence without adding discrimination.
            global_ratio = global_df / float(max(1, total_documents))
            if global_ratio > 0.72:
                continue
            weight = idf * prevalence * (1.0 - 0.45 * global_ratio)
            weighted.append((weight, token, class_df))
        weighted.sort(key=lambda item: (item[0], item[2], len(item[1])), reverse=True)
        token_rows = [
            {"token": token, "weight": round(weight, 6), "documents": df}
            for weight, token, df in weighted[: max(10, int(max_tokens_per_class))]
        ]
        if token_rows:
            classes.append(
                {
                    "ncs_code_no": code,
                    "sclass_name": class_names[code],
                    "documents": document_count,
                    "tokens": token_rows,
                }
            )

    return {
        "version": PROFILE_VERSION,
        "source": "job_alio_explicit_single_sclass_documents",
        "review_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "training_documents": total_documents,
        "class_count": len(classes),
        "classes": classes,
    }


def configured_profile_path() -> Path:
    configured = os.getenv("ALIO_SCLASS_PROFILE_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_PROFILE_PATH


def load_sclass_profile(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else configured_profile_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != PROFILE_VERSION or not data.get("review_only"):
        return {}
    return data


def suggest_sclass_from_profile(
    text: str,
    *,
    profile: dict[str, Any] | None = None,
    profile_path: str | Path | None = None,
    max_items: int = 5,
    min_matching_tokens: int = 2,
) -> list[dict[str, Any]]:
    """Return explainable, review-only suggestions from a saved profile."""

    data = profile if isinstance(profile, dict) else load_sclass_profile(profile_path)
    if not data:
        return []
    input_tokens = set(tokenize_profile_text(text))
    if len(input_tokens) < min_matching_tokens:
        return []

    scored: list[tuple[float, dict[str, Any], list[tuple[str, float]]]] = []
    for row in data.get("classes") or []:
        if not isinstance(row, dict):
            continue
        matches: list[tuple[str, float]] = []
        for token_row in row.get("tokens") or []:
            if not isinstance(token_row, dict):
                continue
            token = str(token_row.get("token") or "")
            if token not in input_tokens:
                continue
            try:
                weight = float(token_row.get("weight") or 0.0)
            except (TypeError, ValueError):
                weight = 0.0
            if weight > 0:
                matches.append((token, weight))
        if len(matches) < min_matching_tokens:
            continue
        matches.sort(key=lambda item: item[1], reverse=True)
        raw_score = sum(weight for _, weight in matches[:12]) / math.sqrt(max(1, len(input_tokens)))
        if raw_score <= 0:
            continue
        scored.append((raw_score, row, matches))

    scored.sort(key=lambda item: (item[0], int(item[1].get("documents") or 0)), reverse=True)
    if not scored:
        return []
    top_raw = scored[0][0]
    output: list[dict[str, Any]] = []
    for raw_score, row, matches in scored[: max(1, int(max_items))]:
        relative = raw_score / top_raw if top_raw > 0 else 0.0
        confidence = min(0.89, max(0.50, 0.48 + 0.32 * relative + min(0.09, len(matches) * 0.015)))
        output.append(
            {
                "sclass_name": str(row.get("sclass_name") or "").strip(),
                "ncs_code_no": str(row.get("ncs_code_no") or "").strip(),
                "confidence": round(confidence, 4),
                "review_required": True,
                "source": "alio_corpus_profile",
                "training_documents": int(row.get("documents") or 0),
                "matched_tokens": [token for token, _ in matches[:8]],
                "evidence": "ALIO 명시 세분류 문서와의 어휘 일치: " + ", ".join(token for token, _ in matches[:8]),
            }
        )
    return [row for row in output if row["sclass_name"] and row["ncs_code_no"]]
