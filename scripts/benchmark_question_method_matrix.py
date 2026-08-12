from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import (  # noqa: E402
    SUPPORTED_INTERVIEW_METHODS,
    _attach_ksa_evidence_to_strategy,
    _behavior_anchored_evaluation,
    _followups_for_method,
    _method_evaluation_points,
    _question_for_method,
    _question_task_frame,
    _task_conditions_for_method,
)
from app.services.jd_strategy import normalize_question_dedup_key  # noqa: E402
from app.services.question_quality_orchestrator import evaluate_ksa_measurement  # noqa: E402


def default_ncs_db_path() -> Path:
    configured = str(os.getenv("NCS_DB_PATH") or "").strip()
    if configured:
        return Path(configured)
    return ROOT.parent / "NCS_MCP" / "data" / "processed" / "ncs.db"


def load_stratified_rows(
    db_path: Path,
    *,
    per_type_per_major: int = 1,
    max_major_groups: int = 24,
) -> list[dict[str, str]]:
    absolute = db_path.expanduser().resolve()
    if not absolute.is_file():
        raise FileNotFoundError(f"NCS DB not found: {absolute}")
    uri = f"file:{absolute.as_posix()}?mode=ro"
    query = """
        WITH ranked AS (
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
                k.ksa_no,
                ROW_NUMBER() OVER (
                    PARTITION BY c.major_code, k.ksa_type_name
                    ORDER BY ABS(LENGTH(COALESCE(NULLIF(k.ksa_text_refined, ''), k.ksa_text_raw)) - 26),
                             u.unit_code, e.element_id, k.ksa_id
                ) AS type_rank,
                DENSE_RANK() OVER (ORDER BY c.major_code) AS major_rank
            FROM ksa_items AS k
            JOIN competency_elements AS e ON e.element_id = k.element_id
            JOIN competency_units AS u ON u.unit_code = e.unit_code
            JOIN classifications AS c ON c.classification_id = u.classification_id
            WHERE TRIM(COALESCE(NULLIF(k.ksa_text_refined, ''), k.ksa_text_raw)) <> ''
              AND k.ksa_type_name IN ('지식', '기술', '태도')
        )
        SELECT major_code, major_name, sub_name, unit_code, unit_name, unit_definition,
               element_name, ksa_type_name, factor_name, ksa_no
        FROM ranked
        WHERE type_rank <= ? AND major_rank <= ?
        ORDER BY major_code, ksa_type_name, type_rank, unit_code
    """
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            query,
            (max(1, int(per_type_per_major)), max(1, int(max_major_groups))),
        ).fetchall()
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
    return [dict(zip(keys, (str(value or "").strip() for value in row))) for row in rows]


def evidence_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "ncsClCd": row.get("unit_code", ""),
        "compeUnitName": row.get("unit_name", ""),
        "ncsSubdCdnm": row.get("sub_name", ""),
        "elementName": row.get("element_name", ""),
        "factorName": row.get("factor_name", ""),
        "ksaTypeName": row.get("ksa_type", ""),
        "ksaNo": row.get("ksa_no", ""),
        "factorSource": "ncs-mcp",
        "source": "ncs-mcp",
        "ksaStatus": "official",
        "isOfficialKsa": True,
    }


def build_template_question(
    row: dict[str, str],
    method: str,
    *,
    variation_index: int = 0,
) -> dict[str, Any]:
    evidence = evidence_row(row)
    focus = row.get("factor_name", "")
    focus_type = row.get("ksa_type", "")
    subject = row.get("unit_name", "")
    detail = row.get("sub_name", "") or row.get("major_name", "")
    definition = row.get("unit_definition", "")
    frame = _question_task_frame(
        focus=focus,
        focus_type=focus_type,
        subject=subject,
        detail=detail,
        comp_def=definition,
        evidence_row=evidence,
    )
    evaluation_points = _method_evaluation_points(
        method,
        [focus],
        focus_type,
        surface_focus=frame["task_object"],
    )
    return {
        "type": method,
        "method": method,
        "competency": subject,
        "ncsClCd": row.get("unit_code", ""),
        "ncs_detail": detail,
        "ncsSclasCdnm": detail,
        "compeUnitDef": definition,
        "question_focus": focus,
        "question_focus_surface": frame["task_object"],
        "question_focus_type": focus_type,
        "question_task_frame": frame,
        "question_evidence_id": frame["evidence_id"],
        "question_evidence_required": True,
        "question": _question_for_method(
            method=method,
            subject=subject,
            focus=focus,
            detail=detail,
            comp_def=definition,
            focus_type=focus_type,
            variation_index=variation_index,
            task_frame=frame,
        ),
        "follow_ups": _followups_for_method(
            method=method,
            subject=subject,
            focus=focus,
            count=3,
            variant_index=variation_index,
            focus_type=focus_type,
            task_frame=frame,
        ),
        "evaluation_points": evaluation_points,
        "task_conditions": _task_conditions_for_method(
            method=method,
            subject=subject,
            focus=focus,
            detail=detail,
            comp_def=definition,
            focus_type=focus_type,
            variation_index=variation_index,
        ),
        "question_variation_index": variation_index,
        "assessment_guide": _behavior_anchored_evaluation(
            method,
            focus,
            evaluation_points,
            focus_type,
            surface_focus=frame["task_object"],
        ),
        "ksa_refs": [focus],
        "ksa_evidence": [evidence],
        "question_source": "matrix_template",
        "model_question_preserved": False,
    }


