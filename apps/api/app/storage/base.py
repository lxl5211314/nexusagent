"""Raw document storage.

Chunks are derived data; the uploaded bytes are the thing a user asks for when
they click a citation. Keeping them also means a re-index never needs the
browser to hold the file.
"""

from __future__ import annotations

from typing import Protocol


class ObjectStore(Protocol):
    backend: str

    def put(self, key: str, data: bytes, content_type: str = "") -> str: ...

    def get(self, key: str) -> bytes: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def health(self) -> bool: ...

    # Chunked upload: a browser can send 5 MB parts and resume after a drop
    # because the unfinished upload keeps its parts object-side.
    def create_multipart(self, key: str, content_type: str = "") -> str: ...

    def upload_part(self, key: str, upload_id: str, part_number: int, data: bytes) -> str: ...

    def list_parts(self, key: str, upload_id: str) -> list[int]: ...

    def complete_multipart(self, key: str, upload_id: str, part_numbers: list[int]) -> str: ...

    def abort_multipart(self, key: str, upload_id: str) -> None: ...

