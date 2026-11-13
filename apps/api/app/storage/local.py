"""Filesystem object store: the default when no MinIO is around."""

from __future__ import annotations

import shutil
from pathlib import Path


class LocalObjectStore:
    backend = "local"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys are built from ids and a sanitised filename, but never trust
        # them: resolve and confirm the result stays inside the root.
        target = (self._root / key).resolve()
        if not str(target).startswith(str(self._root.resolve())):
            raise ValueError(f"key escapes the store: {key}")
        return target

    def put(self, key: str, data: bytes, content_type: str = "") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete_prefix(self, prefix: str) -> int:
        target = self._path(prefix)
        if target.is_dir():
            count = sum(1 for p in target.rglob("*") if p.is_file())
            shutil.rmtree(target, ignore_errors=True)
            return count
        if target.exists():
            target.unlink()
            return 1
        return 0

    def health(self) -> bool:
        return self._root.is_dir()
