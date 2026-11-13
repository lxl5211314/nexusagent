"""Object store selection plus the key layout used for uploads."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import get_settings

from .base import ObjectStore
from .local import LocalObjectStore
from .s3 import S3ObjectStore

_store: ObjectStore | None = None

_UNSAFE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]")


def safe_name(filename: str) -> str:
    """Keep the extension and readable name, drop anything path-like."""
    base = Path(filename or "unnamed").name
    cleaned = _UNSAFE.sub("_", base).strip("._") or "unnamed"
    return cleaned[:120]


def document_key(kb_id: str, doc_id: str, filename: str, ordinal: int = 0) -> str:
    return f"{kb_id}/{doc_id}/{ordinal}-{safe_name(filename)}"


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        settings = get_settings()
        if settings.object_backend == "s3":
            _store = S3ObjectStore(
                endpoint=settings.s3_endpoint,
                bucket=settings.s3_bucket,
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                region=settings.s3_region,
            )
        else:
            _store = LocalObjectStore(settings.data_dir / "uploads")
    return _store


def reset_object_store() -> None:
    global _store
    _store = None
