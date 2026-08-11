from __future__ import annotations

import hashlib
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import desc, func, select, text

from app.db import SessionLocal
from app.models import (
    AuditLog,
    Attachment,
    Branch,
    DocumentParsed,
    DocumentRaw,
    IngestRun,
    Institution,
    JobPosting,
    MatchResult,
    NcsSyncRun,
    NcsUnit,
    QuestionQualityEvalCase,
    QuestionQualityReview,
    QuestionQualityRun,
    UserProfile,
)


class QuestionQualityReviewConflictError(RuntimeError):
    """Raised when a review mutation targets a decision that is no longer active."""


@contextmanager
def db_session() -> Iterator:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _inst_code_from_name(name: str) -> str:
    h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"INST_{h}"


def _lock_question_quality_run(session: Any, run_id: str) -> QuestionQualityRun | None:
    """Serialize review mutations for one run across threads and workers.

    SQLite ignores ``SELECT ... FOR UPDATE``.  ``BEGIN IMMEDIATE`` obtains its
    single writer reservation before reading active reviews, so concurrent
    review/rollback requests cannot both make a decision from stale state.
    Databases with row-lock support use ``FOR UPDATE`` on the run instead.
    """

    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))
        return session.get(QuestionQualityRun, run_id)
    return session.execute(
        select(QuestionQualityRun)
        .where(QuestionQualityRun.id == run_id)
        .with_for_update()
    ).scalar_one_or_none()


def create_posting(payload: dict, requirements_by_attachment: dict[str, list[dict]]) -> dict:
    with db_session() as s:
        inst = s.execute(select(Institution).where(Institution.inst_name == payload["institution_name"])).scalar_one_or_none()
        if not inst:
            inst = Institution(
                inst_cd=_inst_code_from_name(payload["institution_name"]),
                inst_name=payload["institution_name"],
                inst_type_code=payload["a2000"],
                supervising_ministry_code=payload["a1000"],
                region_code=payload["region_code"],
            )
            s.add(inst)
            s.flush()

        posting = JobPosting(
            posting_external_id=f"LOCAL_{uuid.uuid4().hex[:16]}",
            institution_id=inst.id,
            title=payload["title"],
            description_raw="",
            employment_type_code=payload["r1000"],
            recruitment_type_code=payload["r2000"],
            region_code=payload["region_code"],
            ncs_lclass_code=payload["r6000"],
            education_code=payload["r7000"],
            status="active",
        )
        s.add(posting)
        s.flush()

        created_attachments = []
        for at in payload["attachments"]:
            att = Attachment(
                job_posting_id=posting.id,
                attachment_external_id=f"ATT_{uuid.uuid4().hex[:16]}",
                atch_type=at["atch_type"],
                file_name=at["file_name"],
                file_ext=at["file_name"].split(".")[-1] if "." in at["file_name"] else "",
                file_size=len(at.get("content_text", "").encode("utf-8")),
                download_status="inline_loaded",
            )
            s.add(att)
            s.flush()

            raw = DocumentRaw(
                attachment_id=att.id,
                storage_uri=f"inline://attachment/{att.id}",
                mime_type="text/plain",
                charset="utf-8",
                ocr_required=False,
                ingest_status="done",
            )
            s.add(raw)
            s.flush()

            reqs = requirements_by_attachment.get(at["file_name"], [])
            parsed = DocumentParsed(
                document_raw_id=raw.id,
                parser_version="mvp-0.1",
                sections_json={"requirements": reqs},
                plain_text=at.get("content_text", ""),
                quality_score=0.9 if reqs else 0.2,
                parse_status="done",
            )
            s.add(parsed)
            created_attachments.append({"id": att.id, "atch_type": att.atch_type, "file_name": att.file_name})

        s.add(IngestRun(source_name="manual_posting", status="success"))
        s.flush()
        return {"posting_id": posting.id, "title": posting.title, "attachments": created_attachments}


def list_postings() -> list[dict]:
    with db_session() as s:
        rows = s.execute(select(JobPosting).order_by(desc(JobPosting.id))).scalars().all()
        out = []
        for p in rows:
            inst = s.get(Institution, p.institution_id)
            req_count = _requirements_count(s, p.id)
            out.append(
                {
                    "posting_id": p.id,
                    "title": p.title,
                    "institution_name": inst.inst_name if inst else "",
                    "region_code": p.region_code,
                    "r6000": p.ncs_lclass_code,
                    "requirements_extracted": req_count,
                }
            )
        return out


