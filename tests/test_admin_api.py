"""Tests for syncplay.admin — the FastAPI admin app.

Uses an in-memory SQLite store and FastAPI's TestClient. Run:
    python -m pytest tests/test_admin_api.py -v
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "syncplay"))
import admin  # noqa: E402
import db  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    store = db.BackendStore("sqlite::memory:")
    # seed some data
    store.upsert_user("alice", 100)
    store.upsert_watch("movie-room", "movie.mkv", 123456, 500, 3600, 100)
    store.upsert_watch("movie-room", "trailer.mp4", 1000, 30, 60, 100)
    store.save_room("existing", "a.mkv\nb.mkv", 1, 42.0, 100)
    store.set_server_info("version", "1.7.7")
    store.set_server_info("started_at", "100")
    store.set_server_info("updated_at", str(int(__import__("time").time())))
    monkeypatch.setattr(admin, "_store", store)
    monkeypatch.setattr(admin, "_admin_token", None)
    return TestClient(admin.app)


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "syncplay-history admin" in r.text


def test_health_shape(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["server"]["version"] == "1.7.7"
    assert j["server"]["alive"] is True
    assert j["server"]["db_ok"] is True
    assert j["rooms"]["persistent"] == 1
    assert j["users"]["total"] == 1


def test_health_alive_false_when_stale(client, monkeypatch):
    store = admin._store
    store.set_server_info("updated_at", str(int(__import__("time").time()) - 200))
    r = client.get("/api/health")
    assert r.json()["server"]["alive"] is False


def test_rooms_list(client):
    r = client.get("/api/rooms")
    assert r.status_code == 200
    rooms = {room["name"]: room for room in r.json()["rooms"]}
    assert "existing" in rooms
    assert rooms["existing"]["persistent"] is True
    assert rooms["existing"]["playlist"] == ["a.mkv", "b.mkv"]


def test_create_room(client):
    r = client.post("/api/rooms", json={"name": "newroom"})
    assert r.status_code == 200
    assert r.json()["name"] == "newroom"
    rooms = {room["name"] for room in client.get("/api/rooms").json()["rooms"]}
    assert "newroom" in rooms


def test_create_room_permanent(client):
    r = client.post("/api/rooms", json={"name": "permroom", "permanent": True})
    assert r.status_code == 200
    rooms = {room["name"]: room for room in client.get("/api/rooms").json()["rooms"]}
    assert rooms["permroom"]["permanent"] is True


def test_create_room_duplicate_409(client):
    r = client.post("/api/rooms", json={"name": "existing"})
    assert r.status_code == 409


def test_create_room_empty_name_400(client):
    r = client.post("/api/rooms", json={"name": ""})
    assert r.status_code == 400


def test_get_room(client):
    r = client.get("/api/rooms/existing")
    assert r.status_code == 200
    assert r.json()["playlist"] == ["a.mkv", "b.mkv"]


def test_get_room_404(client):
    assert client.get("/api/rooms/nope").status_code == 404


def test_delete_room(client):
    r = client.delete("/api/rooms/existing")
    assert r.status_code == 200
    assert "existing" not in {room["name"] for room in client.get("/api/rooms").json()["rooms"]}


def test_delete_permanent_room_409(client):
    client.post("/api/rooms", json={"name": "permroom", "permanent": True})
    r = client.delete("/api/rooms/permroom")
    assert r.status_code == 409


def test_delete_permanent_room_with_confirm(client):
    client.post("/api/rooms", json={"name": "permroom", "permanent": True})
    r = client.request("DELETE", "/api/rooms/permroom", json={"confirm_permanent": True})
    assert r.status_code == 200
    assert "permroom" not in {room["name"] for room in client.get("/api/rooms").json()["rooms"]}


def test_delete_room_clears_related(client):
    # seed related rows that must be swept on delete
    client.post("/api/rooms/existing/password", json={"password": "AB-123-456"})
    client.put("/api/rooms/existing/motd", json={"motd": "hi"})
    r = client.delete("/api/rooms/existing")
    assert r.status_code == 200
    assert client.get("/api/rooms/existing/password").status_code == 404
    assert client.get("/api/rooms/existing/motd").status_code == 404


def test_set_permanent_toggle(client):
    r = client.post("/api/rooms/existing/permanent", json={"permanent": True})
    assert r.status_code == 200
    assert client.get("/api/rooms/existing").json()["permanent"] is True
    r = client.post("/api/rooms/existing/permanent", json={"permanent": False})
    assert r.status_code == 200
    assert client.get("/api/rooms/existing").json()["permanent"] is False


def test_set_playlist(client):
    r = client.post("/api/rooms/existing/playlist", json={"playlist": ["x.mkv", "y.mkv"], "index": 1})
    assert r.status_code == 200
    room = client.get("/api/rooms/existing").json()
    assert room["playlist"] == ["x.mkv", "y.mkv"]
    assert room["playlist_index"] == 1


# ---------------------------------------------------------------------------
# Room passwords
# ---------------------------------------------------------------------------


def test_set_room_password(client):
    r = client.post("/api/rooms/existing/password", json={"password": "AB-123-456"})
    assert r.status_code == 200
    j = r.json()
    assert j["room"] == "existing"
    assert j["password"] == "AB-123-456"


def test_get_room_password(client):
    client.post("/api/rooms/existing/password", json={"password": "AB-123-456"})
    r = client.get("/api/rooms/existing/password")
    assert r.status_code == 200
    assert r.json()["password"] == "AB-123-456"


def test_get_room_password_404(client):
    r = client.get("/api/rooms/existing/password")
    assert r.status_code == 404


def test_change_room_password(client):
    client.post("/api/rooms/existing/password", json={"password": "AB-123-456"})
    client.post("/api/rooms/existing/password", json={"password": "CD-789-012"})
    r = client.get("/api/rooms/existing/password")
    assert r.json()["password"] == "CD-789-012"


def test_delete_room_password(client):
    client.post("/api/rooms/existing/password", json={"password": "AB-123-456"})
    r = client.delete("/api/rooms/existing/password")
    assert r.status_code == 200
    assert client.get("/api/rooms/existing/password").status_code == 404


def test_set_room_password_invalid_format(client):
    r = client.post("/api/rooms/existing/password", json={"password": "weak"})
    assert r.status_code == 400


def test_set_room_password_empty(client):
    r = client.post("/api/rooms/existing/password", json={"password": ""})
    assert r.status_code == 400


def test_set_room_password_unknown_room_404(client):
    r = client.post("/api/rooms/ghost/password", json={"password": "AB-123-456"})
    assert r.status_code == 404
    assert "room not found" in r.json()["detail"]


def test_set_room_password_derived_name_400(client):
    # a controlled room name in "+base:hash" form is rejected (regex guard)
    r = client.post("/api/rooms/+existing:DEADBEEF1234/password", json={"password": "AB-123-456"})
    assert r.status_code == 400
    assert "base room name" in r.json()["detail"]


def test_get_room_password_unknown_room_404(client):
    r = client.get("/api/rooms/ghost/password")
    assert r.status_code == 404
    assert "room not found" in r.json()["detail"]


def test_room_password_hash_matches_upstream(monkeypatch):
    """The duplicated hash computation must match RoomPasswordProvider."""
    monkeypatch.syspath_prepend(os.path.join(os.path.dirname(__file__), ".."))
    from syncplay.utils import RoomPasswordProvider

    for base, password, salt in [
        ("movie-night", "AB-123-456", "test-salt"),
        ("SextaDosAnimes", "CD-789-012", "another/salt"),
    ]:
        expected = RoomPasswordProvider.getControlledRoomName(base, password, salt)
        actual = admin._controlled_room_name(base, password, salt)
        assert actual == expected


def test_history(client):
    r = client.get("/api/history")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 2


def test_history_filter_room(client):
    r = client.get("/api/history?room=movie-room")
    assert len(r.json()["rows"]) == 2


def test_history_filter_q(client):
    r = client.get("/api/history?q=trailer")
    assert len(r.json()["rows"]) == 1
    assert r.json()["rows"][0]["file"] == "trailer.mp4"


def test_users(client):
    r = client.get("/api/users")
    assert r.status_code == 200
    users = r.json()["users"]
    assert users[0]["username"] == "alice"
    assert users[0]["first_seen"] == 100


def test_delete_history_row(client):
    r = client.request("DELETE", "/api/history?room=movie-room&file=trailer.mp4")
    assert r.status_code == 200
    rows = client.get("/api/history?room=movie-room").json()["rows"]
    assert len(rows) == 1
    assert rows[0]["file"] == "movie.mkv"
    # deleting again is idempotent (no row to remove)
    assert client.request("DELETE", "/api/history?room=movie-room&file=trailer.mp4").status_code == 200


def test_delete_history_requires_params(client):
    assert client.request("DELETE", "/api/history").status_code == 422
    assert client.request("DELETE", "/api/history?room=movie-room").status_code == 422


def test_delete_user(client):
    store = admin._store
    store.upsert_user("bob", 200)
    r = client.delete("/api/users/bob")
    assert r.status_code == 200
    users = client.get("/api/users").json()["users"]
    assert "bob" not in {u["username"] for u in users}
    # no cascade: watch history is untouched
    assert len(client.get("/api/history").json()["rows"]) == 2


# ---------------------------------------------------------------------------
# Settings (MOTD / persistent-rooms notice)
# ---------------------------------------------------------------------------


def test_settings_default(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    j = r.json()
    assert j["motd"] is None
    assert j["persistent_notice"] is None
    assert j["persistent_notice_enabled"] is True  # default: on


def test_settings_put_get_roundtrip(client):
    r = client.put("/api/settings", json={
        "motd": "Welcome to $room",
        "persistent_notice": "Custom notice text",
        "persistent_notice_enabled": False,
    })
    assert r.status_code == 200
    assert r.json()["persistent_notice_enabled"] is False
    j = client.get("/api/settings").json()
    assert j["motd"] == "Welcome to $room"
    assert j["persistent_notice"] == "Custom notice text"
    assert j["persistent_notice_enabled"] is False
    # re-enable
    client.put("/api/settings", json={"persistent_notice_enabled": True})
    assert client.get("/api/settings").json()["persistent_notice_enabled"] is True


def test_settings_partial_update(client):
    client.put("/api/settings", json={"motd": "only motd"})
    j = client.get("/api/settings").json()
    assert j["motd"] == "only motd"
    assert j["persistent_notice"] is None  # untouched


# ---------------------------------------------------------------------------
# Per-room MOTD
# ---------------------------------------------------------------------------


def test_room_motd_roundtrip(client):
    r = client.put("/api/rooms/existing/motd", json={"motd": "Watch parties here"})
    assert r.status_code == 200
    assert r.json()["motd"] == "Watch parties here"
    r = client.get("/api/rooms/existing/motd")
    assert r.status_code == 200
    assert r.json()["room"] == "existing"
    # upsert
    client.put("/api/rooms/existing/motd", json={"motd": "updated"})
    assert client.get("/api/rooms/existing/motd").json()["motd"] == "updated"


def test_room_motd_delete(client):
    client.put("/api/rooms/existing/motd", json={"motd": "x"})
    r = client.delete("/api/rooms/existing/motd")
    assert r.status_code == 200
    assert client.get("/api/rooms/existing/motd").status_code == 404


def test_room_motd_unknown_room_404(client):
    assert client.get("/api/rooms/ghost/motd").status_code == 404
    assert client.put("/api/rooms/ghost/motd", json={"motd": "x"}).status_code == 404


def test_room_motd_empty_404(client):
    assert client.get("/api/rooms/existing/motd").status_code == 404


# ---------------------------------------------------------------------------
# Auth (ADMIN_TOKEN set)
# ---------------------------------------------------------------------------


@pytest.fixture()
def token_client(monkeypatch):
    store = db.BackendStore("sqlite::memory:")
    monkeypatch.setattr(admin, "_store", store)
    monkeypatch.setattr(admin, "_admin_token", "sekret")
    return TestClient(admin.app)


def test_token_required(token_client):
    assert token_client.get("/api/health").status_code == 401
    assert token_client.get("/api/rooms").status_code == 401
    assert token_client.post("/api/rooms/existing/password", json={"password": "AB-123-456"}).status_code == 401


def test_token_wrong(token_client):
    r = token_client.get("/api/health", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_token_correct(token_client):
    r = token_client.get("/api/health", headers={"X-Admin-Token": "sekret"})
    assert r.status_code == 200


def test_token_correct_query_param(token_client):
    r = token_client.get("/api/health?token=sekret")
    assert r.status_code == 200


def test_index_open_even_with_token(token_client):
    # the static page itself stays open; API calls are gated
    assert token_client.get("/").status_code == 200
