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

    # --- chunked upload -------------------------------------------------
    # Parts live under .uploads/, outside the key space, so a half-finished
    # upload is never served as a document.

    def _upload_dir(self, upload_id: str) -> Path:
        if not upload_id or "/" in upload_id or "\\" in upload_id or upload_id.startswith("."):
            raise ValueError(f"bad upload id: {upload_id}")
        path = self._root / ".uploads" / upload_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_multipart(self, key: str, content_type: str = "") -> str:
        import uuid

        upload_id = uuid.uuid4().hex
        directory = self._upload_dir(upload_id)
        (directory / "key").write_text(key, encoding="utf-8")
        return upload_id

    def upload_part(self, key: str, upload_id: str, part_number: int, data: bytes) -> str:
        if part_number < 1 or part_number > 10_000:
            raise ValueError("part number out of range")
        directory = self._upload_dir(upload_id)
        (directory / f"{part_number:05d}.part").write_bytes(data)
        return str(part_number)

    def list_parts(self, key: str, upload_id: str) -> list[int]:
        directory = self._upload_dir(upload_id)
        return sorted(
            int(path.stem)
            for path in directory.glob("*.part")
            if path.stem.isdigit()
        )

    def complete_multipart(self, key: str, upload_id: str, part_numbers: list[int]) -> str:
        directory = self._upload_dir(upload_id)
        assembled = b"".join(
            (directory / f"{number:05d}.part").read_bytes() for number in part_numbers
        )
        self.put(key, assembled)
        shutil.rmtree(directory, ignore_errors=True)
        return key

    def abort_multipart(self, key: str, upload_id: str) -> None:
        shutil.rmtree(self._upload_dir(upload_id), ignore_errors=True)
