from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    department_id: str | None = None


class KnowledgeBaseOut(BaseModel):
    id: str
    name: str
    description: str
    department_id: str | None = None
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


class AuditOut(BaseModel):
    id: int
    tenant_id: str
    actor: str
    action: str
    resource_type: str
    resource_id: str
    detail: str
    created_at: datetime


class TenantOut(BaseModel):
    tenant_id: str


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: str | None = None


class DepartmentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    parent_id: str | None = None


class DepartmentOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    path: str
    created_at: datetime


class DepartmentNode(DepartmentOut):
    children: list[DepartmentNode] = []


class UserIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = ""
    dept_id: str | None = None
    role: str = "viewer"


class UserPatch(BaseModel):
    name: str | None = None
    dept_id: str | None = None
    role: str | None = None


class UserOut(BaseModel):
    id: str
    name: str
    dept_id: str | None
    role: str
    created_at: datetime


DepartmentNode.model_rebuild()
