from __future__ import annotations

from typing import Any, Mapping


AX_CRITERIA = (
    ("ready_asset", "ready", "준비 자산이 실제로 붙어 있다"),
    ("enabled_output", "enabled", "검증 가능한 업무 산출물이 있다"),
    ("enabled_review", "enabled", "사람 검토와 책임 경계가 작동한다"),
    ("first_redesign", "first", "업무 시작점·처리경로·역할을 재설계했다"),
    ("first_auto", "first", "저위험 건이 자동 처리된다"),
    ("first_escalation", "first", "고위험·불확실 건이 숙련자에게 이관된다"),
    ("first_exception", "first", "승인·거부·이의·롤백 경로가 작동한다"),
    ("first_metrics", "first", "운영 결과를 건별 지표로 측정한다"),
    ("first_feedback", "first", "반복 예외가 기준과 제도를 바꾼다"),
    ("native_core", "native", "AI가 서비스 핵심 가치와 결합되어 있다"),
    ("native_loop", "native", "평가→개선→재검증 환류가 닫혀 있다"),
    ("native_resilience", "native", "대체 경로와 장애복원을 훈련했다"),
    ("native_scope", "native", "적용 범위·SLA·감사 책임을 기록했다"),
)

_GROUP_LABELS = {
    "ready": "AI-Ready",
    "enabled": "AI-Enabled",
    "first": "AI-First",
    "native": "AI-Native",
}


def assess_ax_readiness(signals: Mapping[str, Any] | None) -> dict[str, Any]:
    """Apply AX non-additive evidence gates without inflating pilot work."""
    source = signals if isinstance(signals, Mapping) else {}
    items: list[dict[str, Any]] = []
    for criterion_id, group, title in AX_CRITERIA:
        evidence = bool(source.get(f"{criterion_id}_evidence"))
        pilot = bool(source.get(f"{criterion_id}_pilot"))
        status = "evidence" if evidence else "pilot" if pilot else "unknown"
        items.append(
            {
                "id": criterion_id,
                "group": group,
                "title": title,
                "status": status,
                "verified": evidence,
                "evidence_ref": str(source.get(f"{criterion_id}_ref") or "").strip(),
            }
        )

    cumulative: list[str] = []
    base_stage = "미착수"
    for group in ("ready", "enabled", "first", "native"):
        cumulative.append(group)
        required = [item for item in items if item["group"] in cumulative]
        if required and all(item["verified"] for item in required):
            base_stage = _GROUP_LABELS[group]
        else:
            break

    verified = sum(bool(item["verified"]) for item in items)
    missing = [item["id"] for item in items if not item["verified"]]
    return {
        "base_stage": base_stage,
        "verified_count": verified,
        "criterion_count": len(items),
        "evidence_rate": round(verified / max(1, len(items)), 4),
        "all_required_gates": base_stage == "AI-Native",
        "items": items,
        "missing_gates": missing,
        "principle": "합산 점수가 아니라 누적 필수 관문 전부에 운영 증거가 있어야 단계가 올라갑니다.",
    }


__all__ = ["AX_CRITERIA", "assess_ax_readiness"]
