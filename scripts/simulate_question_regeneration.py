from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from app.main import (  # noqa: E402
    QUALITY_INTERVIEW_METHODS,
    _behavior_anchored_evaluation,
    _followups_for_method,
    _method_evaluation_points,
    _question_for_method,
    _run_runtime_question_quality_orchestration,
    _task_conditions_for_method,
)
from app.services.question_quality_orchestrator import (  # noqa: E402
    RUNTIME_QUESTION_ORCHESTRATION_POLICY,
    evaluate_ksa_measurement,
    is_history_duplicate,
)


DEFAULT_REPORT_DIR = ROOT / "reports" / "question_quality_simulation"
FOCUSES = {
    "지식": "문서 보안 법규 지식",
    "기술": "문서 오류 검증 기술",
    "태도": "정확성을 우선하려는 태도",
}


def _deterministic_review_policy_satisfied(
    metadata: dict[str, Any],
    report: dict[str, Any],
    *,
    question_count: int,
) -> bool:
    """Return whether deterministic output was safely held for human review.

    These simulations intentionally exercise the rule/template generation path.
    Such output may still satisfy the structural KSA audit, but it must not be
    promoted to interview-ready without the field-realism gate.
    """
    report_items = [
        item for item in (report.get("items") or []) if isinstance(item, dict)
    ]
    metadata_items = [
        item for item in (metadata.get("items") or []) if isinstance(item, dict)
    ]
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return bool(
        question_count > 0
        and metadata.get("status") == "needs_review"
        and metadata.get("quality_report_passed") is False
        and int(metadata.get("question_count_gap") or 0) == 0
        and int(metadata.get("repair_error_count") or 0) == 0
        and int(metadata.get("full_quality_unresolved_count") or 0) == question_count
        and report.get("passed") is False
        and int(summary.get("ready_count") or 0) == 0
        and int(summary.get("needs_review_count") or 0) == question_count
        and len(report_items) == question_count
        and all(
            item.get("ready") is False
            and "field_realism" in (item.get("issues") or [])
            and (item.get("check_statuses") or {}).get("field_realism") == "fail"
            for item in report_items
        )
        and len(metadata_items) == question_count
        and all(
            set(item.get("final_issues") or []) == {"full_quality_field_realism"}
            for item in metadata_items
        )
    )


def _candidate(method: str, focus_type: str, focus: str, *, shallow: bool) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    code = "SIM-REGEN-001"
    subject = "문서관리"
    detail = "사무행정"
    definition = "문서 오류를 확인하고 법규와 처리 기준에 따라 정확하게 관리한다."
    evaluation_points = _method_evaluation_points(method, [focus], focus_type)
    question_text = (
        f"'{focus}'과 관련하여 실제 경험이 있으십니까? 말씀해 주세요."
        if shallow
        else _question_for_method(
            method,
            subject,
            focus,
            detail,
            definition,
            focus_type,
            variation_index=0,
        )
    )
    question = {
        "type": method,
        "method": method,
        "ncsClCd": code,
        "competency": subject,
        "ncs_detail": detail,
        "ncsSubdCdnm": detail,
        "compeUnitDef": definition,
        "question_focus": focus,
        "question_focus_type": focus_type,
        "question_focus_source": "official_ksa",
        "ksa_refs": [focus],
        "question": question_text,
        "follow_ups": _followups_for_method(
            method,
            subject,
            focus,
            3,
            focus_type=focus_type,
        ),
        "evaluation_points": evaluation_points,
        "question_source": "simulation_candidate",
        "model_question_preserved": False,
        "task_conditions": _task_conditions_for_method(
            method,
            subject,
            focus,
            detail,
            definition,
        ),
        "assessment_guide": _behavior_anchored_evaluation(
            method,
            focus,
            evaluation_points,
            focus_type,
        ),
    }
    plan = {"total_main_count": 1, "follow_up_count": 3}
    strategy = {
        "interview_questions": [question],
        "question_plan_used": dict(plan),
    }
    ksa = [
        {
            "ncsClCd": code,
            "compeUnitName": subject,
            "factorName": focus,
            "factorSource": "ncs-mcp",
            "ksaStatus": "official",
            "ksaTypeName": focus_type,
        }
    ]
    return strategy, plan, ksa