_IDEMPOTENCE_FIELDS = (
    "question",
    "follow_ups",
    "evaluation_points",
    "question_focus",
    "question_focus_surface",
    "question_focus_type",
    "question_evidence_id",
    "task_conditions",
    "assessment_guide",
)


def _snapshot(question: dict[str, Any]) -> str:
    return json.dumps(
        {field: question.get(field) for field in _IDEMPOTENCE_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def benchmark_rows(
    rows: Iterable[dict[str, str]],
    *,
    methods: Iterable[str] = SUPPORTED_INTERVIEW_METHODS,
    example_limit: int = 12,
) -> dict[str, Any]:
    method_list = [str(method).strip() for method in methods if str(method).strip()]
    row_list = [dict(row) for row in rows]
    issue_counts: Counter[str] = Counter()
    result_counts: Counter[str] = Counter()
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    failures: list[dict[str, Any]] = []
    samples: dict[str, dict[str, str]] = {}
    surface_keys: set[str] = set()
    surface_duplicate_count = 0
    score_total = 0.0
    idempotence_failures = 0

    for row_index, row in enumerate(row_list):
        for method_index, method in enumerate(method_list):
            question = build_template_question(
                row,
                method,
                variation_index=row_index * max(1, len(method_list)) + method_index,
            )
            strategy = {
                "interview_questions": [copy.deepcopy(question)],
                "question_plan_used": {"total_main_count": 1},
            }
            evaluated = _attach_ksa_evidence_to_strategy(strategy, [evidence_row(row)])
            evaluated_question = evaluated["interview_questions"][0]
            quality = evaluated["question_quality_report"]["items"][0]
            measurement = evaluate_ksa_measurement(evaluated_question)
            ready = bool(quality.get("ready"))
            result_counts["total"] += 1
            result_counts["ready" if ready else "failed"] += 1
            group_key = f"{method}|{row.get('ksa_type', '')}"
            grouped[group_key]["total"] += 1
            grouped[group_key]["ready" if ready else "failed"] += 1
            score_total += float(quality.get("score") or 0.0)
            for issue in quality.get("issues") or []:
                issue_counts[str(issue)] += 1

            second_pass = _attach_ksa_evidence_to_strategy(
                {
                    "interview_questions": [copy.deepcopy(evaluated_question)],
                    "question_plan_used": {"total_main_count": 1},
                },
                [evidence_row(row)],
            )["interview_questions"][0]
            idempotent = _snapshot(evaluated_question) == _snapshot(second_pass)
            if not idempotent:
                idempotence_failures += 1

            surface_key = normalize_question_dedup_key(str(evaluated_question.get("question") or ""))
            if surface_key in surface_keys:
                surface_duplicate_count += 1
            elif surface_key:
                surface_keys.add(surface_key)

            sample_key = f"{method}|{row.get('ksa_type', '')}"
            samples.setdefault(
                sample_key,
                {
                    "unit_code": row.get("unit_code", ""),
                    "factor_name": row.get("factor_name", ""),
                    "surface": str(evaluated_question.get("question_focus_surface") or ""),
                    "question": str(evaluated_question.get("question") or ""),
                },
            )
            if (not ready or not idempotent) and len(failures) < example_limit:
                failures.append(
                    {
                        "method": method,
                        "ksa_type": row.get("ksa_type", ""),
                        "unit_code": row.get("unit_code", ""),
                        "factor_name": row.get("factor_name", ""),
                        "surface": evaluated_question.get("question_focus_surface", ""),
                        "issues": list(quality.get("issues") or []),
                        "measurement_issues": list(measurement.get("issues") or []),
                        "measurement_checks": dict(measurement.get("checks") or {}),
                        "quality_checks": dict(quality.get("checks") or {}),
                        "observation_group_hits": list(measurement.get("observation_group_hits") or []),
                        "idempotent": idempotent,
                        "question": evaluated_question.get("question", ""),
                        "follow_ups": list(evaluated_question.get("follow_ups") or []),
                        "evaluation_points": list(evaluated_question.get("evaluation_points") or []),
                    }
                )

    total = int(result_counts["total"])
    return {
        "policy": "ncs_question_method_matrix_v1",
        "summary": {
            "source_row_count": len(row_list),
            "method_count": len(method_list),
            "question_count": total,
            "ready_count": int(result_counts["ready"]),
            "failed_count": int(result_counts["failed"]),
            "ready_rate": round(result_counts["ready"] / max(1, total), 6),
            "average_quality_score": round(score_total / max(1, total), 6),
            "idempotence_failure_count": idempotence_failures,
            "exact_surface_duplicate_count": surface_duplicate_count,
            "passed": bool(
                total
                and result_counts["failed"] == 0
                and idempotence_failures == 0
                and surface_duplicate_count == 0
            ),
        },
        "methods": method_list,
        "issue_counts": dict(sorted(issue_counts.items())),
        "group_results": {
            key: dict(sorted(value.items())) for key, value in sorted(grouped.items())
        },
        "failures": failures,
        "samples": dict(sorted(samples.items())),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Benchmark deterministic NCS KSA questions across every supported interview method."
    )
    parser.add_argument("--db", type=Path, default=default_ncs_db_path())
    parser.add_argument("--per-type-per-major", type=int, default=1)
    parser.add_argument("--max-major-groups", type=int, default=24)
    parser.add_argument("--examples", type=int, default=12)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    rows = load_stratified_rows(
        args.db,
        per_type_per_major=max(1, int(args.per_type_per_major)),
        max_major_groups=max(1, int(args.max_major_groups)),
    )
    report = benchmark_rows(rows, example_limit=max(1, int(args.examples)))
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
