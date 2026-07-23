from __future__ import annotations

import argparse
import csv
import io
import os
import re
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.kordoc_parser import KordocParseError, parse_with_kordoc  # noqa: E402


NCS_HOST = "https://www.ncs.go.kr"
INTERVIEW_LIST_URL = f"{NCS_HOST}/blind/rh13/bbs_lib_list.do?libDstinCd=49&menuId=MN02020303"
VIEW_URL = f"{NCS_HOST}/blind/rh13/bbs_lib_view.do"
DOWNLOAD_URL = f"{NCS_HOST}/common/file/downloadFile.do"
SUPPORTED_DOC_SUFFIXES = {".hwp", ".hwpx", ".docx", ".pdf", ".txt"}
METHOD_NAMES = (
    "경험면접",
    "상황면접",
    "발표면접",
    "토론면접",
    "창의적 문제해결력면접",
    "인바스켓면접",
    "직무지식면접",
)


@dataclass
class OfficialSampleCollection:
    key: str
    label: str
    lib_dstin_cd: str
    menu_id: str
    list_url: str


@dataclass
class OfficialSampleEntry:
    seq: str
    title: str
    file_mstky: str
    filedetl_seq: str
    filename: str
    collection_key: str = "interview-model"
    collection_label: str = "채용모델 면접문항"
    lib_dstin_cd: str = "49"
    menu_id: str = "MN02020303"
    ncs_code_hint: str = ""
    detail_label_hint: str = ""


SAMPLE_COLLECTIONS = {
    "interview-model": OfficialSampleCollection(
        key="interview-model",
        label="채용모델 면접문항",
        lib_dstin_cd="49",
        menu_id="MN02020303",
        list_url=INTERVIEW_LIST_URL,
    ),
    "evaluation-sample": OfficialSampleCollection(
        key="evaluation-sample",
        label="전형별 평가샘플",
        lib_dstin_cd="30",
        menu_id="MN42020301",
        list_url=f"{NCS_HOST}/blind/rh13/bbs_lib_list.do?libDstinCd=30&menuId=MN42020301",
    ),
}


_METHOD_DETECTION_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("경험면접", ("경험면접", "경험 면접")),
    ("상황면접", ("상황면접", "상황 면접")),
    ("발표면접", ("발표면접", "발표 면접", "발표면접과제", "발표면접 과제")),
    ("토론면접", ("토론면접", "토론 면접", "토론면접과제", "토론면접 과제")),
    ("인바스켓면접", ("인바스켓면접", "인바스켓 면접", "인바스켓")),
    ("직무지식면접", ("직무지식면접", "직무지식 면접", "직무 지식 면접")),
    ("창의적 문제해결력면접", ("창의적 문제해결력 면접", "창의적문제해결력 면접", "창의적문제해결력면접")),
)
_UNSUPPORTED_METHOD_BOUNDARY = "__unsupported_interview_boundary__"
_UNSUPPORTED_INTERVIEW_SECTION_PATTERNS = (
    "역할연기",
    "역할 연기",
    "Business Case",
    "BusinessCase",
    "비즈니스 케이스",
    "비즈니스케이스",
)
_METHOD_SECTION_ANCHOR_WORDS = (
    "과제",
    "문항",
    "질문",
    "질문지",
    "평가",
    "평가표",
    "평가양식",
    "안내",
    "채점",
    "평정",
)


def _collection_from_key(key: str) -> OfficialSampleCollection:
    return SAMPLE_COLLECTIONS.get(str(key or "").strip()) or SAMPLE_COLLECTIONS["interview-model"]


def _collection_from_url(list_url: str) -> OfficialSampleCollection:
    raw_url = str(list_url or "").strip()
    for collection in SAMPLE_COLLECTIONS.values():
        if collection.list_url == raw_url:
            return collection
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    lib_dstin_cd = str((query.get("libDstinCd") or [""])[0] or "").strip()
    menu_id = str((query.get("menuId") or [""])[0] or "").strip()
    for collection in SAMPLE_COLLECTIONS.values():
        if collection.lib_dstin_cd == lib_dstin_cd:
            return OfficialSampleCollection(
                key=collection.key,
                label=collection.label,
                lib_dstin_cd=collection.lib_dstin_cd,
                menu_id=menu_id or collection.menu_id,
                list_url=raw_url or collection.list_url,
            )
    return SAMPLE_COLLECTIONS["interview-model"]


