"""Structured hand-offs (C3). Anything a later step decides on travels as one of these Pydantic objects,
never as free text. LLM nodes are asked for exactly these schemas (JSON mode + validation + one repair)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Requirement(BaseModel):
    text: str = Field(description="One thing the client needs, phrased as a requirement")
    skill: str | None = Field(default=None, description="Main skill/tool this requirement depends on, if any")
    priority: Literal["must", "nice"] = "must"


class JobSpec(BaseModel):
    job_id: str
    title: str
    summary: str = ""
    deliverables: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    budget_type: Literal["fixed", "hourly", "unknown"] = "unknown"
    budget_min: float | None = None
    budget_max: float | None = None
    timeline: str | None = None
    screening_questions: list[str] = Field(default_factory=list)
    hidden_needs: list[str] = Field(default_factory=list, description="Needs implied but not stated")
    category: str | None = None


class AnalystOut(BaseModel):
    """What the Job Analyst LLM returns; merged with structured marketplace fields into a JobSpec."""
    summary: str
    deliverables: list[str] = Field(default_factory=list, max_length=8)
    requirements: list[Requirement] = Field(default_factory=list, max_length=8)
    timeline: str | None = None
    screening_questions: list[str] = Field(default_factory=list, max_length=6)
    hidden_needs: list[str] = Field(default_factory=list, max_length=5)


class RiskFlag(BaseModel):
    code: Literal["unpaid_work", "off_platform_contact", "off_platform_payment", "upfront_fee", "personal_data_request",
                  "check_fraud", "prompt_injection", "unverified_payment", "new_client", "low_budget", "vague_scope",
                  "low_client_rating", "other"]
    severity: Literal["low", "medium", "high"]
    quote: str = Field(default="", description="Short quote from the post that triggered the flag")


class ClientQuality(BaseModel):
    score: float = Field(ge=0, le=1)
    payment_verified: bool | None = None
    total_spent: float | None = None
    rating: float | None = None
    hires: int | None = None
    notes: list[str] = Field(default_factory=list)


class RiskLLMOut(BaseModel):
    is_scam: bool
    flags: list[RiskFlag] = Field(default_factory=list)
    explanation: str


class RiskReport(BaseModel):
    score: float = Field(ge=0, le=1)
    level: Literal["low", "medium", "high"]
    is_scam: bool
    flags: list[RiskFlag] = Field(default_factory=list)
    injection_detected: bool = False
    injection_score: float | None = None
    client: ClientQuality
    explanation: str


class EvidenceRef(BaseModel):
    doc_id: str
    title: str = ""
    type: str = ""
    snippet: str = ""
    score: float = 0.0


class RequirementMatch(BaseModel):
    requirement: str
    skill: str | None = None
    status: Literal["proven", "partial", "missing"]
    evidence: list[EvidenceRef] = Field(default_factory=list)


class FitReport(BaseModel):
    score: float = Field(ge=0, le=1)
    matrix: list[RequirementMatch] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    unsupported_requirements: list[str] = Field(default_factory=list)
    retrieval_variant: str = ""


class BidDecision(BaseModel):
    action: Literal["bid", "watch", "skip"]
    reasons: list[str]
    confidence: float = Field(ge=0, le=1)
    auto: bool = False


class PriceQuote(BaseModel):
    bid_type: Literal["fixed", "hourly"]
    amount: float = Field(gt=0)
    low: float
    high: float
    currency: str = "USD"
    rationale: list[str]
    connects_cost: int
    connects_surcharges: list[str] = Field(default_factory=list)
    expected_value_usd: float
    connects_worth_it: bool


class ScreeningAnswer(BaseModel):
    question: str
    answer: str


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["supported", "partly_supported", "unsupported", "unchecked"] = "unchecked"
    note: str = ""
    action: Literal["kept", "rewritten", "removed"] = "kept"


class WriterOut(BaseModel):
    cover_letter: str = Field(description="Paragraphs separated by blank lines; every factual sentence cites [doc_id]")
    screening_answers: list[ScreeningAnswer] = Field(default_factory=list)


class ProposalDraft(BaseModel):
    version: int
    cover_letter: str
    screening_answers: list[ScreeningAnswer] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    word_count: int = 0
    source: Literal["writer", "verifier", "human_edit", "compliance"] = "writer"


class VerificationReport(BaseModel):
    claims: list[Claim]
    unsupported_before: int
    unsupported_after: int
    total_claims: int
    llm_checked: int = 0


class CritiqueIssue(BaseModel):
    kind: Literal["grounding", "personalisation", "length", "generic_opening", "tone", "missing_requirement",
                  "screening", "other"]
    detail: str
    paragraph: int | None = None


class Critique(BaseModel):
    approve: bool
    score: float = Field(ge=0, le=10)
    issues: list[CritiqueIssue] = Field(default_factory=list)
    instructions: str = ""


class Violation(BaseModel):
    rule: str
    text: str
    fix: str = ""


class ComplianceVerdict(BaseModel):
    allowed: bool
    blocked_reason: str | None = None
    violations: list[Violation] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list)
    final_text: str
    checked_by: list[str] = Field(default_factory=list)


class ComplianceLLMOut(BaseModel):
    violations: list[Violation] = Field(default_factory=list)
    block: bool = False
    reason: str = ""


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    edited_text: str | None = None
    reason: str | None = None
    user_id: str | None = None


# ----------------------------------------------------------------------------- conversation graph
class IntentResult(BaseModel):
    intent: Literal["question", "negotiation", "interview_request", "scam_signal", "other"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    requested_rate: float | None = None


class ReplyDraft(BaseModel):
    text: str
    proposed_slots: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    walk_away: bool = False