def list_active_ncs_units(limit: int = 300, query: str = "") -> list[dict]:
    q = str(query or "").strip()
    max_rows = min(max(int(limit or 50), 1), 1000)
    with db_session() as s:
        rows = s.execute(
            select(NcsUnit)
            .where(NcsUnit.is_active.is_(True))
            .order_by(NcsUnit.ncs_cl_cd)
        ).scalars().all()

        out: list[dict] = []
        for r in rows:
            code = str(r.ncs_cl_cd or "").strip()
            name = str(r.compe_unit_name or "").strip()
            if not code or not name:
                continue
            if q:
                hay = f"{code} {name} {r.ncs_sclas_cdnm or ''} {r.ncs_subd_cdnm or ''}".lower()
                if q.lower() not in hay:
                    continue
            out.append(
                {
                    "ncsClCd": code,
                    "compeUnitName": name,
                    "compeUnitLevel": r.compe_unit_level,
                    "ncsSclasCdnm": str(r.ncs_sclas_cdnm or ""),
                    "ncsSubdCdnm": str(r.ncs_subd_cdnm or ""),
                }
            )
            if len(out) >= max_rows:
                break
        return out


def recommend_postings(desired_job: str, desired_region: str, limit: int = 10) -> list[dict]:
    with db_session() as s:
        rows = s.execute(select(JobPosting).order_by(desc(JobPosting.id))).scalars().all()
        out = []
        job_tokens = _tokens(desired_job)
        for p in rows:
            inst = s.get(Institution, p.institution_id)
            reqs = get_requirements_for_posting(s, p.id)
            corpus = " ".join(
                [
                    p.title or "",
                    p.description_raw or "",
                    " ".join(r.get("item", "") for r in reqs[:10]),
                    inst.inst_name if inst else "",
                ]
            ).lower()
            overlap = sum(1 for t in job_tokens if t in corpus)
            score = float(overlap)
            if desired_region:
                region_text = f"{p.region_code or ''} {inst.region_code if inst else ''}".lower()
                if desired_region.lower() in region_text:
                    score += 2.0
            out.append(
                {
                    "posting_id": p.id,
                    "title": p.title,
                    "institution_name": inst.inst_name if inst else "",
                    "region_code": p.region_code,
                    "r6000": p.ncs_lclass_code,
                    "score": round(score, 2),
                    "requirements_extracted": len(reqs),
                }
            )
        out.sort(key=lambda x: (x["score"], x["requirements_extracted"], x["posting_id"]), reverse=True)
        return out[:limit]


def get_posting(posting_id: int) -> dict | None:
    with db_session() as s:
        p = s.get(JobPosting, posting_id)
        if not p:
            return None
        inst = s.get(Institution, p.institution_id)
        atts = s.execute(select(Attachment).where(Attachment.job_posting_id == p.id)).scalars().all()
        reqs = get_requirements_for_posting(s, posting_id)
        return {
            "posting_id": p.id,
            "title": p.title,
            "institution_name": inst.inst_name if inst else "",
            "codes": {
                "R1000": p.employment_type_code,
                "R2000": p.recruitment_type_code,
                "R3000": p.region_code,
                "R6000": p.ncs_lclass_code,
                "R7000": p.education_code,
                "A2000": inst.inst_type_code if inst else "",
                "A1000": inst.supervising_ministry_code if inst else "",
            },
            "attachments": [{"id": a.id, "atch_type": a.atch_type, "file_name": a.file_name} for a in atts],
            "requirements_top": reqs[:10],
        }


def get_requirements_for_posting(session, posting_id: int) -> list[dict]:
    stmt = (
        select(DocumentParsed.sections_json)
        .join(DocumentRaw, DocumentParsed.document_raw_id == DocumentRaw.id)
        .join(Attachment, DocumentRaw.attachment_id == Attachment.id)
        .where(Attachment.job_posting_id == posting_id)
    )
    rows = session.execute(stmt).all()
    reqs: list[dict] = []
    for (sections_json,) in rows:
        if isinstance(sections_json, dict):
            reqs.extend(sections_json.get("requirements", []) or [])
    reqs.sort(key=lambda x: x.get("weight", 0), reverse=True)
    return reqs


