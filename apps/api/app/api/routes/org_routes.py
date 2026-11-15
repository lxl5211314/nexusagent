"""Departments and users inside a tenant.

Two rules keep this honest:

* the tree is stored with a materialised path, so "everything under 华东" is a
  prefix match and re-parenting rewrites one prefix for the whole branch;
* a move into your own subtree is refused — rewriting the prefix would detach
  the branch from the root and silently hide it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    DepartmentIn,
    DepartmentNode,
    DepartmentOut,
    DepartmentPatch,
    UserIn,
    UserOut,
    UserPatch,
)
from app.core import org
from app.core.rbac import Identity, normalize_role, require_permission
from app.db.base import get_db
from app.db.models import Department, KnowledgeBase, User
from app.services import audit

router = APIRouter(tags=["org"])

MAX_DEPTH = 12


def _owned_dept(db: Session, dept_id: str, tenant_id: str) -> Department:
    dept = db.get(Department, dept_id)
    if dept is None or dept.tenant_id != tenant_id:
        raise HTTPException(404, "department not found")
    return dept


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:read")),
) -> list[Department]:
    rows = db.execute(
        select(Department)
        .where(Department.tenant_id == identity.tenant_id)
        .order_by(Department.path)
    ).scalars()
    return list(rows)


@router.get("/org/tree", response_model=list[DepartmentNode])
def org_tree(
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:read")),
) -> list[DepartmentNode]:
    rows = list(
        db.execute(
            select(Department)
            .where(Department.tenant_id == identity.tenant_id)
            .order_by(Department.path)
        ).scalars()
    )
    nodes = {
        row.id: DepartmentNode(
            id=row.id,
            name=row.name,
            parent_id=row.parent_id,
            path=row.path,
            created_at=row.created_at,
            children=[],
        )
        for row in rows
    }
    roots: list[DepartmentNode] = []
    for row in rows:
        node = nodes[row.id]
        parent = nodes.get(row.parent_id or "")
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
    return roots


@router.post("/departments", response_model=DepartmentOut, status_code=201)
def create_department(
    payload: DepartmentIn,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:write")),
) -> Department:
    if payload.parent_id:
        parent = _owned_dept(db, payload.parent_id, identity.tenant_id)
        if parent.path.count("/") >= MAX_DEPTH + 1:
            raise HTTPException(422, f"department tree is deeper than {MAX_DEPTH} levels")
    try:
        dept = org.create_dept(db, identity.tenant_id, payload.name, payload.parent_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="dept.create",
        resource_type="department",
        resource_id=dept.id,
        actor=identity.user_id,
        detail={"name": dept.name, "parent_id": dept.parent_id},
    )
    db.commit()
    db.refresh(dept)
    return dept


@router.patch("/departments/{dept_id}", response_model=DepartmentOut)
def patch_department(
    dept_id: str,
    payload: DepartmentPatch,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:write")),
) -> Department:
    dept = _owned_dept(db, dept_id, identity.tenant_id)
    if payload.name is not None:
        dept.name = payload.name
    if "parent_id" in payload.model_fields_set:
        try:
            org.move_dept(db, dept, payload.parent_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit.record(
            db,
            tenant_id=identity.tenant_id,
            action="dept.move",
            resource_type="department",
            resource_id=dept.id,
            actor=identity.user_id,
            detail={"parent_id": payload.parent_id},
        )
    db.commit()
    db.refresh(dept)
    return dept


@router.delete("/departments/{dept_id}", status_code=204)
def delete_department(
    dept_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:write")),
) -> None:
    dept = _owned_dept(db, dept_id, identity.tenant_id)
    children = db.execute(
        select(Department.id).where(
            Department.tenant_id == identity.tenant_id, Department.parent_id == dept.id
        )
    ).first()
    if children:
        raise HTTPException(409, "department still has children")
    users = db.execute(select(User.id).where(User.dept_id == dept.id)).first()
    if users:
        raise HTTPException(409, "department still has users")
    kbs = db.execute(select(KnowledgeBase.id).where(KnowledgeBase.department_id == dept.id)).first()
    if kbs:
        raise HTTPException(409, "department still owns knowledge bases")
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="dept.delete",
        resource_type="department",
        resource_id=dept.id,
        actor=identity.user_id,
        detail={"name": dept.name},
    )
    db.delete(dept)
    db.commit()


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:read")),
) -> list[User]:
    rows = db.execute(
        select(User).where(User.tenant_id == identity.tenant_id).order_by(User.id)
    ).scalars()
    return list(rows)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserIn,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:write")),
) -> User:
    existing = db.get(User, payload.id)
    if existing is not None and existing.tenant_id == identity.tenant_id:
        raise HTTPException(409, "user already exists in this tenant")
    if payload.dept_id:
        _owned_dept(db, payload.dept_id, identity.tenant_id)
    user = User(
        id=payload.id,
        tenant_id=identity.tenant_id,
        name=payload.name,
        dept_id=payload.dept_id,
        role=normalize_role(payload.role),
    )
    db.add(user)
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="user.create",
        resource_type="user",
        resource_id=user.id,
        actor=identity.user_id,
        detail={"role": user.role, "dept_id": user.dept_id},
    )
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: str,
    payload: UserPatch,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:write")),
) -> User:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != identity.tenant_id:
        raise HTTPException(404, "user not found")
    if payload.name is not None:
        user.name = payload.name
    if "dept_id" in payload.model_fields_set:
        if payload.dept_id:
            _owned_dept(db, payload.dept_id, identity.tenant_id)
        user.dept_id = payload.dept_id
    if payload.role is not None:
        user.role = normalize_role(payload.role)
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="user.update",
        resource_type="user",
        resource_id=user.id,
        actor=identity.user_id,
        detail={"role": user.role, "dept_id": user.dept_id},
    )
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    identity: Identity = Depends(require_permission("org:write")),
) -> None:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != identity.tenant_id:
        raise HTTPException(404, "user not found")
    audit.record(
        db,
        tenant_id=identity.tenant_id,
        action="user.delete",
        resource_type="user",
        resource_id=user.id,
        actor=identity.user_id,
        detail={"name": user.name},
    )
    db.delete(user)
    db.commit()
