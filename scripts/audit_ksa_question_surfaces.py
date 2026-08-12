from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.question_surface import (  # noqa: E402
    has_dangling_surface,
    official_ksa_surface_aliases,
    public_task_object,
)


_NON_TASK_CLAUSE_RE = re.compile(
    r"(?:할\s*수\s*있는|할\s*수\s*있도록|위해\s*노력하는)\s*"
    r"(?:$|(?:(?:관련|적극적|구체적)\s*)?(?:확인·판단\s*기준|판단\s*기준|행동\s*기준|수행·검증\s*절차)$)"
)
_INFINITIVE_END_RE = re.compile(r"(?:하기|시키기|해내기)\s*$")
_UNNATURAL_ACTION_GLUE_RE = re.compile(
    r"(?:을|를)\s*[가-힣·ㆍ]+\s+수행·검증\s*절차$|"
    r"(?:설정|선택|선정|조정|조율|활용|의사결정|포착|수정·보완)\s+수행·검증\s*절차$"
)
_TYPE_MARKERS = {
    "지식": ("기준", "규정", "원리", "요건", "범위", "예외", "판단", "정보", "내용"),
    "기술": ("절차", "방법", "수행", "활용", "조치", "작성", "검증", "점검", "조정", "확인"),
    "태도": ("행동", "기준", "책임", "주의", "소통", "협업", "준수", "대응", "노력"),
}


def default_ncs_db_path() -> Path:
    configured = str(os.getenv("NCS_DB_PATH") or "").strip()
    if configured:
        return Path(configured)
    return ROOT.parent / "NCS_MCP" / "data" / "processed" / "ncs.db"


def iter_ksa_rows(connection: sqlite3.Connection, *, limit: int = 0) -> Iterable[dict[str, str]]:
    sql = """
        SELECT
            u.unit_code,
            COALESCE(NULLIF(u.api_unit_name, ''), NULLIF(u.unit_name_refined, ''), u.unit_name_raw),
            e.element_id,
            COALESCE(NULLIF(e.element_name_refined, ''), NULLIF(e.api_element_name, ''), e.element_name_raw),
            k.ksa_type_name,
            COALESCE(NULLIF(k.ksa_text_refined, ''), k.ksa_text_raw),
            COALESCE(NULLIF(u.api_definition_refined, ''), NULLIF(u.api_definition, ''), '')
        FROM ksa_items AS k
        JOIN competency_elements AS e ON e.element_id = k.element_id
        JOIN competency_units AS u ON u.unit_code = e.unit_code
        ORDER BY u.unit_code, e.element_id, k.ksa_type_code, k.ksa_no, k.ksa_id
    """
    parameters: tuple[int, ...] = ()
    if limit > 0:
        sql += " LIMIT ?"
        parameters = (int(limit),)
    cursor = connection.execute(sql, parameters)
    for unit_code, unit_name, element_id, element_name, ksa_type, factor_name, definition in cursor:
        yield {
            "unit_code": str(unit_code or "").strip(),
            "unit_name": str(unit_name or "").strip(),
            "element_id": str(element_id or "").strip(),
            "element_name": str(element_name or "").strip(),
            "ksa_type": str(ksa_type or "").strip(),
            "factor_name": str(factor_name or "").strip(),
            "competency_definition": str(definition or "").strip(),
        }


def surface_issue_codes(
    row: dict[str, str],
    *,
    surface: str,
    surface_source: str,
) -> list[str]:
    issues: list[str] = []
    factor_name = row.get("factor_name", "")
    ksa_type = row.get("ksa_type", "")
    if not surface:
        return ["empty_surface"]
    if has_dangling_surface(surface):
        issues.append("dangling_surface")
    aliases = official_ksa_surface_aliases(factor_name)
    if any(
        alias
        and (
            surface.strip() == alias.strip()
            or (has_dangling_surface(alias) and alias in surface)
        )
        for alias in aliases
    ):
        issues.append("official_alias_visible")
    if _NON_TASK_CLAUSE_RE.search(surface):
        issues.append("non_task_clause")
    if _INFINITIVE_END_RE.search(surface):
        issues.append("infinitive_surface")
    if _UNNATURAL_ACTION_GLUE_RE.search(surface):
        issues.append("unnatural_action_glue")
    if (
        any(marker in factor_name for marker in ("하고자", "하려는"))
        and re.search(r"[가-힣]실행(?:하는|한)", surface)
    ):
        issues.append("attitude_verb_fusion")
    if surface_source in {"competency_name", "safe_fallback"}:
        issues.append("generic_surface")
    markers = _TYPE_MARKERS.get(ksa_type, ())
    if markers and not any(marker in surface for marker in markers):
        issues.append("weak_type_contract")
    if len(surface) > 80:
        issues.append("surface_too_long")
    return issues


def _example(
    row: dict[str, str],
    *,
    surface: str,
    surface_source: str,
) -> dict[str, str]:
    return {
        "unit_code": row.get("unit_code", ""),
        "unit_name": row.get("unit_name", ""),
        "element_name": row.get("element_name", ""),
        "ksa_type": row.get("ksa_type", ""),
        "factor_name": row.get("factor_name", ""),
        "surface": surface,
        "surface_source": surface_source,
    }