def fetch_posting_for_report(posting_id: int) -> dict | None:
    with db_session() as s:
        p = s.get(JobPosting, posting_id)
        if not p:
            return None
        reqs = get_requirements_for_posting(s, posting_id)
        return {"posting_id": p.id, "title": p.title, "r6000": p.ncs_lclass_code or "", "requirements": reqs}


def save_match_result(posting_id: int, report: dict) -> None:
    with db_session() as s:
        profile = s.get(UserProfile, 1)
        if not profile:
            profile = UserProfile(
                id=1,
                user_id="mvp-default-user",
                career_text="",
                desired_job_family="",
                desired_region_code="",
                consent_version="mvp",
            )
            s.add(profile)
            s.flush()
        rec = MatchResult(
            user_profile_id=profile.id,
            job_posting_id=posting_id,
            match_score=report.get("match_score"),
            top_requirements_json=report.get("core_requirements_top"),
            evidence_sentences_json=report.get("profile_match_evidence"),
            gap_analysis_json=report.get("gap_analysis"),
            plan_json=report.get("prep_plan"),
            interview_questions_json=report.get("interview_questions"),
            ncs_mapping_confidence=report.get("ncs_mapping_confidence"),
            model_version=report.get("model_version", "mvp-0.1"),
        )
        s.add(rec)


def record_audit_log(
    *,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    ip_hash: str = "",
) -> int:
    with db_session() as s:
        rec = AuditLog(
            actor_id=str(actor_id or "anonymous")[:128],
            action=str(action or "")[:64],
            resource_type=str(resource_type or "")[:64],
            resource_id=str(resource_id or "")[:128],
            ip_hash=(str(ip_hash or "")[:128] or None),
        )
        s.add(rec)
        s.flush()
        return int(rec.id)


def _quality_run_dict(rec: QuestionQualityRun) -> dict[str, Any]:
    return {
        "id": rec.id,
        "source_endpoint": rec.source_endpoint,
        "ncs_codes": list((rec.ncs_codes_json or {}).get("items") or []),
        "competency_names": list((rec.competency_names_json or {}).get("items") or []),
        "quality_policy_version": rec.quality_policy_version,
        "generator_version": rec.generator_version or "",
        "question_count": int(rec.question_count or 0),
        "ready_count": int(rec.ready_count or 0),
        "review_required": bool(rec.review_required),
        "escalation_required": bool(rec.escalation_required),
        "exception_allowed": bool(rec.exception_allowed),
        "trigger_codes": list((rec.trigger_codes_json or {}).get("items") or []),
        "final_decision": rec.final_decision,
        "created_at": rec.created_at.isoformat() if rec.created_at else "",
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else "",
    }


def create_question_quality_run(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("id") or uuid.uuid4()).strip()[:64]
    review_token = str(payload.get("review_token") or "").strip()
    if not review_token:
        raise ValueError("review_token is required")
    with db_session() as s:
        rec = QuestionQualityRun(
            id=run_id,
            source_endpoint=str(payload.get("source_endpoint") or "unknown")[:128],
            ncs_codes_json={"items": list(payload.get("ncs_codes") or [])[:40]},
            competency_names_json={"items": list(payload.get("competency_names") or [])[:40]},
            quality_policy_version=str(payload.get("quality_policy_version") or "unknown")[:128],
            generator_version=str(payload.get("generator_version") or "")[:128] or None,
            review_token_hash=hashlib.sha256(review_token.encode("utf-8")).hexdigest(),
            question_count=max(0, int(payload.get("question_count") or 0)),
            ready_count=max(0, int(payload.get("ready_count") or 0)),
            review_required=bool(payload.get("review_required", True)),
            escalation_required=bool(payload.get("escalation_required", False)),
            exception_allowed=bool(payload.get("exception_allowed", True)),
            trigger_codes_json={"items": list(payload.get("trigger_codes") or [])[:40]},
            evidence_json=dict(payload.get("evidence") or {}),
            final_decision="draft",
        )
        s.add(rec)
        s.flush()
        out = _quality_run_dict(rec)
        out["review_token"] = review_token
        return out