def run_simulation(
    *,
    cycles: int = 30,
    history_window: int = 500,
    methods: list[str] | None = None,
    focus_types: list[str] | None = None,
) -> dict[str, Any]:
    cycle_count = max(3, int(cycles))
    window = max(1, int(history_window))
    selected_methods = [method for method in (methods or QUALITY_INTERVIEW_METHODS) if method in QUALITY_INTERVIEW_METHODS]
    selected_focus_types = [kind for kind in (focus_types or list(FOCUSES)) if kind in FOCUSES]
    if not selected_methods:
        raise ValueError("at least one supported interview method is required")
    if not selected_focus_types:
        raise ValueError("at least one KSA focus type is required")
    cases: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    total_repairs = 0
    total_review_required = 0

    for method in selected_methods:
        for focus_type in selected_focus_types:
            focus = FOCUSES[focus_type]
            history: list[str] = []
            generated: list[str] = []
            repaired_cycles = 0
            review_required_cycles = 0
            case_failures: list[dict[str, Any]] = []
            first_question = ""
            last_question = ""

            for cycle in range(1, cycle_count + 1):
                strategy, plan, ksa = _candidate(
                    method,
                    focus_type,
                    focus,
                    shallow=cycle == 1,
                )
                result = _run_runtime_question_quality_orchestration(
                    strategy,
                    question_plan=plan,
                    ncs_ksa=ksa,
                    avoid_questions=history[-window:],
                    generation_offset=cycle - 1,
                )
                questions = [
                    item
                    for item in (result.get("interview_questions") or [])
                    if isinstance(item, dict)
                ]
                metadata = result.get("question_quality_orchestration") or {}
                report = result.get("question_quality_report") or {}
                question = str(questions[0].get("question") or "").strip() if questions else ""
                measurement = evaluate_ksa_measurement(questions[0]) if questions else {"passed": False, "issues": ["empty_result"]}
                duplicated = is_history_duplicate(question, history) if question else False
                cycle_issues: list[str] = []
                if len(questions) != 1:
                    cycle_issues.append("question_count")
                held_for_review = _deterministic_review_policy_satisfied(
                    metadata,
                    report,
                    question_count=len(questions),
                )
                if held_for_review:
                    review_required_cycles += 1
                    total_review_required += 1
                else:
                    cycle_issues.append("deterministic_review_policy")
                if measurement.get("passed") is not True:
                    cycle_issues.extend(f"ksa_{issue}" for issue in (measurement.get("issues") or ["measurement"]))
                if duplicated:
                    cycle_issues.append("history_duplicate")
                if not question:
                    cycle_issues.append("empty_question")

                repaired = int(metadata.get("repaired_count") or 0)
                repaired_cycles += repaired
                total_repairs += repaired
                if cycle_issues:
                    failure = {
                        "method": method,
                        "focus_type": focus_type,
                        "cycle": cycle,
                        "issues": list(dict.fromkeys(cycle_issues)),
                        "metadata": metadata,
                    }
                    case_failures.append(failure)
                    failures.append(failure)
                if question:
                    generated.append(question)
                    history.append(question)
                first_question = first_question or question
                last_question = question

            cases.append(
                {
                    "method": method,
                    "focus_type": focus_type,
                    "focus": focus,
                    "cycles": cycle_count,
                    "generated_count": len(generated),
                    "unique_count": len(set(generated)),
                    "repaired_cycles": repaired_cycles,
                    "review_required_cycles": review_required_cycles,
                    "failure_count": len(case_failures),
                    "first_question": first_question,
                    "last_question": last_question,
                }
            )

    total_cycles = len(cases) * cycle_count
    return {
        "schema_version": "question_regeneration_simulation_v2",
        "runtime_policy": RUNTIME_QUESTION_ORCHESTRATION_POLICY,
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "passed" if not failures else "failed",
        "cycles_per_case": cycle_count,
        "history_window": window,
        "case_count": len(cases),
        "method_count": len(selected_methods),
        "focus_type_count": len(selected_focus_types),
        "total_cycles": total_cycles,
        "total_repairs": total_repairs,
        "review_required_count": total_review_required,
        "failure_count": len(failures),
        "invariants": {
            "no_empty_result": not any("empty_question" in failure["issues"] for failure in failures),
            "no_history_duplicate": not any("history_duplicate" in failure["issues"] for failure in failures),
            "all_ksa_measurement_gates_passed": not any(
                any(issue.startswith("ksa_") for issue in failure["issues"])
                for failure in failures
            ),
            "deterministic_outputs_require_review": (
                total_review_required == total_cycles
                and not any(
                    "deterministic_review_policy" in failure["issues"]
                    for failure in failures
                )
            ),
        },
        "cases": cases,
        "failures": failures,
    }