def _list_url_with_page_index(list_url: str, page_index: int) -> str:
    parsed = urlparse(str(list_url or ""))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if page_index <= 0:
        query.pop("pageIndex", None)
    else:
        query["pageIndex"] = [str(page_index)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


_TITLE_NCS_CODE_PATTERN = re.compile(r"(?<!\d)(\d{1,2})[-_](\d{1,2})[-_](\d{1,2})(?:[-_](\d{1,2}))?(?!\d)")


def _title_ncs_hints(title: str) -> tuple[str, str]:
    source = _clean_html_text(title)
    match = _TITLE_NCS_CODE_PATTERN.search(source)
    code_hint = ""
    if match:
        code_hint = "-".join(part.zfill(2) for part in match.groups() if part)

    label_source = re.sub(r"\([^)]*\)", " ", source)
    label_source = _TITLE_NCS_CODE_PATTERN.sub(" ", label_source)
    label_source = re.split(r"\s+면접|_면접", label_source, maxsplit=1)[0]
    label_source = re.sub(r"\s+", " ", label_source).strip(" _-.")
    return code_hint, label_source[:120]


def _curl_bytes(url: str, post_data: dict[str, str] | None = None, timeout_sec: int = 60) -> bytes:
    curl = "curl.exe" if os.name == "nt" else "curl"
    cmd = [curl, "-L", "-s", "--max-time", str(timeout_sec)]
    if post_data:
        cmd.extend(["-X", "POST", "-d", urlencode(post_data)])
    cmd.append(url)
    completed = subprocess.run(cmd, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(detail or f"curl failed with exit code {completed.returncode}")
    return completed.stdout


def _decode_html(data: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def discover_official_samples(
    limit: int,
    list_url: str = INTERVIEW_LIST_URL,
    collection: OfficialSampleCollection | None = None,
) -> list[OfficialSampleEntry]:
    collection = collection or _collection_from_url(list_url)
    row_pattern = re.compile(r"<tr>\s*<td>(?P<num>\d+)</td>(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
    entries: list[OfficialSampleEntry] = []
    seen_keys: set[tuple[str, str, str]] = set()
    limit = max(0, int(limit))
    if limit <= 0:
        return entries

    max_pages = min(50, max(1, (limit // 10) + 3))
    for page_index in range(max_pages):
        page_url = _list_url_with_page_index(list_url, page_index)
        html = _decode_html(_curl_bytes(page_url))
        page_added = 0
        for row in row_pattern.finditer(html):
            body = row.group("body")
            view = re.search(
                r"fn_view\('(?P<seq>\d+)'\).*?title=\"(?P<title>.*?)\"",
                body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            file_call = re.search(
                r"gfn_file_downloadFile\('(?P<sys>[^']+)','(?P<mst>[^']+)','(?P<detl>[^']+)'",
                body,
                flags=re.IGNORECASE,
            )
            if not view or not file_call:
                continue
            dedupe_key = (view.group("seq"), file_call.group("mst"), file_call.group("detl"))
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            title = _clean_html_text(view.group("title"))
            ncs_code_hint, detail_label_hint = _title_ncs_hints(title)
            filename = title
            entries.append(
                OfficialSampleEntry(
                    seq=view.group("seq"),
                    title=title,
                    file_mstky=file_call.group("mst"),
                    filedetl_seq=file_call.group("detl"),
                    filename=filename,
                    collection_key=collection.key,
                    collection_label=collection.label,
                    lib_dstin_cd=collection.lib_dstin_cd,
                    menu_id=collection.menu_id,
                    ncs_code_hint=ncs_code_hint,
                    detail_label_hint=detail_label_hint,
                )
            )
            page_added += 1
            if len(entries) >= limit:
                break
        if len(entries) >= limit or page_added == 0:
            break
    return entries


def fetch_sample_view_text(entry: OfficialSampleEntry) -> str:
    html = _decode_html(
        _curl_bytes(
            VIEW_URL,
            {
                "libDstinCd": entry.lib_dstin_cd,
                "menuId": entry.menu_id,
                "libSeq": entry.seq,
            },
        )
    )
    match = re.search(r"<span id=\"iframeBbsNtcSource\"[^>]*>(?P<body>.*?)</span>", html, flags=re.IGNORECASE | re.DOTALL)
    return _clean_html_text(match.group("body")) if match else ""


def _infer_download_suffix(data: bytes) -> str:
    head = bytes(data[:16])
    stripped = bytes(data[:64]).lstrip()
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        return ".zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ".hwp"
    if stripped.startswith(b"%PDF"):
        return ".pdf"
    if stripped.startswith((b"<html", b"<!DOCTYPE html", b"<!doctype html")):
        return ".html"
    return ".bin"


def _cached_download_path(out_dir: Path, seq: str, safe_title: str) -> Path | None:
    for suffix in (".zip", ".hwp", ".hwpx", ".docx", ".pdf", ".txt", ".html", ".bin"):
        candidate = out_dir / f"{seq}_{safe_title}{suffix}"
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def download_sample_archive(entry: OfficialSampleEntry, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r'[\\/:*?"<>|]+', "_", entry.title).strip(" .")[:120] or entry.seq
    cached_path = _cached_download_path(out_dir, entry.seq, safe_title)
    if cached_path:
        return cached_path
    data = _curl_bytes(
        DOWNLOAD_URL,
        {
            "sysDstinCd": "01",
            "fileMstky": entry.file_mstky,
            "filedetlSeq": entry.filedetl_seq,
            "downlDstinCd": "09",
        },
        timeout_sec=120,
    )
    suffix = _infer_download_suffix(data)
    out_path = out_dir / f"{entry.seq}_{safe_title}{suffix}"
    out_path.write_bytes(data)
    return out_path


def _method_from_name(filename: str) -> str:
    methods = _methods_from_name_or_text(filename, "")
    if methods:
        return methods[0]
    return ""


def _methods_from_name_or_text(filename: str, text: str = "") -> list[str]:
    name = str(filename or "")
    compact_name = re.sub(r"\s+", "", name)
    name_matches: list[str] = []
    for method, patterns in _METHOD_DETECTION_PATTERNS:
        if any(re.sub(r"\s+", "", pattern) in compact_name for pattern in patterns):
            name_matches.append(method)
    if name_matches:
        return name_matches

    compact_text = re.sub(r"\s+", "", str(text or "")[:40000])
    text_matches: list[str] = []
    for method, patterns in _METHOD_DETECTION_PATTERNS:
        if any(re.sub(r"\s+", "", pattern) in compact_text for pattern in patterns):
            text_matches.append(method)
    return text_matches


def _artifact_type(filename: str, text: str = "") -> str:
    artifact_types = _artifact_types(filename, text)
    return artifact_types[0] if artifact_types else "other"


def _artifact_types(filename: str, text: str = "") -> list[str]:
    found: list[str] = []
    name = str(filename or "")
    if "평가" in filename:
        found.append("evaluation_form")
    if "과제" in filename or "문항" in filename:
        found.append("task")
    compact_text = re.sub(r"\s+", "", str(text or "")[:40000])
    markers = (
        ("job_description", ("직무기술서", "직무 설명자료", "직무설명자료")),
        ("job_posting", ("채용공고", "모집공고")),
        ("task", ("면접질문지", "면접과제", "면접문항", "발표면접과제", "토론면접과제")),
        ("evaluation_form", ("면접전형별평가표", "평가양식", "평가요소", "평가항목", "평가표", "채점기준")),
    )
    for artifact_type, tokens in markers:
        if any(token in compact_text for token in tokens):
            found.append(artifact_type)
    out: list[str] = []
    seen: set[str] = set()
    for item in found:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out or ["other"]


def _container_type(path: Path) -> str:
    return "zip" if zipfile.is_zipfile(path) else "document"


def _method_source(filename: str, markdown: str) -> str:
    return "filename" if _methods_from_name_or_text(filename, "") else "document_text"


def _extract_terms(text: str, limit: int = 8) -> list[str]:
    cleaned_text = _clean_html_text(text)
    candidates: list[str] = []
    for pattern in (r"평가\s*요소\s*[:：]?\s*([^\n\r]{2,80})", r"평가\s*항목\s*[:：]?\s*([^\n\r]{2,80})", r"역량\s*[:：]?\s*([^\n\r]{2,80})"):
        for match in re.finditer(pattern, cleaned_text):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" -:：|")
            if value and value not in candidates:
                candidates.append(value[:80])
            if len(candidates) >= limit:
                return candidates
    for token in ("평가요소", "평가항목", "면접과제", "평가양식", "응시자", "면접위원", "채점"):
        if token in cleaned_text and token not in candidates:
            candidates.append(token)
    return candidates[:limit]


def _official_sample_signal_flags(text: str) -> dict[str, bool]:
    compact = re.sub(r"\s+", "", str(text or ""))

    def has_any(tokens: tuple[str, ...]) -> bool:
        return any(re.sub(r"\s+", "", token) in compact for token in tokens)

    return {
        "has_candidate_instruction": has_any(("응시자", "지원자", "수험자", "면접대상자")),
        "has_interviewer_instruction": has_any(("면접위원", "평가위원", "면접관")),
        "has_time_limit": bool(re.search(r"(제한시간|\d+\s*분\s*(이내|동안|발표|토론|준비|작성)?)", str(text or ""))),
        "has_scoring_criteria": has_any(("채점기준", "평가기준", "평가척도", "평정기준", "채점")),
        "has_rating_scale": has_any(("평정", "배점", "점수", "등급", "탁월", "우수", "보통", "미흡")),
    }


def _minute_values_near(text: str, labels: tuple[str, ...]) -> str:
    source = str(text or "")
    values: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in values:
            values.append(value)

    for label in labels:
        label_pattern = re.escape(label)
        for match in re.finditer(rf"{label_pattern}(?!\s*면접).{{0,30}}?(\d{{1,3}})\s*분", source, flags=re.DOTALL):
            add(match.group(1))
    return "; ".join(values[:4])


def _extract_rating_scale_labels(text: str) -> str:
    source = str(text or "")
    labels: list[str] = []
    for label in ("탁월", "우수", "양호", "보통", "미흡", "부족", "매우우수", "매우 미흡"):
        if label in source and label not in labels:
            labels.append(label)
    for score in re.findall(r"(?<!\d)(100|90|80|70|60)(?!\d)", source):
        if score not in labels:
            labels.append(score)
    return "; ".join(labels[:12])


def _extract_evaluation_elements(text: str, limit: int = 10) -> str:
    source = _clean_html_text(text)
    elements: list[str] = []

    def add(value: str) -> None:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" -:：,;|")
        if cleaned and cleaned not in elements:
            elements.append(cleaned[:80])

    for pattern in (
        r"평가\s*요소\s*[:：]?\s*([^\n\r]{2,160})",
        r"평가\s*항목\s*[:：]?\s*([^\n\r]{2,160})",
        r"평가\s*주안점\s*[:：]?\s*([^\n\r]{2,160})",
        r"역량\s*[:：]?\s*([^\n\r]{2,160})",
    ):
        for match in re.finditer(pattern, source):
            for part in re.split(r"\s*(?:[,;/|]|ㆍ|·|•)\s*", match.group(1)):
                add(part)
                if len(elements) >= limit:
                    return "; ".join(elements)
    for token in (
        "미래예측",
        "창의적 사고",
        "상황 판단",
        "혁신적 사고",
        "논리 분석",
        "실현가능성",
        "문제해결",
        "의사결정",
    ):
        if token in source:
            add(token)
    return "; ".join(elements[:limit])


def _task_prompt_style(method: str, text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    if method == "경험면접":
        if "유사경험" in compact or "관련경력이없" in compact:
            return "experience_or_similar_training_case"
        return "experience_star_probe"
    if method == "상황면접":
        return "work_situation_judgment"
    if method == "발표면접":
        if "질의응답" in compact or "질의" in compact:
            return "presentation_materials_qna"
        return "presentation_materials"
    if method == "토론면접":
        if "입장발표" in compact or "기조발언" in compact:
            return "discussion_opening_position"
        return "discussion_consensus"
    if method == "창의적 문제해결력면접":
        if "미래예측" in compact:
            return "creative_future_prediction_solution"
        return "creative_problem_solution"
    if method == "인바스켓면접":
        return "inbasket_priority_documents"
    if method == "직무지식면접":
        return "job_knowledge_procedure"
    return ""


def _followup_section_labels(text: str) -> str:
    source = str(text or "")
    labels: list[str] = []
    for label in ("질문", "추가질문", "후속질문", "탐침질문", "꼬리질문", "확인질문", "질의응답"):
        if label in source and label not in labels:
            labels.append(label)
    return "; ".join(labels)


def _official_sample_structured_signals(method: str, text: str) -> dict[str, str]:
    return {
        "task_prompt_style": _task_prompt_style(method, text),
        "followup_section_labels": _followup_section_labels(text),
        "prep_minutes": _minute_values_near(text, ("준비", "준비시간", "사전준비")),
        "presentation_minutes": _minute_values_near(text, ("발표", "발표시간")),
        "qa_minutes": _minute_values_near(text, ("질의응답", "질의", "문답", "질문")),
        "discussion_minutes": _minute_values_near(text, ("토론", "토의", "토론시간")),
        "rating_scale_labels": _extract_rating_scale_labels(text),
        "evaluation_elements": _extract_evaluation_elements(text),
    }


def _line_bounds(source: str, start: int) -> tuple[int, int, str]:
    line_start = source.rfind("\n", 0, start) + 1
    line_end = source.find("\n", start)
    if line_end < 0:
        line_end = len(source)
    return line_start, line_end, source[line_start:line_end]


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _looks_like_toc_method_line(source: str, line_start: int, line_end: int) -> bool:
    line = source[line_start:line_end].strip()
    if not line:
        return False
    compact_line = _compact(line)
    before = source[max(0, line_start - 700) : line_start]
    near_toc = "목차" in before or "차례" in before
    numbered = bool(re.match(r"^\s*(\d{1,2}[\)\.]|[IVX]{1,5}[\)\.]|[가-힣][\)\.])\s*", line, flags=re.IGNORECASE))
    page_numbered = bool(re.search(r"(\t|\s{2,})\d{1,3}\s*$", line))
    short_listing = len(compact_line) <= 45 and numbered
    return bool(page_numbered or (near_toc and short_listing))


def _looks_like_method_section_anchor(source: str, start: int, end: int, pattern: str) -> bool:
    line_start, line_end, line = _line_bounds(source, start)
    if _looks_like_toc_method_line(source, line_start, line_end):
        return False
    compact_line = _compact(line)
    compact_pattern = _compact(pattern)
    if compact_pattern not in compact_line:
        return False
    if len(compact_line) > 180:
        return False
    if any(word in compact_line for word in _METHOD_SECTION_ANCHOR_WORDS):
        return True
    if compact_line in {compact_pattern, f"-{compact_pattern}-", f"[{compact_pattern}]"}:
        return True
    nearby = _compact(source[max(0, start - 30) : min(len(source), end + 50)])
    return any(word in nearby for word in _METHOD_SECTION_ANCHOR_WORDS)


def _method_marker_hits(text: str) -> list[tuple[int, int, str]]:
    source = str(text or "")
    hits: list[tuple[int, int, str]] = []
    for method, patterns in _METHOD_DETECTION_PATTERNS:
        for raw_pattern in patterns:
            pattern = str(raw_pattern or "").strip()
            if len(pattern) < 2:
                continue
            for match in re.finditer(re.escape(pattern), source):
                start, end = match.span()
                if not _looks_like_method_section_anchor(source, start, end, pattern):
                    continue
                if any(existing_method == method and abs(existing_start - start) <= 3 for existing_start, _, existing_method in hits):
                    continue
                hits.append((start, end, method))
    for pattern in _UNSUPPORTED_INTERVIEW_SECTION_PATTERNS:
        for match in re.finditer(re.escape(pattern), source, flags=re.IGNORECASE):
            start, end = match.span()
            if not _looks_like_method_section_anchor(source, start, end, pattern):
                continue
            if any(abs(existing_start - start) <= 3 for existing_start, _, _existing_method in hits):
                continue
            hits.append((start, end, _UNSUPPORTED_METHOD_BOUNDARY))
    return sorted(hits, key=lambda item: (item[0], item[1] - item[0]))


def _method_context_score(method: str, text: str) -> int:
    signals = _official_sample_structured_signals(method, text)
    flags = _official_sample_signal_flags(text)
    structured_fields = (
        "followup_section_labels",
        "prep_minutes",
        "presentation_minutes",
        "qa_minutes",
        "discussion_minutes",
        "rating_scale_labels",
        "evaluation_elements",
    )
    score = sum(1 for value in flags.values() if value)
    score += sum(2 for field in structured_fields if str(signals.get(field) or "").strip())
    score += min(len(_extract_terms(text)), 3)
    return score


def _method_contexts_from_text(
    filename: str,
    text: str,
    methods: list[str],
    method_source: str,
) -> dict[str, tuple[str, str]]:
    source = str(text or "")
    default = {method: (source, "full_document") for method in methods}
    named_methods = [method for method in methods if method]
    if not source or len(named_methods) <= 1 or method_source == "filename":
        return default

    method_set = set(named_methods)
    hits = [
        hit
        for hit in _method_marker_hits(source)
        if hit[2] in method_set or hit[2] == _UNSUPPORTED_METHOD_BOUNDARY
    ]
    supported_hits = [hit for hit in hits if hit[2] in method_set]
    if len(supported_hits) < 2:
        return default

    sections_by_method: dict[str, list[str]] = {method: [] for method in named_methods}
    for index, (start, _end, method) in enumerate(hits):
        next_start = hits[index + 1][0] if index + 1 < len(hits) else len(source)
        if method == _UNSUPPORTED_METHOD_BOUNDARY:
            continue
        section = source[start:next_start].strip()
        if section:
            sections_by_method.setdefault(method, []).append(section)

    scored_by_method = {
        method: [
            (_method_context_score(method, section), len(section), section)
            for section in sections_by_method.get(method, [])
        ]
        for method in named_methods
    }
    reliable_methods = {
        method
        for method, scored_sections in scored_by_method.items()
        if any(score > 0 for score, _length, _section in scored_sections)
    }
    if len(reliable_methods) < min(2, len(named_methods)):
        return default

    contexts = dict(default)
    for method in named_methods:
        scored_sections = scored_by_method.get(method, [])
        selected = [section for score, _length, section in scored_sections if score > 0]
        context_source = "document_section"
        if not selected:
            selected = [section for _score, _length, section in scored_sections if section]
            context_source = "document_section_low_signal"
        if selected:
            contexts[method] = ("\n\n".join(selected), context_source)
    return contexts


def _profile_document_bytes(filename: str, data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_DOC_SUFFIXES:
        return [], [f"{filename}: unsupported suffix: {suffix or '(none)'}"]
    try:
        if suffix == ".txt":
            markdown = data.decode("utf-8", errors="ignore")
        else:
            parsed = parse_with_kordoc(data, filename=Path(filename).name, ocr=False)
            markdown = str(parsed.get("markdown") or "")
    except KordocParseError as exc:
        warnings.append(f"{filename}: parse failed: {exc}")
        markdown = ""

    methods = _methods_from_name_or_text(filename, markdown) or [""]
    artifact_types = _artifact_types(filename, markdown)
    artifact_type = artifact_types[0] if artifact_types else "other"
    method_source = _method_source(filename, markdown) if methods != [""] else ""
    rows: list[dict[str, Any]] = []
    method_contexts = _method_contexts_from_text(filename, markdown, methods, method_source)
    for method in methods:
        method_text, method_context_source = method_contexts.get(method, (markdown, "full_document"))
        signal_flags = _official_sample_signal_flags(method_text)
        structured_signals = _official_sample_structured_signals(method, method_text)
        rows.append(
            {
                "member": Path(filename).name,
                "suffix": suffix,
                "method": method,
                "method_source": method_source,
                "method_context_source": method_context_source,
                "method_context_chars": len(method_text),
                "artifact_type": artifact_type,
                "artifact_types": "; ".join(artifact_types),
                "chars": len(markdown),
                "has_task_prompt": any(token in markdown for token in ("면접과제", "면접질문지", "과제", "응시자", "발표", "토론")),
                "has_evaluation_form": any(token in markdown for token in ("평가양식", "평가요소", "평가항목", "평가표", "채점", "평정")),
                **signal_flags,
                **structured_signals,
                "terms": "; ".join(_extract_terms(method_text)),
            }
        )
    return rows, warnings


def profile_sample_archive(archive_path: Path, max_members: int = 16) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not zipfile.is_zipfile(archive_path):
        return _profile_document_bytes(archive_path.name, archive_path.read_bytes())
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist()[:max_members]:
                if info.is_dir():
                    continue
                suffix = Path(info.filename).suffix.lower()
                if suffix not in SUPPORTED_DOC_SUFFIXES:
                    continue
                try:
                    data = archive.read(info)
                except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                    warnings.append(f"{info.filename}: read failed: {exc}")
                    continue
                member_rows, member_warnings = _profile_document_bytes(info.filename, data)
                rows.extend(member_rows)
                warnings.extend(member_warnings)
    except zipfile.BadZipFile as exc:
        warnings.append(f"{archive_path.name}: not a readable ZIP: {exc}")
    return rows, warnings


def profile_sample_file(sample_path: Path, max_members: int = 16) -> tuple[list[dict[str, Any]], list[str]]:
    return profile_sample_archive(sample_path, max_members=max_members)


def summarize_sample(title: str, member_rows: list[dict[str, Any]]) -> dict[str, Any]:
    methods = sorted({str(row.get("method") or "") for row in member_rows if str(row.get("method") or "")})
    artifact_type_set: set[str] = set()
    for row in member_rows:
        for raw_type in str(row.get("artifact_types") or row.get("artifact_type") or "").split(";"):
            artifact_type = raw_type.strip()
            if artifact_type:
                artifact_type_set.add(artifact_type)
    artifact_types = sorted(artifact_type_set)
    task_methods = sorted(
        {
            str(row.get("method") or "")
            for row in member_rows
            if "task" in str(row.get("artifact_types") or row.get("artifact_type") or "") and str(row.get("method") or "")
        }
    )
    eval_methods = sorted(
        {
            str(row.get("method") or "")
            for row in member_rows
            if "evaluation_form" in str(row.get("artifact_types") or row.get("artifact_type") or "") and str(row.get("method") or "")
        }
    )
    member_names = {str(row.get("member") or "") for row in member_rows if str(row.get("member") or "")}
    pairing_scope = "document" if len(member_names) == 1 and {"task", "evaluation_form"}.issubset(set(artifact_types)) else "archive_members"
    return {
        "title": title,
        "member_count": len(member_rows),
        "methods": "; ".join(methods),
        "task_methods": "; ".join(task_methods),
        "evaluation_methods": "; ".join(eval_methods),
        "method_count": len(methods),
        "artifact_types": "; ".join(artifact_types),
        "pairing_scope": pairing_scope,
        "has_task_and_eval_material": bool(task_methods and eval_methods),
        "has_task_and_eval_pairs": bool(task_methods and set(task_methods).issubset(set(eval_methods))),
    }


def write_reports(samples: list[dict[str, Any]], members: list[dict[str, Any]], report_dir: Path) -> tuple[Path, Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = report_dir / f"ncs_official_interview_samples_{stamp}.md"
    sample_csv = report_dir / f"ncs_official_interview_samples_{stamp}.csv"
    member_csv = report_dir / f"ncs_official_interview_sample_members_{stamp}.csv"

    sample_fields = [
        "collection",
        "collection_id",
        "lib_dstin_cd",
        "menu_id",
        "seq",
        "title",
        "ncs_code_hint",
        "detail_label_hint",
        "view_text",
        "member_count",
        "methods",
        "task_methods",
        "evaluation_methods",
        "method_count",
        "artifact_types",
        "pairing_scope",
        "has_task_and_eval_material",
        "has_task_and_eval_pairs",
        "archive",
        "download_path",
        "download_suffix",
        "container_type",
        "warnings",
    ]
    with sample_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=sample_fields)
        writer.writeheader()
        for row in samples:
            writer.writerow({field: row.get(field, "") for field in sample_fields})

    member_fields = [
        "collection",
        "collection_id",
        "seq",
        "title",
        "member",
        "suffix",
        "method",
        "method_source",
        "method_context_source",
        "method_context_chars",
        "artifact_type",
        "artifact_types",
        "chars",
        "has_task_prompt",
        "has_evaluation_form",
        "has_candidate_instruction",
        "has_interviewer_instruction",
        "has_time_limit",
        "has_scoring_criteria",
        "has_rating_scale",
        "task_prompt_style",
        "followup_section_labels",
        "prep_minutes",
        "presentation_minutes",
        "qa_minutes",
        "discussion_minutes",
        "rating_scale_labels",
        "evaluation_elements",
        "terms",
    ]
    with member_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=member_fields)
        writer.writeheader()
        for row in members:
            writer.writerow({field: row.get(field, "") for field in member_fields})

    passed_pairs = sum(1 for row in samples if row.get("has_task_and_eval_pairs"))
    observed_methods = sorted({str(row.get("method") or "") for row in members if str(row.get("method") or "")})
    missing_methods = [method for method in METHOD_NAMES if method not in observed_methods]
    candidate_instruction_count = sum(1 for row in members if row.get("has_candidate_instruction"))
    interviewer_instruction_count = sum(1 for row in members if row.get("has_interviewer_instruction"))
    time_limit_count = sum(1 for row in members if row.get("has_time_limit"))
    scoring_criteria_count = sum(1 for row in members if row.get("has_scoring_criteria"))
    rating_scale_count = sum(1 for row in members if row.get("has_rating_scale"))
    structured_time_count = sum(
        1
        for row in members
        if any(str(row.get(field) or "").strip() for field in ("prep_minutes", "presentation_minutes", "qa_minutes", "discussion_minutes"))
    )
    rating_label_count = sum(1 for row in members if str(row.get("rating_scale_labels") or "").strip())
    evaluation_element_count = sum(1 for row in members if str(row.get("evaluation_elements") or "").strip())
    section_context_count = sum(1 for row in members if row.get("method_context_source") == "document_section")
    ncs_code_hint_count = sum(1 for row in samples if str(row.get("ncs_code_hint") or "").strip())
    detail_label_hint_count = sum(1 for row in samples if str(row.get("detail_label_hint") or "").strip())
    prompt_styles = sorted(
        {
            str(row.get("task_prompt_style") or "").strip()
            for row in members
            if str(row.get("task_prompt_style") or "").strip()
        }
    )
    lines = [
        f"# NCS Official Interview Samples - {stamp}",
        "",
        f"- Samples profiled: {len(samples)}",
        f"- Samples with task/evaluation pairs: {passed_pairs}",
        f"- Observed methods: {', '.join(observed_methods) if observed_methods else 'none'}",
        f"- Supported methods not observed in sampled files: {', '.join(missing_methods) if missing_methods else 'none'}",
        f"- Member method rows with candidate instructions: {candidate_instruction_count}",
        f"- Member method rows with interviewer/evaluator instructions: {interviewer_instruction_count}",
        f"- Member method rows with time-limit signals: {time_limit_count}",
        f"- Member method rows with scoring criteria: {scoring_criteria_count}",
        f"- Member method rows with rating-scale signals: {rating_scale_count}",
        f"- Member method rows with structured timing values: {structured_time_count}",
        f"- Member method rows with extracted rating labels: {rating_label_count}",
        f"- Member method rows with extracted evaluation elements: {evaluation_element_count}",
        f"- Member method rows with section-specific context: {section_context_count}",
        f"- Samples with NCS code hints from title: {ncs_code_hint_count}",
        f"- Samples with detail label hints from title: {detail_label_hint_count}",
        f"- Observed task prompt styles: {', '.join(prompt_styles) if prompt_styles else 'none'}",
        "",
        "| collection | seq | ncs code | detail hint | title | methods | artifacts | task methods | evaluation methods | members | paired |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in samples:
        lines.append(
            f"| {row.get('collection')} | {row.get('seq')} | {row.get('ncs_code_hint')} | {str(row.get('detail_label_hint') or '').replace('|', '/')} | "
            f"{str(row.get('title') or '').replace('|', '/')} | {row.get('methods')} | "
            f"{row.get('artifact_types')} | {row.get('task_methods')} | {row.get('evaluation_methods')} | {row.get('member_count')} | {row.get('has_task_and_eval_pairs')} |"
        )
    lines.extend(["", f"CSV: `{sample_csv}`", f"Member CSV: `{member_csv}`", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, sample_csv, member_csv


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile official NCS fair-hiring interview task/evaluation samples.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--out-dir", default=".tmp/ncs_official_interview_samples")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--max-members", type=int, default=16)
    parser.add_argument("--collection", choices=[*SAMPLE_COLLECTIONS.keys(), "all"], default="interview-model")
    parser.add_argument("--list-url", default="")
    args = parser.parse_args()

    samples: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    collections = list(SAMPLE_COLLECTIONS.values()) if args.collection == "all" else [_collection_from_key(args.collection)]
    for collection in collections:
        list_url = str(args.list_url or "").strip() or collection.list_url
        entries = discover_official_samples(max(1, int(args.limit)), list_url=list_url, collection=collection)
        for entry in entries:
            archive_path = download_sample_archive(entry, Path(args.out_dir))
            view_text = fetch_sample_view_text(entry)
            member_rows, warnings = profile_sample_file(archive_path, max_members=max(1, int(args.max_members)))
            summary = summarize_sample(entry.title, member_rows)
            sample_row = {
                "collection": entry.collection_label,
                "collection_id": entry.collection_key,
                "lib_dstin_cd": entry.lib_dstin_cd,
                "menu_id": entry.menu_id,
                "seq": entry.seq,
                "title": entry.title,
                "ncs_code_hint": entry.ncs_code_hint,
                "detail_label_hint": entry.detail_label_hint,
                "view_text": view_text[:500],
                "archive": str(archive_path),
                "download_path": str(archive_path),
                "download_suffix": archive_path.suffix.lower(),
                "container_type": _container_type(archive_path),
                "warnings": "; ".join(warnings),
                **summary,
            }
            samples.append(sample_row)
            for row in member_rows:
                members.append(
                    {
                        "collection": entry.collection_label,
                        "collection_id": entry.collection_key,
                        "seq": entry.seq,
                        "title": entry.title,
                        **row,
                    }
                )
            time.sleep(0.2)
    md_path, sample_csv, member_csv = write_reports(samples, members, Path(args.report_dir))
    print(f"report={md_path}")
    print(f"csv={sample_csv}")
    print(f"member_csv={member_csv}")
    print(f"rows={len(samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