def get_question_quality_run(run_id: str) -> dict[str, Any] | None:
    with db_session() as s:
        rec = s.get(QuestionQualityRun, str(run_id or "").strip())
        if not rec:
            return None
        out = _quality_run_dict(rec)
        reviews = s.execute(
            select(QuestionQualityReview)
            .where(QuestionQualityReview.run_id == rec.id)
            .order_by(QuestionQualityReview.id)
        ).scalars().all()
        out["reviews"] = [
            {
                "id": review.id,
                "question_hash": review.question_hash,
                "question_index": review.question_index,
                "ncs_code": review.ncs_code or "",
                "method": review.method or "",
                "verdict": review.verdict,
                "active": bool(review.active),
                "issue_codes": list((review.issue_codes_json or {}).get("items") or []),
                "note": review.note_redacted or "",
                "created_at": review.created_at.isoformat() if review.created_at else "",
            }
            for review in reviews
        ]
        return out


def verify_question_quality_run_token(run_id: str, review_token: str) -> bool:
    with db_session() as s:
        rec = s.get(QuestionQualityRun, str(run_id or "").strip())
        token = str(review_token or "").strip()
        return bool(
            rec
            and token
            and hashlib.sha256(token.encode("utf-8")).hexdigest() == rec.review_token_hash
        )


def record_question_quality_review(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or "").strip()
    with db_session() as s:
        run = _lock_question_quality_run(s, run_id)
        if not run:
            raise LookupError("quality run not found")
        review_token = str(payload.get("review_token") or "").strip()
        if not review_token or hashlib.sha256(review_token.encode("utf-8")).hexdigest() != run.review_token_hash:
            raise PermissionError("invalid review token")
        question_hash = str(payload.get("question_hash") or "").strip()
        evidence_by_hash = {
            str(item.get("question_hash") or "").strip(): item
            for item in ((run.evidence_json or {}).get("question_items") or [])
            if isinstance(item, dict) and str(item.get("question_hash") or "").strip()
        }
        if not question_hash or question_hash not in evidence_by_hash:
            raise ValueError("question_hash is not part of this quality run")
        evidence_item = evidence_by_hash[question_hash]
        question_text = str(payload.get("question_text") or "").strip()
        if question_text and hashlib.sha256(question_text.encode("utf-8")).hexdigest() != question_hash:
            raise ValueError("question_text does not match question_hash")
        reviewer_ref = str(payload.get("reviewer_ref") or "local-reviewer")
        reviewer_ref_hash = hashlib.sha256(reviewer_ref.encode("utf-8")).hexdigest()
        verdict = str(payload.get("verdict") or "needs_edit")[:32]
        issue_codes = list(payload.get("issue_codes") or [])[:20]
        note_redacted = str(payload.get("note") or "")[:500] or None
        prior_active = s.execute(
            select(QuestionQualityReview)
            .where(QuestionQualityReview.run_id == run_id)
            .where(QuestionQualityReview.question_hash == question_hash)
            .where(QuestionQualityReview.active.is_(True))
        ).scalars().all()
        if len(prior_active) == 1:
            prior = prior_active[0]
            prior_issue_codes = list((prior.issue_codes_json or {}).get("items") or [])
            if (
                prior.verdict == verdict
                and prior_issue_codes == issue_codes
                and (prior.question_text or "") == question_text
                and prior.note_redacted == note_redacted
                and prior.reviewer_ref_hash == reviewer_ref_hash
            ):
                return {
                    "id": int(prior.id),
                    "run_id": run_id,
                    "question_hash": prior.question_hash,
                    "verdict": prior.verdict,
                    "issue_codes": prior_issue_codes,
                    "run_decision": run.final_decision,
                    "idempotent": True,
                }
        expected_review_id = payload.get("expected_review_id")
        if expected_review_id is not None:
            actual_review_id = int(prior_active[0].id) if len(prior_active) == 1 else 0
            if len(prior_active) > 1 or actual_review_id != int(expected_review_id):
                raise QuestionQualityReviewConflictError(
                    "the active review changed; refresh the run before saving the decision again"
                )
        previous_active_review_id = (
            int(prior_active[0].id) if len(prior_active) == 1 else None
        )
        for prior in prior_active:
            prior.active = False
        review = QuestionQualityReview(
            run_id=run_id,
            question_hash=question_hash[:64],
            question_index=(int(evidence_item.get("index")) if evidence_item.get("index") is not None else None),
            ncs_code=str(evidence_item.get("ncs_code") or "")[:64] or None,
            method=str(evidence_item.get("method") or "")[:64] or None,
            question_text=question_text[:1600] or None,
            verdict=verdict,
            issue_codes_json={
                "items": issue_codes,
                "previous_active_review_id": previous_active_review_id,
            },
            note_redacted=note_redacted,
            reviewer_ref_hash=reviewer_ref_hash,
            active=True,
        )
        s.add(review)
        s.flush()

        review_rows = s.execute(
            select(QuestionQualityReview)
            .where(QuestionQualityReview.run_id == run_id)
            .where(QuestionQualityReview.active.is_(True))
            .order_by(QuestionQualityReview.id)
        ).scalars().all()
        latest_by_question = {row.question_hash: row.verdict for row in review_rows}
        verdicts = list(latest_by_question.values())
        if any(value == "reject" for value in verdicts):
            run.final_decision = "rejected"
        elif any(value == "needs_edit" for value in verdicts):
            run.final_decision = "needs_edit"
        elif verdicts and len(verdicts) >= int(run.question_count or 0) and not run.escalation_required:
            run.final_decision = "approved"
        else:
            run.final_decision = "reviewing"
        s.flush()
        return {
            "id": int(review.id),
            "run_id": run_id,
            "question_hash": review.question_hash,
            "verdict": review.verdict,
            "issue_codes": list((review.issue_codes_json or {}).get("items") or []),
            "run_decision": run.final_decision,
            "idempotent": False,
        }


