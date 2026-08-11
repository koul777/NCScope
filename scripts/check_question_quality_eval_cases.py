from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from uuid import uuid4
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.repository import list_question_quality_eval_cases  # noqa: E402
from app.services.question_quality_ops import feedback_prompt_context  # noqa: E402


def validate_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for case in cases:
        case_id = case.get("id")
        case_type = str(case.get("case_type") or "").strip()
        expected = str(case.get("expected_decision") or "").strip()
        verdict = str(case.get("verdict") or "").strip()
        question = str(case.get("question_text") or "").strip()
        issues = [str(code).strip() for code in (case.get("issue_codes") or []) if str(code).strip()]
        if not question:
            failures.append({"id": case_id, "reason": "missing_question_text"})
            continue
        if case_type == "golden":
            if expected != "pass" or verdict != "approve":
                failures.append({"id": case_id, "reason": "golden_review_contract_mismatch"})
            continue
        if case_type not in {"negative", "regression"}:
            failures.append({"id": case_id, "reason": "unsupported_case_type"})
            continue
        if expected != "fail" or verdict not in {"reject", "needs_edit"} or not issues:
            failures.append({"id": case_id, "reason": "negative_review_contract_mismatch"})
            continue
        context = feedback_prompt_context([case], str(case.get("ncs_code") or ""), max_items=1)
        if question not in context:
            failures.append({"id": case_id, "reason": "negative_case_not_in_feedback_loop"})
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate promoted operational question-quality eval cases.")
    parser.add_argument("--report-dir", default=str(ROOT / "reports" / "question_quality_loop"))
    args = parser.parse_args()

    cases = list_question_quality_eval_cases(active_only=True)
    failures = validate_cases(cases)
    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')}-{uuid4().hex[:8]}"
    report_path = out_dir / f"feedback-eval-{stamp}.json"
    payload = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "active_cases": len(cases),
        "passed_cases": len(cases) - len(failures),
        "failures": failures,
        "status": "passed" if not failures else "failed",
        "note": "No active cases is a valid bootstrap state; promoted cases become mandatory thereafter.",
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report={report_path}")
    print(f"active_cases={len(cases)} failures={len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
