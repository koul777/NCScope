from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inst_cd: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    inst_name: Mapped[str] = mapped_column(String(255), nullable=False)
    inst_type_code: Mapped[str] = mapped_column(String(64), nullable=False)
    supervising_ministry_code: Mapped[str] = mapped_column(String(64), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_institutions_type_region", "inst_type_code", "region_code"),
    )


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=True)
    address: Mapped[str] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_branches_institution_region", "institution_id", "region_code"),
    )

    institution = relationship("Institution")


class JobPosting(Base):
    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    posting_external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description_raw: Mapped[str] = mapped_column(Text, nullable=True)
    employment_type_code: Mapped[str] = mapped_column(String(64), nullable=True)
    recruitment_type_code: Mapped[str] = mapped_column(String(64), nullable=True)
    region_code: Mapped[str] = mapped_column(String(64), nullable=True)
    ncs_lclass_code: Mapped[str] = mapped_column(String(64), nullable=True)
    education_code: Mapped[str] = mapped_column(String(64), nullable=True)
    open_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    close_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=True)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=True)
    source_updated_at: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index(
            "ix_job_postings_filter",
            "employment_type_code",
            "recruitment_type_code",
            "region_code",
            "ncs_lclass_code",
            "education_code",
        ),
        Index("ix_job_postings_close_at", "close_at"),
        Index("ix_job_postings_institution", "institution_id"),
    )

    institution = relationship("Institution")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), nullable=False)
    attachment_external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    atch_type: Mapped[str] = mapped_column(String(1), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(32), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=True)
    download_url: Mapped[str] = mapped_column(String(1000), nullable=True)
    download_status: Mapped[str] = mapped_column(String(32), nullable=True)
    downloaded_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    checksum_sha256: Mapped[str] = mapped_column(String(128), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_attachments_posting_type", "job_posting_id", "atch_type"),
        Index("ix_attachments_download_status", "download_status"),
    )


class DocumentRaw(Base):
    __tablename__ = "documents_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("attachments.id"), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=True)
    charset: Mapped[str] = mapped_column(String(64), nullable=True)
    ocr_required: Mapped[bool] = mapped_column(Boolean, default=False)
    ingest_status: Mapped[str] = mapped_column(String(32), nullable=True)
    ingest_error_code: Mapped[str] = mapped_column(String(32), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_documents_raw_ingest_status", "ingest_status"),
    )


class DocumentParsed(Base):
    __tablename__ = "documents_parsed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_raw_id: Mapped[int] = mapped_column(ForeignKey("documents_raw.id"), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=True)
    sections_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    plain_text: Mapped[str] = mapped_column(Text, nullable=True)
    quality_score: Mapped[float] = mapped_column(Float, nullable=True)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=True)
    parse_error_code: Mapped[str] = mapped_column(String(32), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_documents_parsed_status_quality", "parse_status", "quality_score"),
    )


class NcsUnit(Base):
    __tablename__ = "ncs_units"

    ncs_cl_cd: Mapped[str] = mapped_column(String(32), primary_key=True)
    compe_unit_name: Mapped[str] = mapped_column(String(255), nullable=False)
    compe_unit_level: Mapped[int] = mapped_column(Integer, nullable=True)
    ncs_lclas_cdnm: Mapped[str] = mapped_column(String(255), nullable=True)
    ncs_mclas_cdnm: Mapped[str] = mapped_column(String(255), nullable=True)
    ncs_sclas_cdnm: Mapped[str] = mapped_column(String(255), nullable=True)
    ncs_subd_cdnm: Mapped[str] = mapped_column(String(255), nullable=True)
    compe_unit_def: Mapped[str] = mapped_column(Text, nullable=True)
    version_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    synced_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_ncs_units_active_name", "is_active", "compe_unit_name"),
        Index("ix_ncs_units_lclass_level", "ncs_lclas_cdnm", "compe_unit_level"),
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    career_text: Mapped[str] = mapped_column(Text, nullable=False)
    desired_job_family: Mapped[str] = mapped_column(String(255), nullable=True)
    desired_region_code: Mapped[str] = mapped_column(String(64), nullable=True)
    consent_version: Mapped[str] = mapped_column(String(32), nullable=True)
    consented_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_user_profiles_region", "desired_region_code"),
    )


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    job_posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), nullable=False)
    match_score: Mapped[float] = mapped_column(Float, nullable=True)
    top_requirements_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    evidence_sentences_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    gap_analysis_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    interview_questions_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    ncs_mapping_confidence: Mapped[float] = mapped_column(Float, nullable=True)
    model_version: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_match_results_user_created", "user_profile_id", "created_at"),
        Index("ix_match_results_posting_score", "job_posting_id", "match_score"),
    )


class SavedJob(Base):
    __tablename__ = "saved_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_posting_id: Mapped[int] = mapped_column(ForeignKey("job_postings.id"), nullable=False)
    saved_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(32), nullable=True)
    memo: Mapped[str] = mapped_column(String(1000), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "job_posting_id", name="ux_saved_jobs_user_posting"),
        Index("ix_saved_jobs_user_status", "user_id", "status"),
    )


class NcsSyncRun(Base):
    __tablename__ = "ncs_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str] = mapped_column(String(32), nullable=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str] = mapped_column(String(32), nullable=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ip_hash: Mapped[str] = mapped_column(String(128), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
    )