def rollback_question_quality_review(
    *,
    run_id: str,
    review_token: str,
    question_hash: str,
    reviewer_ref: str = "local-reviewer",
    note: str = "",
    expected_review_id: int | None = None,
) -> dict[str, Any]:
    with db_session() as s:
        run = _lock_question_quality_run(s, str(run_id or "").strip())
        if not run:
            raise LookupError("quality run not found")
        token = str(review_token or "").strip()
        if not token or hashlib.sha256(token.encode("utf-8")).hexdigest() != run.review_token_hash:
            raise PermissionError("invalid review token")
        q_hash = str(question_hash or "").strip()
        allowed_hashes = {
            str(item.get("question_hash") or "").strip()
            for item in ((run.evidence_json or {}).get("question_items") or [])
            if isinstance(item, dict)
        }
        if q_hash not in allowed_hashes:
            raise ValueError("question_hash is not part of this quality run")
        note_redacted = str(note or "")[:500] or None
        reviewer_ref_hash = hashlib.sha256(
            str(reviewer_ref or "local-reviewer").encode("utf-8")
        ).hexdigest()
        latest_event = s.execute(
            select(QuestionQualityReview)
            .where(QuestionQualityReview.run_id == run.id)
            .where(QuestionQualityReview.question_hash == q_hash)
            .order_by(desc(QuestionQualityReview.id))
            .limit(1)
        ).scalar_one_or_none()
        latest_metadata = (
            latest_event.issue_codes_json
            if latest_event and isinstance(latest_event.issue_codes_json, dict)
            else {}
        )
        if (
            latest_event
            and latest_event.verdict == "rollback"
            and latest_event.note_redacted == note_redacted
            and latest_event.reviewer_ref_hash == reviewer_ref_hash
            and latest_metadata.get("rolled_back_review_id") is not None
            and (
                expected_review_id is None
                or int(latest_metadata["rolled_back_review_id"]) == int(expected_review_id)
            )
        ):
            return {
                "run_id": run.id,
                "question_hash": q_hash,
                "rollback_event_id": int(latest_event.id),
                "rolled_back_review_id": int(latest_metadata["rolled_back_review_id"]),
                "restored_review_id": (
                    int(latest_metadata["restored_review_id"])
                    if latest_metadata.get("restored_review_id") is not None
                    else None
                ),
                "run_decision": run.final_decision,
                "idempotent": True,
            }
        history = s.execute(
            select(QuestionQualityReview)
            .where(QuestionQualityReview.run_id == run.id)
            .where(QuestionQualityReview.question_hash == q_hash)
            .where(QuestionQualityReview.verdict != "rollback")
            .order_by(desc(QuestionQualityReview.id))
        ).scalars().all()
        active_history = [row for row in history if row.active]
        current = active_history[0] if active_history else None
        if not current:
            raise ValueError("no active review to roll back")
        if expected_review_id is not None and int(current.id) != int(expected_review_id):
            raise QuestionQualityReviewConflictError(
                "the active review changed; refresh the run before attempting rollback again"
            )
        # Serialized writes should leave one active row, but clean up every
        # active row here so a legacy/corrupted state cannot survive rollback.
        for active_review in active_history:
            active_review.active = False
        current_metadata = (
            current.issue_codes_json
            if isinstance(current.issue_codes_json, dict)
            else {}
        )
        has_previous_pointer = "previous_active_review_id" in current_metadata
        previous_id = current_metadata.get("previous_active_review_id")
        previous = None
        if previous_id is not None:
            candidate = s.get(QuestionQualityReview, int(previous_id))
            if (
                candidate
                and candidate.run_id == run.id
                and candidate.question_hash == q_hash
                and candidate.verdict != "rollback"
                and candidate.id != current.id
            ):
                previous = candidate
        elif not has_previous_pointer:
            # Compatibility for decisions persisted before predecessor pointers
            # were introduced. New rows always carry the key, including an
            # explicit null when there was no prior active decision.
            previous = next((row for row in history if row.id != current.id), None)
        if previous:
            previous.active = True
        rollback_event = QuestionQualityReview(
            run_id=run.id,
            question_hash=q_hash,
            question_index=current.question_index,
            ncs_code=current.ncs_code,
            method=current.method,
            question_text=None,
            verdict="rollback",
            issue_codes_json={
                "items": [],
                "rolled_back_review_id": int(current.id),
                "restored_review_id": int(previous.id) if previous else None,
            },
            note_redacted=note_redacted,
            reviewer_ref_hash=reviewer_ref_hash,
            active=False,
        )
        s.add(rollback_event)
        s.flush()

        active_verdicts = list(
            s.execute(
                select(QuestionQualityReview.verdict)
                .where(QuestionQualityReview.run_id == run.id)
                .where(QuestionQualityReview.active.is_(True))
            ).scalars().all()
        )
        if any(value == "reject" for value in active_verdicts):
            run.final_decision = "rejected"
        elif any(value == "needs_edit" for value in active_verdicts):
            run.final_decision = "needs_edit"
        elif active_verdicts and len(active_verdicts) >= int(run.question_count or 0) and not run.escalation_required:
            run.final_decision = "approved"
        elif active_verdicts:
            run.final_decision = "reviewing"
        else:
            run.final_decision = "draft"
        s.flush()
        return {
            "run_id": run.id,
            "question_hash": q_hash,
            "rollback_event_id": int(rollback_event.id),
            "rolled_back_review_id": int(current.id),
            "restored_review_id": int(previous.id) if previous else None,
            "run_decision": run.final_decision,
            "idempotent": False,
        }


