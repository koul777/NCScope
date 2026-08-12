from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import SUPPORTED_INTERVIEW_METHODS, _attach_ksa_evidence_to_strategy  # noqa: E402
from scripts.benchmark_question_method_matrix import (  # noqa: E402
    build_template_question,
    default_ncs_db_path,
    evidence_row,
)


DEFAULT_NCS_CODE = "0101010205_17v2"
DEFAULT_FACTOR = "승인된 변경에 대한 지식"


def load_target_row(db_path: Path, *, ncs_code: str, factor_name: str) -> dict[str, str]:
    absolute = db_path.expanduser().resolve()
    if not absolute.is_file():
        raise FileNotFoundError(f"NCS DB not found: {absolute}")
    uri = f"file:{absolute.as_posix()}?mode=ro"
    query = """
        SELECT
            c.major_code,
            c.major_name,
            c.sub_name,
            u.unit_code,
            COALESCE(NULLIF(u.api_unit_name, ''), NULLIF(u.unit_name_refined, ''), u.unit_name_raw) AS unit_name,
            COALESCE(NULLIF(u.api_definition_refined, ''), NULLIF(u.api_definition, ''), '') AS unit_definition,
            COALESCE(NULLIF(e.element_name_refined, ''), NULLIF(e.api_element_name, ''), e.element_name_raw) AS element_name,
            k.ksa_type_name,
            COALESCE(NULLIF(k.ksa_text_refined, ''), k.ksa_text_raw) AS factor_name,
            k.ksa_no
        FROM ksa_items AS k
        JOIN competency_elements AS e ON e.element_id = k.element_id
        JOIN competency_units AS u ON u.unit_code = e.unit_code
        JOIN classifications AS c ON c.classification_id = u.classification_id
        WHERE u.unit_code = ?
          AND REPLACE(TRIM(COALESCE(NULLIF(k.ksa_text_refined, ''), k.ksa_text_raw)), ' ', '')
              = REPLACE(TRIM(?), ' ', '')
        ORDER BY e.element_id, k.ksa_id
        LIMIT 1
    """
    with sqlite3.connect(uri, uri=True) as connection:
        result = connection.execute(query, (ncs_code.strip(), factor_name.strip())).fetchone()
    if result is None:
        raise LookupError(
            f"NCS KSA not found: ncs_code={ncs_code!r}, factor_name={factor_name!r}"
        )
    keys = (
        "major_code",
        "major_name",
        "sub_name",
        "unit_code",
        "unit_name",
        "unit_definition",
        "element_name",
        "ksa_type",
        "factor_name",
        "ksa_no",
    )
    return dict(zip(keys, (str(value or "").strip() for value in result)))


def build_showcase(
    row: dict[str, str],
    *,
    methods: Iterable[str] = SUPPORTED_INTERVIEW_METHODS,
) -> dict[str, Any]:
    method_list = [str(method).strip() for method in methods if str(method).strip()]
    evidence = evidence_row(row)
    questions: list[dict[str, Any]] = []

    for variation_index, method in enumerate(method_list):
        draft = build_template_question(row, method, variation_index=variation_index)
        strategy = _attach_ksa_evidence_to_strategy(
            {
                "interview_questions": [draft],
                "question_plan_used": {"total_main_count": 1},
            },
            [evidence],
        )
        evaluated = strategy["interview_questions"][0]
        quality = strategy["question_quality_report"]["items"][0]
        questions.append(
            {
                "method": method,
                "candidate_view": {
                    "question": evaluated.get("question", ""),
                    "follow_ups": list(evaluated.get("follow_ups") or []),
                    "task_conditions": dict(evaluated.get("task_conditions") or {}),
                },
                "interviewer_view": {
                    "evaluation_points": list(evaluated.get("evaluation_points") or []),
                    "assessment_guide": dict(evaluated.get("assessment_guide") or {}),
                },
                "traceability": {
                    "ncs_code": evaluated.get("ncsClCd", ""),
                    "competency": evaluated.get("competency", ""),
                    "public_task_object": evaluated.get("question_focus_surface", ""),
                    "evidence_id": evaluated.get("question_evidence_id", ""),
                },
                "quality": {
                    "ready": bool(quality.get("ready")),
                    "score": float(quality.get("score") or 0.0),
                    "issues": list(quality.get("issues") or []),
                    "checks": dict(quality.get("checks") or {}),
                    "check_statuses": dict(quality.get("check_statuses") or {}),
                },
            }
        )

    ready_count = sum(1 for item in questions if item["quality"]["ready"])
    linked_evidence_id = str(
        questions[0]["traceability"].get("evidence_id") if questions else ""
    ).strip()
    return {
        "policy": "ncs_exact_ksa_question_showcase_v1",
        "source": {
            "system": "NCS_MCP serving DB (read-only)",
            "ncs_code": row.get("unit_code", ""),
            "competency": row.get("unit_name", ""),
            "classification": row.get("sub_name", ""),
            "element": row.get("element_name", ""),
            "official_factor": row.get("factor_name", ""),
            "ksa_type": row.get("ksa_type", ""),
            "ksa_no": row.get("ksa_no", ""),
            "evidence_id": linked_evidence_id,
        },
        "separation_policy": {
            "official_factor_visibility": "internal_traceability_only",
            "candidate_surface": "operationalized_public_task_object",
            "operating_conditions": "separate_from_substantive_question",
        },
        "summary": {
            "method_count": len(method_list),
            "ready_count": ready_count,
            "failed_count": len(method_list) - ready_count,
            "passed": bool(method_list and ready_count == len(method_list)),
        },
        "questions": questions,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Export a human-reviewable question bundle for one exact official NCS KSA."
    )
    parser.add_argument("--db", type=Path, default=default_ncs_db_path())
    parser.add_argument("--ncs-code", default=DEFAULT_NCS_CODE)
    parser.add_argument("--factor", default=DEFAULT_FACTOR)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    row = load_target_row(args.db, ncs_code=args.ncs_code, factor_name=args.factor)
    report = build_showcase(row)
    report["database"] = str(args.db.expanduser().resolve())
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        output_path = args.output.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    if args.strict and not report["summary"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