def write_report(result: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}-{uuid4().hex[:8]}"
    json_path = report_dir / f"regeneration-simulation-{stamp}.json"
    markdown_path = report_dir / f"regeneration-simulation-{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 면접 문항 반복 생성 시뮬레이션",
        "",
        f"- 상태: **{result['status']}**",
        f"- 런타임 정책: `{result['runtime_policy']}`",
        f"- 조합: {result['case_count']}개 ({result['method_count']}개 면접기법 × {result['focus_type_count']}개 KSA 유형)",
        f"- 조합별 반복: {result['cycles_per_case']}회",
        f"- 총 생성 사이클: {result['total_cycles']}회",
        f"- 자동 보정: {result['total_repairs']}회",
        f"- 현장성 검토 필요: {result['review_required_count']}회",
        f"- 실패: {result['failure_count']}회",
        f"- 이력 창: 최근 {result['history_window']}개",
        "",
        "## 운영 불변조건",
        "",
    ]
    for name, passed in result["invariants"].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — `{name}`")
    lines.extend(
        [
            "",
            "## 조합별 결과",
            "",
            "| 면접기법 | KSA | 반복 | 고유 | 보정 | 검토 필요 | 실패 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in result["cases"]:
        lines.append(
            f"| {case['method']} | {case['focus_type']} | {case['cycles']} | "
            f"{case['unique_count']} | {case['repaired_cycles']} | "
            f"{case['review_required_cycles']} | {case['failure_count']} |"
        )
    lines.extend(["", "## 사람이 확인할 대표 문항", ""])
    for case in result["cases"]:
        lines.extend(
            [
                f"<details><summary>{case['method']} / {case['focus_type']}</summary>",
                "",
                f"- 첫 문항: {case['first_question'] or '-'}",
                f"- 마지막 문항: {case['last_question'] or '-'}",
                "",
                "</details>",
                "",
            ]
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate repeated KSA interview-question generation.")
    parser.add_argument("--cycles", type=int, default=30)
    parser.add_argument("--history-window", type=int, default=500)
    parser.add_argument("--method", action="append", choices=list(QUALITY_INTERVIEW_METHODS))
    parser.add_argument("--focus-type", action="append", choices=list(FOCUSES))
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_simulation(
        cycles=args.cycles,
        history_window=args.history_window,
        methods=args.method,
        focus_types=args.focus_type,
    )
    json_path, markdown_path = write_report(result, args.report_dir)
    print(json.dumps({
        "status": result["status"],
        "total_cycles": result["total_cycles"],
        "total_repairs": result["total_repairs"],
        "failure_count": result["failure_count"],
        "json_report": str(json_path),
        "markdown_report": str(markdown_path),
    }, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