def list_question_quality_feedback(ncs_codes: list[str], limit: int = 80) -> list[dict[str, Any]]:
    codes = [str(code or "").strip() for code in ncs_codes if str(code or "").strip()]
    with db_session() as s:
        stmt = (
            select(QuestionQualityReview)
            .where(QuestionQualityReview.verdict.in_(("reject", "needs_edit")))
            .where(QuestionQualityReview.active.is_(True))
            .where(QuestionQualityReview.question_text.is_not(None))
            .order_by(desc(QuestionQualityReview.id))
            .limit(min(max(int(limit or 20), 1), 200))
        )
        if codes:
            stmt = stmt.where(QuestionQualityReview.ncs_code.in_(codes))
        rows = s.execute(stmt).scalars().all()
        return [
            {
                "run_id": row.run_id,
                "question_hash": row.question_hash,
                "question_text": row.question_text or "",
                "ncs_code": row.ncs_code or "",
                "method": row.method or "",
                "verdict": row.verdict,
                "issue_codes": list((row.issue_codes_json or {}).get("items") or []),
            }
            for row in rows
        ]


def promote_question_quality_eval_case(review_id: int, case_type: str, policy_version: str) -> dict[str, Any]:
    with db_session() as s:
        review = s.get(QuestionQualityReview, int(review_id))
        if not review:
            raise LookupError("quality review not found")
        if not review.active:
            raise ValueError("only the active review decision can be promoted")
        normalized_case = str(case_type or "").strip()
        if normalized_case == "golden" and review.verdict != "approve":
            raise ValueError("golden cases require an approved review")
        if normalized_case in {"negative", "regression"} and review.verdict not in {"reject", "needs_edit"}:
            raise ValueError("negative/regression cases require reject or needs_edit")
        case = QuestionQualityEvalCase(
            source_review_id=review.id,
            case_type=normalized_case,
            quality_policy_version=str(policy_version or "unknown")[:128],
            expected_decision="pass" if normalized_case == "golden" else "fail",
            active=True,
        )
        s.add(case)
        s.flush()
        return {
            "id": int(case.id),
            "source_review_id": int(review.id),
            "case_type": case.case_type,
            "expected_decision": case.expected_decision,
            "active": bool(case.active),
        }


