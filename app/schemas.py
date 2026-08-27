"""Request/response models for the HTTP API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal[
    "developer", "production_support", "business_analyst",
    "business_analyst_tech", "business_analyst_non_tech", "general",
]
Level = Literal["foundation", "intermediate", "advanced"]


class DocumentOut(BaseModel):
    id: str
    title: str
    filename: str | None = None
    source_type: str
    consultant: str | None = None
    client: str | None = None
    department: str | None = None
    role: str | None = None
    placement_period: str | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    object_key: str | None = None
    n_chunks: int = 0
    status: str = "indexed"
    created_at: str | None = None


class IngestResult(BaseModel):
    document_id: str
    title: str
    chunks: int
    source_type: str


class SearchHitOut(BaseModel):
    chunk_id: int
    document_id: str
    document_title: str
    locator: str
    heading: str
    text: str
    client: str | None = None
    department: str | None = None
    role: str | None = None
    consultant: str | None = None
    score: float
    matched_by: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None
    role: Role | None = None
    client: str | None = None
    department: str | None = None
    use_knowledge_base: bool = True


class ChatSessionOut(BaseModel):
    id: str
    title: str | None = None
    role_focus: str | None = None
    created_at: str | None = None


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str | None = None


class InterviewStartRequest(BaseModel):
    role: Role = "general"
    level: Level = "intermediate"
    topic: str | None = Field(default=None, max_length=200)
    # None means "any client" - questions stay generic across investment banks.
    client: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)


class InterviewQuestionOut(BaseModel):
    session_id: str
    turn_id: int
    ordinal: int
    question: str
    kind: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class InterviewAnswerRequest(BaseModel):
    session_id: str
    turn_id: int
    answer: str = Field(min_length=1, max_length=8000)


class InterviewFeedbackOut(BaseModel):
    turn_id: int
    score: int
    verdict: str
    strengths: list[str]
    # Gaps are bucketed by seniority so the score can be driven by must_know alone
    # and senior-level extras stay informative rather than punitive.
    must_know: list[str] = Field(default_factory=list)
    good_to_know: list[str] = Field(default_factory=list)
    advanced_bonus: list[str] = Field(default_factory=list)
    process_covered: dict[str, bool] = Field(default_factory=dict)
    # Populated only for command-line questions; empty for domain/competency ones.
    command_walkthrough: list[dict[str, str]] = Field(default_factory=list)
    minimum_commands: list[str] = Field(default_factory=list)
    # Populated only when the question was a coding exercise; empty otherwise.
    code_correctness: str = ""
    complexity_verdict: str = ""
    edge_cases_missed: list[str] = Field(default_factory=list)
    model_solution: str = ""
    model_answer: str
    follow_up_question: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class InterviewSummaryOut(BaseModel):
    session_id: str
    role: str
    level: str
    topic: str | None = None
    client: str | None = None
    department: str | None = None
    answered: int
    average_score: float | None = None
    turns: list[dict[str, Any]] = Field(default_factory=list)


class StatusOut(BaseModel):
    llm_configured: bool
    model: str
    documents: int
    chunks: int
    embedded_chunks: int
    missing_embeddings: int
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    roles: dict[str, int] = Field(default_factory=dict)
    clients: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)
    admin_token_is_default: bool = False
    database: str = "sqlite"
    storage_backend: str = "local"
    retains_originals: bool = True
    password_protected: bool = False
