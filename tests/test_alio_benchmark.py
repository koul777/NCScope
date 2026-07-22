from __future__ import annotations

import io
import importlib.util
from pathlib import Path
import sys
import zipfile

import pytest

from app.services.kordoc_parser import KordocParseError


_BENCHMARK_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_alio_jd.py"
_SPEC = importlib.util.spec_from_file_location("ncscope_benchmark_alio_jd", _BENCHMARK_PATH)
assert _SPEC and _SPEC.loader
benchmark_alio_jd = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = benchmark_alio_jd
_SPEC.loader.exec_module(benchmark_alio_jd)
parse_benchmark_document = benchmark_alio_jd.parse_benchmark_document


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _mark_zip_encrypted(data: bytes) -> bytes:
    blob = bytearray(data)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = 0
        while True:
            idx = blob.find(signature, start)
            if idx < 0:
                break
            flags = int.from_bytes(blob[idx + offset : idx + offset + 2], "little") | 0x1
            blob[idx + offset : idx + offset + 2] = flags.to_bytes(2, "little")
            start = idx + 4
    return bytes(blob)


def test_benchmark_zip_txt_member_is_parsed_without_kordoc() -> None:
    data = _zip_bytes({"직무기술서.txt": "세분류: 경영기획\n담당업무: 경영계획 수립"})

    parsed = parse_benchmark_document(data, filename="alio.zip", max_bytes=1024 * 1024)

    assert "ZIP member: 직무기술서.txt" in parsed["markdown"]
    assert "세분류: 경영기획" in parsed["markdown"]
    assert parsed["metadata"]["archive"] is True
    assert parsed["metadata"]["members"] == [{"filename": "직무기술서.txt", "suffix": ".txt"}]


def test_benchmark_zip_encrypted_member_returns_parse_error() -> None:
    data = _mark_zip_encrypted(_zip_bytes({"직무기술서.txt": "세분류: 경영기획"}))

    with pytest.raises(KordocParseError, match="ZIP contains no parseable"):
        parse_benchmark_document(data, filename="alio.zip", max_bytes=1024 * 1024)