def list_question_quality_eval_cases(active_only: bool = True) -> list[dict[str, Any]]:
    with db_session() as s:
        stmt = (
            select(QuestionQualityEvalCase, QuestionQualityReview)
            .join(QuestionQualityReview, QuestionQualityReview.id == QuestionQualityEvalCase.source_review_id)
            .order_by(QuestionQualityEvalCase.id)
        )
        if active_only:
            stmt = stmt.where(QuestionQualityEvalCase.active.is_(True))
        rows = s.execute(stmt).all()
        return [
            {
                "id": int(case.id),
                "source_review_id": int(review.id),
                "case_type": case.case_type,
                "quality_policy_version": case.quality_policy_version,
                "expected_decision": case.expected_decision,
                "active": bool(case.active),
                "question_hash": review.question_hash,
                "question_text": review.question_text or "",
                "ncs_code": review.ncs_code or "",
                "method": review.method or "",
                "verdict": review.verdict,
                "issue_codes": list((review.issue_codes_json or {}).get("items") or []),
            }
            for case, review in rows
        ]


def question_quality_metrics() -> dict[str, Any]:
    with db_session() as s:
        run_count = int(s.scalar(select(func.count()).select_from(QuestionQualityRun)) or 0)
        review_count = int(s.scalar(select(func.count()).select_from(QuestionQualityReview)) or 0)
        active_review_count = int(
            s.scalar(
                select(func.count()).select_from(QuestionQualityReview).where(QuestionQualityReview.active.is_(True))
            )
            or 0
        )
        rollback_count = int(
            s.scalar(
                select(func.count()).select_from(QuestionQualityReview).where(QuestionQualityReview.verdict == "rollback")
            )
            or 0
        )
        escalation_count = int(
            s.scalar(
                select(func.count()).select_from(QuestionQualityRun).where(QuestionQualityRun.escalation_required.is_(True))
            )
            or 0
        )
        active_eval_cases = int(
            s.scalar(
                select(func.count()).select_from(QuestionQualityEvalCase).where(QuestionQualityEvalCase.active.is_(True))
            )
            or 0
        )
        decision_rows = s.execute(
            select(QuestionQualityReview.verdict, func.count(QuestionQualityReview.id))
            .where(QuestionQualityReview.active.is_(True))
            .group_by(QuestionQualityReview.verdict)
        ).all()
        issue_counts: dict[str, int] = {}
        for payload in s.execute(
            select(QuestionQualityReview.issue_codes_json).where(QuestionQualityReview.active.is_(True))
        ).scalars().all():
            for code in (payload or {}).get("items") or []:
                key = str(code or "").strip()
                if key:
                    issue_counts[key] = issue_counts.get(key, 0) + 1
        decisions = {str(verdict): int(count) for verdict, count in decision_rows}
        return {
            "runs": run_count,
            "reviews": review_count,
            "active_reviews": active_review_count,
            "rollback_events": rollback_count,
            "escalation_runs": escalation_count,
            "active_eval_cases": active_eval_cases,
            "decisions": decisions,
            "approval_rate": round(decisions.get("approve", 0) / max(1, active_review_count), 4),
            "rejection_rate": round(decisions.get("reject", 0) / max(1, active_review_count), 4),
            "escalation_rate": round(escalation_count / max(1, run_count), 4),
            "issue_counts": dict(sorted(issue_counts.items())),
        }


