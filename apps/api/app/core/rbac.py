"""Role-based access control at the HTTP edge.

The gateway authenticates the caller and stamps ``X-User-Id`` / ``X-User-Role``;
everything downstream reads those headers and checks a permission instead of
comparing role names inline. Unknown or missing roles downgrade to ``viewer`` so
a misconfigured proxy fails closed rather than open.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from app.core.tenant import normalize_tenant

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(
        {
            "kb:read",
            "kb:write",
            "doc:write",
            "mcp:call",
            "settings:write",
            "audit:read",
            "org:read",
            "org:write",
        }
    ),
    "editor": frozenset({"kb:read", "kb:write", "doc:write", "mcp:call", "org:read"}),
    "viewer": frozenset({"kb:read", "org:read"}),
}

ROLES = tuple(ROLE_PERMISSIONS)


def normalize_role(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return value if value in ROLE_PERMISSIONS else "viewer"


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str
    role: str

    @property
    def permissions(self) -> frozenset[str]:
        return ROLE_PERMISSIONS[self.role]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def get_identity(
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_user_role: str | None = Header(default=None),
) -> Identity:
    return Identity(
        tenant_id=normalize_tenant(x_tenant_id),
        user_id=(x_user_id or "anonymous").strip()[:64] or "anonymous",
        role=normalize_role(x_user_role),
    )


def require_permission(permission: str):
    """Dependency factory: 403 unless the caller's role grants ``permission``."""

    def dependency(identity: Identity = Depends(get_identity)) -> Identity:
        if not identity.can(permission):
            raise HTTPException(
                status_code=403,
                detail=f"role '{identity.role}' lacks permission '{permission}'",
            )
        return identity

    return dependency
