from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.ncs_mcp_client import (  # noqa: E402
    search_units_by_detail,
    use_ncs_mcp_request_session,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def export_catalog_from_mcp(detail_catalog_path: Path) -> dict[str, Any]:
    detail_payload = json.loads(detail_catalog_path.read_text(encoding="utf-8"))
    details = detail_payload.get("details")
    if not isinstance(details, list):
        raise ValueError("detail catalog must contain a details list")

    units_by_code: dict[str, dict[str, str]] = {}
    with use_ncs_mcp_request_session():
        for index, detail in enumerate(details, start=1):
            if not isinstance(detail, dict):
                continue
            if _text(detail.get("usage_yn")).upper() != "Y":
                continue
            detail_code = _text(detail.get("code"))
            detail_name = _text(detail.get("name"))
            if len(detail_code) != 8 or not detail_name:
                continue
            official_units = search_units_by_detail([detail_name], max_units=200)
            for unit in official_units:
                unit_code = _text(unit.get("ncsClCd"))
                unit_name = _text(unit.get("compeUnitName"))
                if not unit_code or not unit_name:
                    continue
                base_code = unit_code.split("_", 1)[0]
                if not base_code.startswith(detail_code):
                    continue
                units_by_code.setdefault(
                    unit_code,
                    {
                        "code": unit_code,
                        "base_code": base_code,
                        "name": unit_name,
                        "detail_code": detail_code,
                        "detail_name": detail_name,
                    },
                )
            print(
                f"[{index}/{len(details)}] {detail_code} {detail_name} "
                f"units={len(official_units)}",
                flush=True,
            )

    units = sorted(
        units_by_code.values(),
        key=lambda item: (item["detail_code"], item["base_code"], item["code"]),
    )
    return {
        "schema_version": 1,
        "source": "NCS_MCP ncs_search exact-detail results",
        "detail_catalog": detail_catalog_path.name,
        "unit_count": len(units),
        "units": units,
    }


def export_catalog_from_db(db_path: Path) -> dict[str, Any]:
    """Export through immutable code-prefix joins, never classification_id.

    A handful of source rows have a stale ``classification_id``.  The official
    ten-digit base unit code still embeds the eight-digit detail code, so the
    prefix is the deterministic hierarchy edge used by the serving resolver.
    The database is always opened in SQLite read-only mode.
    """

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT cu.unit_code AS code,
                   cu.base_unit_code AS base_code,
                   COALESCE(
                       NULLIF(TRIM(cu.unit_name_refined), ''),
                       NULLIF(TRIM(cu.api_unit_name), ''),
                       cu.unit_name_raw
                   ) AS name,
                   substr(cu.base_unit_code, 1, 8) AS detail_code,
                   c.sub_name AS detail_name
            FROM competency_units cu
            JOIN classifications c
              ON c.major_code || c.middle_code || c.small_code || c.sub_code
                 = substr(cu.base_unit_code, 1, 8)
            WHERE length(cu.base_unit_code) >= 10
              AND trim(cu.unit_code) <> ''
              AND upper(trim(COALESCE(c.api_usg_yn, ''))) = 'Y'
              AND trim(COALESCE(
                    cu.unit_name_refined,
                    cu.api_unit_name,
                    cu.unit_name_raw,
                    ''
                  )) <> ''
            ORDER BY detail_code, base_code, code
            """
        ).fetchall()
    finally:
        connection.close()

    units = [
        {
            "code": _text(row["code"]),
            "base_code": _text(row["base_code"]),
            "name": _text(row["name"]),
            "detail_code": _text(row["detail_code"]),
            "detail_name": _text(row["detail_name"]),
        }
        for row in rows
    ]
    return {
        "schema_version": 1,
        "source": "NCS_MCP SQLite read-only code-prefix export",
        "database": db_path.name,
        "unit_count": len(units),
        "units": units,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a lightweight official NCS ability-unit code/name catalog "
            "through the read-only MCP contract."
        )
    )
    parser.add_argument(
        "--detail-catalog",
        default="app/data/ncs_detail_catalog.json",
    )
    parser.add_argument(
        "--db-path",
        help=(
            "optional NCS_MCP SQLite database; when supplied, export every "
            "unit with a read-only code-prefix join instead of MCP iteration"
        ),
    )
    parser.add_argument(
        "--output",
        default="app/data/ncs_unit_catalog.json",
    )
    args = parser.parse_args()

    if args.db_path:
        payload = export_catalog_from_db(Path(args.db_path))
    else:
        payload = export_catalog_from_mcp(Path(args.detail_catalog))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"units={payload['unit_count']}")
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