def upsert_institution(
    *,
    inst_cd: str,
    inst_name: str,
    inst_type_code: str,
    supervising_ministry_code: str,
    region_code: str,
) -> int:
    with db_session() as s:
        inst = s.execute(select(Institution).where(Institution.inst_cd == inst_cd)).scalar_one_or_none()
        if not inst:
            inst = Institution(
                inst_cd=inst_cd,
                inst_name=inst_name,
                inst_type_code=inst_type_code,
                supervising_ministry_code=supervising_ministry_code,
                region_code=region_code,
            )
            s.add(inst)
            s.flush()
            return inst.id
        inst.inst_name = inst_name or inst.inst_name
        inst.inst_type_code = inst_type_code or inst.inst_type_code
        inst.supervising_ministry_code = supervising_ministry_code or inst.supervising_ministry_code
        inst.region_code = region_code or inst.region_code
        s.flush()
        return inst.id


def upsert_branch(
    *,
    institution_id: int,
    branch_name: str,
    region_code: str = "",
    address: str = "",
) -> int:
    with db_session() as s:
        br = (
            s.execute(
                select(Branch).where(Branch.institution_id == institution_id).where(Branch.branch_name == branch_name)
            ).scalar_one_or_none()
        )
        if not br:
            br = Branch(
                institution_id=institution_id,
                branch_name=branch_name or "미지정 지점",
                region_code=region_code,
                address=address,
            )
            s.add(br)
            s.flush()
            return br.id
        br.region_code = region_code or br.region_code
        br.address = address or br.address
        s.flush()
        return br.id


def record_ingest_run(source_name: str, status: str, error_code: str | None = None) -> int:
    with db_session() as s:
        run = IngestRun(source_name=source_name, status=status, error_code=error_code)
        s.add(run)
        s.flush()
        return run.id


def start_ncs_sync(version_tag: str) -> int:
    with db_session() as s:
        run = NcsSyncRun(version_tag=version_tag, status="running")
        s.add(run)
        s.flush()
        return run.id


def finish_ncs_sync(run_id: int, status: str, total_count: int | None, error_code: str | None = None) -> None:
    with db_session() as s:
        run = s.get(NcsSyncRun, run_id)
        if not run:
            return
        run.status = status
        run.total_count = total_count
        run.error_code = error_code
        s.flush()


def upsert_ncs_units(
    version_tag: str,
    units: list[dict[str, Any]],
    deactivate_existing: bool = False,
) -> int:
    with db_session() as s:
        if deactivate_existing:
            s.query(NcsUnit).update({"is_active": False})
        count = 0
        for row in units:
            ncs_cl_cd = str(row.get("ncsClCd", "")).strip()
            if not ncs_cl_cd:
                continue
            rec = s.get(NcsUnit, ncs_cl_cd)
            if not rec:
                rec = NcsUnit(
                    ncs_cl_cd=ncs_cl_cd,
                    compe_unit_name=row.get("compeUnitName", ""),
                    compe_unit_level=_safe_int(row.get("compeUnitLevel")),
                    ncs_lclas_cdnm=row.get("ncsLclasCdnm", ""),
                    ncs_mclas_cdnm=row.get("ncsMclasCdnm", ""),
                    ncs_sclas_cdnm=row.get("ncsSclasCdnm", ""),
                    ncs_subd_cdnm=row.get("ncsSubdCdnm", ""),
                    compe_unit_def=row.get("compeUnitDef", ""),
                    version_tag=version_tag,
                    is_active=True,
                )
                s.add(rec)
            else:
                rec.compe_unit_name = row.get("compeUnitName", rec.compe_unit_name)
                rec.compe_unit_level = _safe_int(row.get("compeUnitLevel")) or rec.compe_unit_level
                rec.ncs_lclas_cdnm = row.get("ncsLclasCdnm", rec.ncs_lclas_cdnm)
                rec.ncs_mclas_cdnm = row.get("ncsMclasCdnm", rec.ncs_mclas_cdnm)
                rec.ncs_sclas_cdnm = row.get("ncsSclasCdnm", rec.ncs_sclas_cdnm)
                rec.ncs_subd_cdnm = row.get("ncsSubdCdnm", rec.ncs_subd_cdnm)
                rec.compe_unit_def = row.get("compeUnitDef", rec.compe_unit_def)
                rec.version_tag = version_tag
                rec.is_active = True
            count += 1
        s.flush()
        return count


def _safe_int(value) -> int | None:
    try:
        return int(str(value))
    except Exception:
        return None


def _requirements_count(session, posting_id: int) -> int:
    return len(get_requirements_for_posting(session, posting_id))


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "")
    return {w.lower() for w in words}
