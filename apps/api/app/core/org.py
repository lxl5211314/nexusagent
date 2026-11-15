"""Departments and the data scope they imply.

Identity at the edge is a header triple (tenant, user, role). The department is
not in that header on purpose: it lives in the database, so a stale or forged
header cannot widen what a user sees. A knowledge base belongs to one
department (or to the whole tenant when ``department_id`` is NULL), and access
is decided by the materialised path of the caller's own department.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import Identity
from app.db.models import Department, KnowledgeBase, User


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    user_id: str
    role: str
    dept_id: str | None
    dept_path: str | None

    @property
    def unrestricted(self) -> bool:
        """Admins see the whole tenant; everyone else sees their subtree."""
        return self.role == "admin"


def can_access_kb(db: Session, scope: Scope, kb: KnowledgeBase) -> bool:
    if scope.unrestricted:
        return True
    if not kb.department_id:
        return True  # tenant-wide corpus
    if not scope.dept_path:
        return False  # no department, no subtree
    path = dept_path(db, scope.tenant_id, kb.department_id)
    return bool(path and path.startswith(scope.dept_path))


def dept_path(db: Session, tenant_id: str, dept_id: str) -> str | None:
    if not dept_id:
        return None
    row = db.get(Department, dept_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row.path


def resolve_scope(db: Session, identity: Identity) -> Scope:
    """Attach the caller's department to the header identity.

    An unknown user simply has no department: they still read tenant-wide
    corpora, which keeps a fresh install usable without seeding users.
    """
    row = db.execute(
        select(User).where(User.tenant_id == identity.tenant_id, User.id == identity.user_id)
    ).scalar_one_or_none()
    if row is None:
        return Scope(identity.tenant_id, identity.user_id, identity.role, None, None)
    path = dept_path(db, identity.tenant_id, row.dept_id or "")
    return Scope(identity.tenant_id, identity.user_id, identity.role, row.dept_id, path)


def descendant_ids(db: Session, tenant_id: str, dept_id: str) -> list[str]:
    prefix = dept_path(db, tenant_id, dept_id)
    if not prefix:
        return []
    rows = db.execute(
        select(Department.id).where(
            Department.tenant_id == tenant_id, Department.path.startswith(prefix)
        )
    ).scalars()
    return list(rows)


def create_dept(
    db: Session, tenant_id: str, name: str, parent_id: str | None
) -> Department:
    parent = None
    if parent_id:
        parent = db.get(Department, parent_id)
        if parent is None or parent.tenant_id != tenant_id:
            raise ValueError("parent department not found")
    dept = Department(tenant_id=tenant_id, name=name, parent_id=parent_id, path="")
    db.add(dept)
    db.flush()
    dept.path = (parent.path if parent else "/") + dept.id + "/"
    db.flush()
    return dept


def move_dept(db: Session, dept: Department, parent_id: str | None) -> None:
    """Re-parent a node, rewriting the prefix for the whole subtree.

    Rejects a move into its own branch: the prefix rewrite would otherwise
    detach the subtree from the tree entirely.
    """
    if parent_id == dept.id:
        raise ValueError("a department cannot be its own parent")
    parent = None
    if parent_id:
        parent = db.get(Department, parent_id)
        if parent is None or parent.tenant_id != dept.tenant_id:
            raise ValueError("parent department not found")
        if parent.path.startswith(dept.path):
            raise ValueError("cannot move a department into its own subtree")
    old_prefix = dept.path
    new_prefix = (parent.path if parent else "/") + dept.id + "/"
    if old_prefix == new_prefix:
        dept.parent_id = parent_id
        return
    rows = db.execute(
        select(Department).where(
            Department.tenant_id == dept.tenant_id,
            Department.path.startswith(old_prefix),
        )
    ).scalars()
    for row in rows:
        row.path = new_prefix + row.path[len(old_prefix):]
    dept.parent_id = parent_id
    db.flush()


def visible_kb_filter(scope: Scope):
    """SQL predicate matching the knowledge bases this caller may list."""
    if scope.unrestricted:
        return KnowledgeBase.tenant_id == scope.tenant_id
    if not scope.dept_path:
        return (KnowledgeBase.tenant_id == scope.tenant_id) & (
            KnowledgeBase.department_id.is_(None)
        )
    subtree = (
        select(Department.id)
        .where(
            Department.tenant_id == scope.tenant_id,
            Department.path.startswith(scope.dept_path),
        )
        .scalar_subquery()
    )
    return (
        (KnowledgeBase.tenant_id == scope.tenant_id)
        & (
            KnowledgeBase.department_id.is_(None)
            | KnowledgeBase.department_id.in_(subtree)
        )
    )