def audit_rows(
    rows: Iterable[dict[str, str]],
    *,
    example_limit: int = 8,
) -> dict[str, Any]:
    row_count = 0
    type_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    issue_signatures: dict[str, Counter[str]] = defaultdict(Counter)
    issue_examples: dict[str, list[dict[str, str]]] = defaultdict(list)
    collision_count = 0
    collision_examples: list[dict[str, Any]] = []
    current_element_key: tuple[str, str] | None = None
    surfaces_in_element: dict[str, set[str]] = {}
    global_surface_factors: dict[str, str | set[str]] = {}
    global_collision_group_count = 0
    global_collision_variant_count = 0
    global_collision_examples: list[dict[str, Any]] = []

    for row in rows:
        row_count += 1
        ksa_type = row.get("ksa_type", "")
        type_counts[ksa_type or "unknown"] += 1
        surface, surface_source = public_task_object(
            factor_name=row.get("factor_name", ""),
            ksa_type=ksa_type,
            element_name=row.get("element_name", ""),
            competency_name=row.get("unit_name", ""),
            competency_definition=row.get("competency_definition", ""),
        )
        source_counts[surface_source] += 1
        for issue in surface_issue_codes(row, surface=surface, surface_source=surface_source):
            issue_counts[issue] += 1
            if issue == "unnatural_action_glue":
                signature_match = re.search(
                    r"([가-힣]+(?:·[가-힣]+)*)\s+수행·검증\s*절차$",
                    surface,
                )
                signature = str(signature_match.group(1) if signature_match else "other")
                issue_signatures[issue][signature] += 1
            if len(issue_examples[issue]) < example_limit:
                issue_examples[issue].append(
                    _example(row, surface=surface, surface_source=surface_source)
                )

        element_key = (row.get("unit_code", ""), row.get("element_id", ""))
        if element_key != current_element_key:
            current_element_key = element_key
            surfaces_in_element = {}
        factor_name = row.get("factor_name", "")
        seen_factors = surfaces_in_element.setdefault(surface, set())
        if factor_name not in seen_factors and seen_factors:
            collision_count += 1
            if len(collision_examples) < example_limit:
                collision_examples.append(
                    {
                        **_example(row, surface=surface, surface_source=surface_source),
                        "previous_factor_name": sorted(seen_factors)[0],
                    }
                )
        seen_factors.add(factor_name)

        global_seen = global_surface_factors.get(surface)
        if global_seen is None:
            global_surface_factors[surface] = factor_name
        elif isinstance(global_seen, str):
            if global_seen != factor_name:
                global_surface_factors[surface] = {global_seen, factor_name}
                global_collision_group_count += 1
                global_collision_variant_count += 1
                if len(global_collision_examples) < example_limit:
                    global_collision_examples.append(
                        {
                            **_example(row, surface=surface, surface_source=surface_source),
                            "previous_factor_name": global_seen,
                        }
                    )
        elif factor_name not in global_seen:
            global_seen.add(factor_name)
            global_collision_variant_count += 1

    issue_counts["same_element_surface_collision"] = collision_count
    if collision_examples:
        issue_examples["same_element_surface_collision"] = collision_examples
    strict_issue_names = (
        "empty_surface",
        "dangling_surface",
        "official_alias_visible",
        "non_task_clause",
        "infinitive_surface",
        "unnatural_action_glue",
        "attitude_verb_fusion",
    )
    strict_issue_total = sum(issue_counts[name] for name in strict_issue_names)
    return {
        "policy": "ncs_ksa_question_surface_audit_v1",
        "summary": {
            "row_count": row_count,
            "strict_issue_total": strict_issue_total,
            "strict_issue_rate": round(strict_issue_total / max(1, row_count), 6),
            "collision_count": collision_count,
            "global_collision_group_count": global_collision_group_count,
            "global_collision_variant_count": global_collision_variant_count,
            "generic_surface_count": issue_counts["generic_surface"],
        },
        "ksa_type_counts": dict(sorted(type_counts.items())),
        "surface_source_counts": dict(sorted(source_counts.items())),
        "issue_counts": dict(sorted(issue_counts.items())),
        "issue_signatures": {
            issue: dict(counter.most_common())
            for issue, counter in sorted(issue_signatures.items())
        },
        "examples": dict(sorted(issue_examples.items())),
        "global_collision_examples": global_collision_examples,
    }


def audit_database(db_path: Path, *, limit: int = 0, example_limit: int = 8) -> dict[str, Any]:
    absolute = db_path.expanduser().resolve()
    if not absolute.is_file():
        raise FileNotFoundError(f"NCS DB not found: {absolute}")
    uri = f"file:{absolute.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        report = audit_rows(iter_ksa_rows(connection, limit=limit), example_limit=example_limit)
    report["database"] = str(absolute)
    report["limit"] = max(0, int(limit))
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Audit candidate-facing task surfaces against the official NCS KSA corpus."
    )
    parser.add_argument("--db", type=Path, default=default_ncs_db_path())
    parser.add_argument("--limit", type=int, default=0, help="0 audits all KSA rows")
    parser.add_argument("--examples", type=int, default=8)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--fail-on-strict", action="store_true")
    args = parser.parse_args()
    report = audit_database(
        args.db,
        limit=max(0, int(args.limit)),
        example_limit=max(1, int(args.examples)),
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        output_path = args.output.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    if args.fail_on_strict and int(report["summary"]["strict_issue_total"]) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
