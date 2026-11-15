"""Departments, users, and the data scope they impose on knowledge bases."""

from __future__ import annotations


def _dept(client, name, parent_id=None, headers=None):
    response = client.post(
        "/api/departments",
        json={"name": name, "parent_id": parent_id},
        headers=headers or {},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _kb(client, name, department_id=None, headers=None):
    response = client.post(
        "/api/kb",
        json={"name": name, "department_id": department_id},
        headers=headers or {},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_tree_walks_parent_links(client):
    root = _dept(client, "集团")
    child = _dept(client, "研发中心", root["id"])
    leaf = _dept(client, "平台组", child["id"])

    assert leaf["path"] == f"/{root['id']}/{child['id']}/{leaf['id']}/"

    tree = client.get("/api/org/tree").json()
    assert tree[0]["id"] == root["id"]
    assert tree[0]["children"][0]["children"][0]["id"] == leaf["id"]


def test_moving_a_department_rewrites_the_subtree(client):
    root = _dept(client, "集团")
    east = _dept(client, "华东", root["id"])
    team = _dept(client, "交付组", east["id"])
    west = _dept(client, "华南", root["id"])

    moved = client.patch(f"/api/departments/{east['id']}", json={"parent_id": west["id"]})
    assert moved.status_code == 200, moved.text

    team_after = client.get("/api/departments").json()
    row = next(item for item in team_after if item["id"] == team["id"])
    assert row["path"].startswith(f"/{root['id']}/{west['id']}/{east['id']}/")


def test_cannot_move_a_department_into_its_own_subtree(client):
    root = _dept(client, "集团")
    child = _dept(client, "研发", root["id"])

    response = client.patch(f"/api/departments/{root['id']}", json={"parent_id": child["id"]})
    assert response.status_code == 409
    assert "own subtree" in response.json()["detail"]


def test_delete_is_blocked_while_referenced(client):
    root = _dept(client, "集团")
    child = _dept(client, "研发", root["id"])

    with_child = client.delete(f"/api/departments/{root['id']}")
    assert with_child.status_code == 409

    client.post("/api/users", json={"id": "u-1", "name": "张三", "dept_id": child["id"]})
    with_user = client.delete(f"/api/departments/{child['id']}")
    assert with_user.status_code == 409
    assert "users" in with_user.json()["detail"]

    client.delete("/api/users/u-1")
    _kb(client, "研发库", child["id"])
    with_kb = client.delete(f"/api/departments/{child['id']}")
    assert with_kb.status_code == 409
    assert "knowledge bases" in with_kb.json()["detail"]


def test_scope_hides_sibling_departments(client):
    root = _dept(client, "集团")
    east = _dept(client, "华东", root["id"])
    west = _dept(client, "华南", root["id"])
    _kb(client, "华东库", east["id"])
    west_kb = _kb(client, "华南库", west["id"])
    shared = _kb(client, "公共库")

    client.post(
        "/api/users", json={"id": "east-user", "name": "李明", "dept_id": east["id"], "role": "editor"}
    )
    east_headers = {"X-User-Id": "east-user", "X-User-Role": "editor"}

    visible = {item["id"] for item in client.get("/api/kb", headers=east_headers).json()}
    assert shared["id"] in visible
    assert west_kb["id"] not in visible

    blocked = client.get(f"/api/kb/{west_kb['id']}", headers=east_headers)
    assert blocked.status_code == 404


def test_unknown_user_sees_only_shared_bases(client):
    dept = _dept(client, "研发")
    private = _kb(client, "研发库", dept["id"])
    shared = _kb(client, "公共库")

    stranger = {"X-User-Id": "nobody", "X-User-Role": "viewer"}
    visible = {item["id"] for item in client.get("/api/kb", headers=stranger).json()}
    assert shared["id"] in visible
    assert private["id"] not in visible


def test_admin_sees_every_department(client):
    dept = _dept(client, "研发")
    private = _kb(client, "研发库", dept["id"])
    visible = {item["id"] for item in client.get("/api/kb").json()}
    assert private["id"] in visible


def test_only_admin_can_write_the_org(client):
    editor = {"X-User-Id": "ed", "X-User-Role": "editor"}
    response = client.post("/api/departments", json={"name": "研发"}, headers=editor)
    assert response.status_code == 403
    assert "org:write" in response.json()["detail"]

    assert client.get("/api/departments", headers=editor).status_code == 200


def test_building_a_base_outside_your_scope_is_forbidden(client):
    root = _dept(client, "集团")
    east = _dept(client, "华东", root["id"])
    west = _dept(client, "华南", root["id"])
    client.post(
        "/api/users", json={"id": "east-ed", "name": "王五", "dept_id": east["id"], "role": "editor"}
    )
    headers = {"X-User-Id": "east-ed", "X-User-Role": "editor"}

    response = client.post("/api/kb", json={"name": "越权库", "department_id": west["id"]}, headers=headers)
    assert response.status_code == 403
    assert "outside your scope" in response.json()["detail"]
