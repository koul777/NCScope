"""Serverless-safe text extraction for HWP 5 and HWPX documents.

Kordoc remains the preferred parser because it preserves richer document
structure. Vercel's Python runtime does not guarantee that a Node executable
and the npm package tree are available inside the function, so these readers
provide a bounded local fallback without calling an external conversion API.
"""

from __future__ import annotations

import io
import re
import struct
import unicodedata
import zlib
import zipfile
from pathlib import PurePosixPath
from typing import Iterable
from xml.etree import ElementTree as ET


class HwpTextExtractionError(RuntimeError):
    """Raised when a supported Hangul document has no safely readable text."""


_MAX_SECTION_COUNT = 256
_MAX_SECTION_BYTES = 16 * 1024 * 1024
_MAX_TEXT_CHARS = 500_000
_HWP_PARA_TEXT_TAG = 0x43
_EXTENDED_CONTROL_CODES = frozenset(
    {1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 14, 15, 16, 17, 20, 21, 22, 23}
)


def _bounded_raw_deflate(data: bytes, limit: int = _MAX_SECTION_BYTES) -> bytes:
    inflater = zlib.decompressobj(-15)
    output = inflater.decompress(data, limit + 1)
    if len(output) > limit or inflater.unconsumed_tail:
        raise HwpTextExtractionError("HWP section exceeds the decompression limit")
    output += inflater.flush(max(0, limit + 1 - len(output)))
    if len(output) > limit:
        raise HwpTextExtractionError("HWP section exceeds the decompression limit")
    return output


def _clean_hwp_paragraph(value: str) -> str:
    chars: list[str] = []
    index = 0
    while index < len(value):
        code = ord(value[index])
        if code in _EXTENDED_CONTROL_CODES:
            # HWP inline/extended controls occupy eight UTF-16 code units.
            # Their parameter bytes are not candidate-facing document text.
            index += 8
            chars.append(" ")
            continue
        if code < 0x20 or code == 0x7F:
            chars.append(" ")
        else:
            chars.append(value[index])
        index += 1
    return re.sub(r"[ \t\f\v]+", " ", "".join(chars)).strip()


def _hwp_record_paragraphs(section: bytes) -> list[str]:
    paragraphs: list[str] = []
    offset = 0
    while offset + 4 <= len(section):
        header = struct.unpack_from("<I", section, offset)[0]
        offset += 4
        tag_id = header & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if offset + 4 > len(section):
                break
            size = struct.unpack_from("<I", section, offset)[0]
            offset += 4
        if size < 0 or offset + size > len(section):
            break
        payload = section[offset : offset + size]
        offset += size
        if tag_id != _HWP_PARA_TEXT_TAG or not payload:
            continue
        paragraph = _clean_hwp_paragraph(payload.decode("utf-16le", errors="ignore"))
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def _section_number(name: str) -> tuple[int, str]:
    match = re.search(r"Section(\d+)$", name, flags=re.IGNORECASE)
    return (int(match.group(1)) if match else 1_000_000, name)


