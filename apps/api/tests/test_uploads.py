"""Chunked upload: parts can arrive out of order and resume after a drop."""

from __future__ import annotations


def _kb(client, name: str) -> str:
    return client.post("/api/kb", json={"name": name}).json()["id"]


def test_parts_assemble_into_the_original_file(client) -> None:
    kb_id = _kb(client, "chunked")
    started = client.post(
        f"/api/kb/{kb_id}/uploads",
        json={"filename": "guide.md", "content_type": "text/markdown"},
    )
    assert started.status_code == 201, started.text
    upload = started.json()
    assert upload["uploaded"] == []
    assert upload["part_size"] == 5 * 1024 * 1024

    first = b"# RRF\n\nReciprocal rank fusion merges ranked lists.\n"
    second = b"\n## Rerank\n\nThe cross encoder reorders the top candidates.\n"

    # order matters only in the sense that the client names the parts
    part_two = client.put(
        f"/api/kb/{kb_id}/uploads/{upload['upload_id']}/parts/2", content=second
    )
    assert part_two.status_code == 200, part_two.text
    assert part_two.json()["uploaded"] == [2]

    resumed = client.get(f"/api/kb/{kb_id}/uploads/{upload['upload_id']}")
    assert resumed.json()["uploaded"] == [2]
    assert resumed.json()["bytes_received"] == len(second)

    assert (
        client.put(
            f"/api/kb/{kb_id}/uploads/{upload['upload_id']}/parts/1", content=first
        ).status_code
        == 200
    )

    done = client.post(
        f"/api/kb/{kb_id}/uploads/{upload['upload_id']}/complete",
        json={"parts": [1, 2]},
    )
    assert done.status_code == 201, done.text
    document = done.json()
    assert document["filename"] == "guide.md"
    assert document["chunk_count"] >= 1

    content = client.get(f"/api/kb/{kb_id}/documents/{document['id']}/content")
    assert content.status_code == 200
    assert content.content == first + second

    # the upload row is gone once completed
    assert (
        client.get(f"/api/kb/{kb_id}/uploads/{upload['upload_id']}").status_code == 404
    )


def test_complete_reports_missing_parts(client) -> None:
    kb_id = _kb(client, "missing-parts")
    upload = client.post(
        f"/api/kb/{kb_id}/uploads", json={"filename": "a.md"}
    ).json()
    client.put(f"/api/kb/{kb_id}/uploads/{upload['upload_id']}/parts/1", content=b"one")

    response = client.post(
        f"/api/kb/{kb_id}/uploads/{upload['upload_id']}/complete",
        json={"parts": [1, 2]},
    )
    assert response.status_code == 409
    assert "2" in response.json()["detail"]


def test_abort_discards_the_upload(client) -> None:
    kb_id = _kb(client, "abort")
    upload = client.post(
        f"/api/kb/{kb_id}/uploads", json={"filename": "b.md"}
    ).json()
    client.put(f"/api/kb/{kb_id}/uploads/{upload['upload_id']}/parts/1", content=b"x")

    assert (
        client.delete(f"/api/kb/{kb_id}/uploads/{upload['upload_id']}").status_code == 204
    )
    assert (
        client.get(f"/api/kb/{kb_id}/uploads/{upload['upload_id']}").status_code == 404
    )
    assert client.get(f"/api/kb/{kb_id}/documents").json() == []


def test_upload_is_tenant_scoped(client) -> None:
    kb_id = _kb(client, "tenant-upload")
    upload = client.post(
        f"/api/kb/{kb_id}/uploads", json={"filename": "c.md"}
    ).json()

    other = client.get(
        f"/api/kb/{kb_id}/uploads/{upload['upload_id']}",
        headers={"X-Tenant-Id": "someone-else", "X-User-Role": "admin"},
    )
    assert other.status_code == 404


def test_viewer_cannot_start_an_upload(client) -> None:
    kb_id = _kb(client, "viewer-upload")
    denied = client.post(
        f"/api/kb/{kb_id}/uploads",
        json={"filename": "d.md"},
        headers={"X-User-Role": "viewer"},
    )
    assert denied.status_code == 403
