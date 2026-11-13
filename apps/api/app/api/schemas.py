from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class KnowledgeBaseOut(BaseModel):
    id: str
    name: str
    description: str
    document_count: int = 0
    chunk_count: int = 0
    created_at: datetime


class DocumentOut(BaseModel):
    id: str
    kb_id: str
    filename: str
    mime: str
    size: int
    status: str
    error: str
    chunk_count: int
    created_at: datetime


class ChatRequest(BaseModel):
    kb_id: str
    question: str = Field(min_length=1, max_length=4000)


class RetrievalRequest(BaseModel):
    kb_id: str
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class EvalRunRequest(BaseModel):
    kb_id: str
    dataset: str = "sample"
    limit: int = Field(default=0, ge=0)