def extract_hwp_text(data: bytes) -> str:
    """Extract bounded paragraph text from an OLE-based HWP 5 document."""

    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - deployment contract covers dependency
        raise HwpTextExtractionError("olefile is required for HWP extraction") from exc

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:
        raise HwpTextExtractionError("not a readable HWP 5 compound document") from exc

    try:
        if not ole.exists("FileHeader") or not ole.exists("BodyText"):
            raise HwpTextExtractionError("HWP body streams are missing")
        header = ole.openstream("FileHeader").read(40)
        compressed = len(header) >= 40 and bool(struct.unpack_from("<I", header, 36)[0] & 0x1)
        section_names = [
            "/".join(parts)
            for parts in ole.listdir(streams=True, storages=False)
            if len(parts) == 2 and parts[0].casefold() == "bodytext" and parts[1].lower().startswith("section")
        ]
        section_names.sort(key=_section_number)
        if not section_names or len(section_names) > _MAX_SECTION_COUNT:
            raise HwpTextExtractionError("HWP section count is invalid")

        paragraphs: list[str] = []
        text_chars = 0
        total_section_bytes = 0
        for name in section_names:
            raw = ole.openstream(name).read(_MAX_SECTION_BYTES + 1)
            if len(raw) > _MAX_SECTION_BYTES:
                raise HwpTextExtractionError("HWP section exceeds the read limit")
            remaining = _MAX_SECTION_BYTES - total_section_bytes
            if remaining <= 0:
                raise HwpTextExtractionError("HWP sections exceed the extraction limit")
            section = _bounded_raw_deflate(raw, remaining) if compressed else raw
            total_section_bytes += len(section)
            if total_section_bytes > _MAX_SECTION_BYTES:
                raise HwpTextExtractionError("HWP sections exceed the extraction limit")
            for paragraph in _hwp_record_paragraphs(section):
                paragraphs.append(paragraph)
                text_chars += len(paragraph)
                if text_chars >= _MAX_TEXT_CHARS:
                    break
            if text_chars >= _MAX_TEXT_CHARS:
                break
    finally:
        ole.close()

    text = "\n".join(paragraphs).strip()
    if not text:
        raise HwpTextExtractionError("HWP contains no readable paragraph text")
    return text[:_MAX_TEXT_CHARS]


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].casefold()


def _hwpx_paragraphs(xml_bytes: bytes) -> Iterable[str]:
    if b"<!DOCTYPE" in xml_bytes.upper() or b"<!ENTITY" in xml_bytes.upper():
        raise HwpTextExtractionError("HWPX XML declarations are not allowed")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise HwpTextExtractionError("HWPX section XML is invalid") from exc
    for element in root.iter():
        if _local_name(element.tag) != "p":
            continue
        pieces = [
            str(node.text or "")
            for node in element.iter()
            if _local_name(node.tag) == "t" and str(node.text or "")
        ]
        paragraph = re.sub(r"[ \t\f\v]+", " ", "".join(pieces)).strip()
        if paragraph:
            yield paragraph


