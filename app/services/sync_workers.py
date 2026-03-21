"""
Sync Workers - 공공기관/NCS 데이터 동기화
"""

from __future__ import annotations

import json
from typing import Any
from xml.etree import ElementTree as ET

from app.repository import upsert_institution, upsert_ncs_units, start_ncs_sync, finish_ncs_sync
from app.services.external_api import fetch_public_inst, fetch_ncs
from app.settings import settings


# ---------------------------------------------------------------------------
# Pure utility functions (테스트 가능한 순수 함수)
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list:
    """None이면 빈 리스트, 리스트면 그대로, 그 외엔 단일 아이템 리스트로 변환."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _pick(row: dict, *keys: str, default: str = "") -> str:
    """여러 키 중 첫 번째로 값이 있는 항목 반환."""
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def _extract_public_items(payload: dict) -> list[dict]:
    """공공기관 API 응답에서 기관 목록 추출."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return []

    # result 배열 형태
    result = data.get("result")
    if isinstance(result, list):
        return result

    # response.body.items.item 형태
    try:
        items = data["response"]["body"]["items"]["item"]
        return _as_list(items)
    except (KeyError, TypeError):
        return []


def _parse_ncs_items_from_xml(xml_text: str) -> tuple[list[dict], int | None]:
    """XML에서 NCS 능력단위 목록 파싱."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], None

    # totalCount 추출
    total = None
    tc = root.find(".//totalCount")
    if tc is not None and tc.text and tc.text.strip().isdigit():
        total = int(tc.text.strip())

    # item 요소들 파싱
    items = []
    fields = [
        "ncsClCd", "compeUnitName", "compeUnitLevel", "compeUnitDef",
        "ncsLclasCdnm", "ncsMclasCdnm", "ncsSclasCdnm", "ncsSubdCdnm",
    ]
    for item_el in root.findall(".//item"):
        row: dict[str, Any] = {}
        for f in fields:
            el = item_el.find(f)
            row[f] = el.text.strip() if el is not None and el.text else ""
        items.append(row)

    return items, total


def _parse_ncs_items(content_type: str, body: str) -> tuple[list[dict], int | None]:
    """content_type에 따라 JSON/XML 자동 판별하여 NCS 항목 파싱."""
    if "json" in content_type.lower():
        try:
            data = json.loads(body)
            body_section = data["response"]["body"]
            raw_items = body_section["items"]["item"]
            items = _as_list(raw_items)
            tc = body_section.get("totalCount")
            total = int(tc) if tc is not None and str(tc).isdigit() else None
            return items, total
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return [], None
    else:
        return _parse_ncs_items_from_xml(body)


# ---------------------------------------------------------------------------
# Sync functions (외부 API 호출 + DB 저장)
# ---------------------------------------------------------------------------

def sync_public_institutions(
    max_pages: int = 5,
    num_of_rows: int = 100,
) -> dict[str, Any]:
    """공공기관 목록을 API에서 가져와 DB에 저장."""
    upserted = 0
    errors = 0

    for page in range(1, max_pages + 1):
        try:
            payload = fetch_public_inst(
                resource="list",
                page_no=page,
                num_of_rows=num_of_rows,
                data_type="json",
            )
            items = _extract_public_items(payload)
            if not items:
                break
            for item in items:
                try:
                    upsert_institution(
                        inst_cd=_pick(item, "instCd"),
                        inst_name=_pick(item, "instNm", "instName"),
                        inst_type_code=_pick(item, "instType", "instTp"),
                        supervising_ministry_code=_pick(item, "supervisingMinCd", "minCd"),
                        region_code=_pick(item, "regionCd", "region"),
                    )
                    upserted += 1
                except Exception:
                    errors += 1
        except Exception:
            errors += 1
            break

    return {"upserted": upserted, "errors": errors, "pages": max_pages}


def sync_ncs_units(
    path: str | None = None,
    pages: int = 20,
    num_of_rows: int = 100,
) -> dict[str, Any]:
    """NCS 능력단위를 API에서 가져와 DB에 저장."""
    if path is None:
        path = settings.ncs_sync_path()

    version_tag = path.split("/")[-1] if path else "unknown"
    run_id = start_ncs_sync(version_tag=version_tag)
    total_upserted = 0
    errors = 0

    for page in range(1, pages + 1):
        try:
            query = {
                "ServiceKey": settings.ncs_key(),
                "pageNo": str(page),
                "numOfRows": str(num_of_rows),
            }
            result = fetch_ncs(path=path, query=query)
            if result.get("status_code") != 200:
                break

            content_type = result.get("content_type", "")
            body = result.get("data", "")
            items, total = _parse_ncs_items(content_type, body)
            if not items:
                break

            upsert_ncs_units(
                version_tag=version_tag,
                units=items,
                deactivate_existing=(page == 1),
            )
            total_upserted += len(items)

            if total is not None and total_upserted >= total:
                break
        except Exception:
            errors += 1
            break

    finish_ncs_sync(run_id=run_id, status="done", total_count=total_upserted)
    return {"upserted": total_upserted, "errors": errors, "pages": pages}
