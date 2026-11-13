"""Request/response models. The web client's TypeScript types are generated from these (OpenAPI), never hand-written."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=6, max_length=200)


class RegisterIn(LoginIn):
    display_name: str = Field(min_length=1, max_length=80)
    persona: Literal["ai_ml", "fullstack", "data_analyst", "own"] = "fullstack"


class User(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    corpus: str
    onboarded: bool
    persona: str | None = None


class SavedSearch(BaseModel):
    search_id: int | None = None
    query: str = Field(default="", max_length=200)
    domain: Literal["ai_ml", "fullstack", "data_analyst"] | None = None
    interval_s: int = Field(default=900, ge=300, le=86400)
    enabled: bool = True


class Profile(BaseModel):
    hourly_rate: float = Field(ge=1, le=1000)
    hourly_floor: float = Field(ge=1, le=1000)
    min_project: float = Field(ge=0, le=100000)
    domain: Literal["ai_ml", "fullstack", "data_analyst"] = "fullstack"
    headline: str | None = None


class OnboardingIn(BaseModel):
    marketplace: Literal["gigsim", "upwork_live"] = "gigsim"
    gigsim_token: str | None = Field(default=None, max_length=200, description="leave empty to use the demo account")
    profile: Profile
    weekly_connects_budget: int = Field(default=80, ge=0, le=1000)
    saved_searches: list[SavedSearch] = Field(default_factory=list, max_length=10)


class SettingsOut(BaseModel):
    profile: Profile
    autonomy: Literal["manual", "auto_skip_low_fit"] = "manual"
    weekly_connects_budget: int = 80
    saved_searches: list[SavedSearch] = Field(default_factory=list)
    connected_accounts: list[str] = Field(default_factory=list)
    marketplace_mode: str
    budgets: dict[str, Any] = Field(default_factory=dict)


class SettingsIn(BaseModel):
    profile: Profile | None = None
    autonomy: Literal["manual", "auto_skip_low_fit"] | None = None
    weekly_connects_budget: int | None = Field(default=None, ge=0, le=1000)
    saved_searches: list[SavedSearch] | None = None


class InboxItem(BaseModel):
    job_id: str
    title: str
    category: str | None = None
    status: str
    fit: float | None = None
    risk: float | None = None
    risk_level: str | None = None
    is_scam: bool = False
    injection: bool = False
    action: str | None = None
    price: float | None = None
    bid_type: str | None = None
    connects: int | None = None
    duplicate_of: str | None = None
    thread_id: str | None = None
    updated_at: str | None = None


class JobDetail(BaseModel):
    job: dict[str, Any]
    status: str
    thread_id: str | None = None
    job_spec: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    fit: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    price: dict[str, Any] | None = None
    trace_url: str | None = None
    errors: list[str] = Field(default_factory=list)


class StudioPayload(BaseModel):
    thread_id: str
    status: str
    pending: bool
    approval: dict[str, Any] | None = None
    drafts: list[dict[str, Any]] = Field(default_factory=list)
    critiques: list[dict[str, Any]] = Field(default_factory=list)
    verification: list[dict[str, Any]] = Field(default_factory=list)
    compliance: list[dict[str, Any]] = Field(default_factory=list)
    submission: dict[str, Any] | None = None
    attention_reason: str | None = None
    trace_url: str | None = None


class ApprovalIn(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    edited_text: str | None = Field(default=None, max_length=6000)
    reason: str | None = Field(default=None, max_length=500)


class ApprovalOut(BaseModel):
    thread_id: str
    accepted: bool
    status: str


class ParagraphIn(BaseModel):
    paragraph_index: int = Field(ge=0, le=20)
    instruction: str | None = Field(default=None, max_length=300)


class ParagraphOut(BaseModel):
    paragraph_index: int
    text: str


class PendingApproval(BaseModel):
    thread_id: str
    kind: Literal["proposal", "message"]
    title: str | None = None
    ref: str | None = None
    created_at: str | None = None


class RunEvent(BaseModel):
    id: int
    node: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RunOut(BaseModel):
    thread_id: str
    kind: str
    ref: str | None = None
    status: str
    trace_id: str | None = None
    trace_url: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    events: list[RunEvent] = Field(default_factory=list)
    created_at: str | None = None


class CheckpointOut(BaseModel):
    checkpoint_id: str
    next: list[str]
    step: int | None = None
    writes: list[str] = Field(default_factory=list)
    status: str | None = None
    created_at: str | None = None


class PipelineCard(BaseModel):
    job_id: str
    title: str | None = None
    stage: Literal["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"]
    category: str | None = None
    connects: int = 0
    amount: float | None = None
    thread_id: str | None = None
    updated_at: str | None = None


class Thread(BaseModel):
    marketplace_thread_id: str
    job_id: str | None = None
    job_title: str | None = None
    state: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    graph_thread_id: str | None = None
    graph_status: str | None = None
    suggestion: dict[str, Any] | None = None


class Analytics(BaseModel):
    reply_rate_by_category: list[dict[str, Any]]
    connects: dict[str, Any]
    cost_latency: dict[str, Any]
    rejection_reasons: list[dict[str, Any]]
    funnel: dict[str, int]
    experiment: list[dict[str, Any]] = Field(default_factory=list)  # X2: reply rate per writer strategy arm


class MemoryItem(BaseModel):
    kind: Literal["style", "outcome"]
    key: str
    text: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class Health(BaseModel):
    api: bool
    marketplace: bool
    portfolio: bool
    workspace: bool
    degraded: bool
    message: str | None = None
    queued_jobs: int = 0


class UploadOut(BaseModel):
    filename: str
    doc_type: str
    documents: int
    chunks: int | None = None