def extract_hwpx_text(data: bytes) -> str:
    """Extract bounded paragraph text from an HWPX ZIP container."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HwpTextExtractionError("not a readable HWPX archive") from exc
    with archive:
        section_infos = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and PurePosixPath(info.filename.replace("\\", "/")).as_posix().casefold().startswith("contents/section")
            and info.filename.casefold().endswith(".xml")
        ]
        section_infos.sort(key=lambda info: _section_number(PurePosixPath(info.filename).stem))
        if not section_infos or len(section_infos) > _MAX_SECTION_COUNT:
            raise HwpTextExtractionError("HWPX section count is invalid")

        paragraphs: list[str] = []
        total_xml_bytes = 0
        text_chars = 0
        for info in section_infos:
            total_xml_bytes += int(info.file_size or 0)
            if total_xml_bytes > _MAX_SECTION_BYTES:
                raise HwpTextExtractionError("HWPX sections exceed the extraction limit")
            xml_bytes = archive.read(info)
            for paragraph in _hwpx_paragraphs(xml_bytes):
                paragraphs.append(paragraph)
                text_chars += len(paragraph)
                if text_chars >= _MAX_TEXT_CHARS:
                    break
            if text_chars >= _MAX_TEXT_CHARS:
                break

    text = "\n".join(paragraphs).strip()
    if not text:
        raise HwpTextExtractionError("HWPX contains no readable paragraph text")
    return text[:_MAX_TEXT_CHARS]


def _classification_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s:：·ㆍ･.,()\[\]{}\-_/]+", "", text)


def extract_linear_ncs_classification_terms(
    text: str,
    *,
    excluded_hierarchy_names: Iterable[str] = (),
    limit: int = 40,
) -> list[str]:
    """Recover possible 세분류 cells from a table flattened to paragraphs.

    HWP text streams retain table-cell order but not row/column coordinates.
    We therefore collect cell labels only inside an explicitly headed NCS
    classification block, discard known 대분류/중분류 labels, and leave the
    final exact 세분류 decision to the official NCS MCP.  Real ALIO HWP tables
    also use abbreviated one-character headers and split a numbered cell over
    several paragraphs, so both layouts are handled without scanning ordinary
    duty prose.
    """

    excluded = {_classification_key(value) for value in excluded_hierarchy_names if str(value or "").strip()}
    output: list[str] = []
    seen: set[str] = set()
    active = False
    recent: list[str] = []
    pending_numbered_parts: list[str] = []
    pending_numbered_cell = False
    capped_limit = max(1, min(100, int(limit or 40)))
    structural_keys = {
        "분류체계",
        "대",
        "중",
        "소",
        "세",
        "대분류",
        "중분류",
        "소분류",
        "세분류",
        "세분류직무",
        "세분류직무명",
        "세분류특화분류",
        "주요",
        "사업",
        "업무",
        "직무",
        "수행",
        "내용",
    }

    def add_label(value: str) -> None:
        label = re.sub(r"\s+", " ", str(value or "")).strip(" |:：-–—")
        if not label:
            return
        # Preserve official ASCII acronyms such as QM/QC관리, but split table
        # shorthand such as PR/광고 or 보건/의료 into independently verifiable
        # cells. The MCP still requires an exact official 세분류 path.
        parts = re.split(r"(?<![A-Za-z])[/／]|[/／](?![A-Za-z])", label)
        labels = [part.strip() for part in parts if part.strip()] if len(parts) > 1 else [label]
        for candidate in labels:
            candidate_key = _classification_key(candidate)
            if (
                not candidate_key
                or candidate_key in structural_keys
                or candidate_key in excluded
                or candidate_key in seen
                or len(candidate) > 80
                or not re.search(r"[가-힣A-Za-z]", candidate)
            ):
                continue
            seen.add(candidate_key)
            output.append(candidate)
            if len(output) >= capped_limit:
                return

    def flush_pending() -> None:
        nonlocal pending_numbered_cell
        if pending_numbered_parts:
            add_label(" ".join(pending_numbered_parts))
            pending_numbered_parts.clear()
        pending_numbered_cell = False

    for raw_line in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        key = _classification_key(line)
        recent.append(key)
        # Wide classification tables can contain many 대/중/소 cells before
        # the final 세분류 header. Keep enough local table history to retain
        # that evidence without treating a document-wide mention as context.
        recent = recent[-40:]
        has_hierarchy_context = any(
            item == "분류체계"
            or "대분류" in item
            or "중분류" in item
            or "소분류" in item
            or item in {"대", "중", "소"}
            for item in recent
        )
        is_detail_header = "세분류" in key or (key == "세" and has_hierarchy_context)
        if is_detail_header and has_hierarchy_context:
            flush_pending()
            active = True
            continue
        boundary_markers = (
            "주요사업",
            "주요업무",
            "해당직무",
            "직무수행내용",
            "능력단위",
            "일반요건",
            "교육요건",
            "필요지식",
            "필요기술",
            "필요태도",
            "전형방법",
            "관련자격",
            "직업기초능력",
            "참고사이트",
        )
        recent_tail = "".join(recent[-3:])
        if active and any(marker in key or marker in recent_tail for marker in boundary_markers):
            flush_pending()
            active = False
            continue
        if not active:
            continue

        if re.fullmatch(r"\d{1,2}(?:\s*[.]\s*\d{1,2})?\s*[.)]?", line):
            flush_pending()
            pending_numbered_cell = True
            continue

        match = re.match(r"^\s*\d{1,2}(?:\s*[.]\s*\d{1,2})?\s*[.)]?\s*(.+?)\s*$", line)
        if match:
            flush_pending()
            add_label(match.group(1))
        elif pending_numbered_cell:
            pending_numbered_parts.append(line)
        elif key not in structural_keys:
            add_label(line)
        if len(output) >= capped_limit:
            pending_numbered_parts.clear()
            break

    flush_pending()
    return output
