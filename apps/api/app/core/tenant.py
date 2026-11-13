"""Tenant resolution.

The gateway (or any trusted caller) puts the tenant on ``X-Tenant-Id``. We never
trust arbitrary text: values are restricted to a small safe alphabet so a header
cannot be used to smuggle SQL, glob or filter syntax into downstream stores
(Milvus filters in particular are string-interpolated).
"""

from __future__ import annotations

import re

from fastapi import Header

DEFAULT_TENANT = "local"
MAX_TENANT_LEN = 32
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
_VALID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")


def normalize_tenant(raw: str | None) -> str:
    """Return a safe tenant id, falling back to the default tenant."""
    if not raw:
        return DEFAULT_TENANT
    cleaned = _SAFE.sub("-", raw.strip())[:MAX_TENANT_LEN]
    if not _VALID.match(cleaned):
        return DEFAULT_TENANT
    return cleaned


def get_tenant(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> str:
    """FastAPI dependency: resolved tenant for the current request."""
    return normalize_tenant(x_tenant_id)
