from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AtchType = Literal["A", "B", "C", "Z"]


class AttachmentIn(BaseModel):
    atch_type: AtchType
    file_name: str = Field(min_length=1, max_length=255)
    content_text: str = Field(default="", max_length=200000)


class PostingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    institution_name: str = Field(min_length=1, max_length=255)
    region_code: str = Field(default="R3000_UNKNOWN", max_length=50)
    r1000: str = Field(default="R1000_UNKNOWN", max_length=50)
    r2000: str = Field(default="R2000_UNKNOWN", max_length=50)
    r6000: str = Field(default="R6000_UNKNOWN", max_length=50)
    r7000: str = Field(default="R7000_UNKNOWN", max_length=50)
    a2000: str = Field(default="A2000_UNKNOWN", max_length=50)
    a1000: str = Field(default="A1000_UNKNOWN", max_length=50)
    attachments: list[AttachmentIn] = Field(default_factory=list)


class ReportCreate(BaseModel):
    posting_id: int
    profile_text: str = Field(min_length=20, max_length=200000)
    desired_job: str = Field(default="", max_length=255)
    desired_region_code: str = Field(default="", max_length=50)


class RequirementOut(BaseModel):
    item: str
    source: str
    weight: float


class MatchEvidenceOut(BaseModel):
    requirement: str
    evidence_sentence: str
    reason: str
    score: float


class NcsCandidateOut(BaseModel):
    ncsClCd: str
    compeUnitName: str
    compeUnitLevel: int
    reason: str
    score: float


class ReportOut(BaseModel):
    posting_id: int
    core_requirements_top: list[RequirementOut]
    profile_match_evidence: list[MatchEvidenceOut]
    gap_analysis: list[str]
    prep_plan: list[str]
    interview_questions: list[str]
    ncs_candidates: list[NcsCandidateOut]


class AiInterviewRequest(BaseModel):
    posting_id: int
    profile_text: str = Field(min_length=20, max_length=200000)
    desired_job: str = Field(default="", max_length=255)


class AiInterviewResponse(BaseModel):
    posting_id: int
    model: str
    questions: list[str]
