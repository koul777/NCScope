from __future__ import annotations

import hashlib
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import desc, select

from app.db import SessionLocal
from app.models import (
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
    UserProfile,
)


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
