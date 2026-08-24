from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def export_catalog(db_path: Path) -> dict[str, object]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            WITH unit_counts AS (
                SELECT substr(base_unit_code, 1, 8) AS detail_code,
                       COUNT(*) AS unit_count
                FROM competency_units
                WHERE length(base_unit_code) >= 10
                GROUP BY substr(base_unit_code, 1, 8)
            )
            SELECT c.major_code || c.middle_code || c.small_code || c.sub_code
                       AS detail_code,
                   c.sub_name AS name,
                   c.api_ncs_degr AS ncs_degree,
                   c.api_usg_yn AS usage_yn,
                   u.unit_count AS unit_count
            FROM classifications c
            JOIN unit_counts u
              ON u.detail_code =
                 c.major_code || c.middle_code || c.small_code || c.sub_code
            WHERE length(c.major_code || c.middle_code || c.small_code || c.sub_code) = 8
              AND trim(c.sub_name) <> ''
              AND upper(trim(COALESCE(c.api_usg_yn, ''))) = 'Y'
            ORDER BY detail_code, name
            """
        ).fetchall()
    finally:
        connection.close()

    details = [
        {
            "code": str(row["detail_code"]),
            "name": str(row["name"]),
            "ncs_degree": str(row["ncs_degree"] or ""),
            "usage_yn": str(row["usage_yn"] or ""),
            "unit_count": int(row["unit_count"] or 0),
        }
        for row in rows
    ]
    return {
        "schema_version": 1,
        "source": "NCS_MCP classifications joined to unit-code prefixes",
        "classification_count": len(details),
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the lightweight official NCS detail code/name catalog."
    )
    parser.add_argument("db_path")
    parser.add_argument(
        "--output",
        default="app/data/ncs_detail_catalog.json",
    )
    args = parser.parse_args()

    payload = export_catalog(Path(args.db_path))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"details={payload['classification_count']}")
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
