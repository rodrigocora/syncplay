"""Admin web app for the syncplay-history deployment.

A small FastAPI app exposing the same backend database as the server and the
proxy. Binds to 127.0.0.1:ADMIN_PORT (default 12347) inside the container and
is meant to be exposed through the same-host reverse proxy as the main
service (see DOCKER.md, "Behind a reverse proxy").

Auth: if ADMIN_TOKEN is set, every /api/* request must carry it in the
X-Admin-Token header (or ?token= for the static page). Unset = open access
(roadmap: login).

Writes go straight to the backend DB; the syncplay server picks them up on
its next restart (the server reads rooms/permanent rooms at startup). This is
documented on the page itself.
"""
import argparse
import hashlib
import os
import re
import secrets
import time
from contextlib import asynccontextmanager

import db
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(_app):
    global _store
    if _store is None:
        _store = db.open_store_with_retry()
    yield


app = FastAPI(title="syncplay-history admin", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "resources")), name="static")

_store = None
_admin_token = None


def get_store():
    return _store


def require_token(request: Request, x_admin_token: str | None = Header(default=None)):
    if not _admin_token:
        return
    token = x_admin_token or request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    if not secrets.compare_digest(token, _admin_token):
        raise HTTPException(status_code=401, detail="invalid token")


def _row_to_dict(row):
    room, file, size, watched, percentage, duration, stopped_at, started_at, last_seen = row
    return {
        "room": room,
        "file": file,
        "size": size,
        "watched": watched,
        "percentage": percentage,
        "duration": duration,
        "stopped_at": stopped_at,
        "started_at": started_at,
        "last_seen": last_seen,
    }


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    with open(os.path.join(os.path.dirname(__file__), "resources", "admin.html"), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/api/health", dependencies=[Depends(require_token)])
def health():
    store = get_store()
    info = store.server_info()
    now = int(time.time())
    try:
        started_at = int(info.get("started_at") or 0)
    except (TypeError, ValueError):
        started_at = 0
    try:
        updated_at = int(info.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0
    alive = bool(updated_at) and (now - updated_at) <= 90  # 3x the 30s heartbeat
    rooms = store.list_room_status()
    active_rooms = sum(1 for r in rooms if r[1] > 0)
    return {
        "server": {
            "version": info.get("version"),
            "started_at": started_at,
            "last_heartbeat": updated_at,
            "uptime_s": max(0, now - started_at) if started_at else 0,
            "alive": alive,
            "db_ok": store.db_ok(),
        },
        "rooms": {
            "active": active_rooms,
            "persistent": len(store.load_rooms()),
            "permanent": len(store.list_permanent()),
        },
        "users": {"total": store.count_users()},
    }


# ---------------------------------------------------------------------------
# Rooms
# ---------------------------------------------------------------------------


def _room_union(store):
    """Union of persistent_rooms + permanent_rooms + live room_status."""
    persistent = {r[0]: r for r in store.load_rooms()}
    permanent = set(store.list_permanent())
    live = {r[0]: r for r in store.list_room_status()}
    names = set(persistent) | permanent | set(live)
    rooms = []
    for name in sorted(names):
        entry = {
            "name": name,
            "persistent": name in persistent,
            "permanent": name in permanent,
            "playlist": [],
            "playlist_index": None,
            "position": None,
            "watchers": 0,
            "playing": False,
            "playlist_size": 0,
            "updated_at": None,
        }
        if name in persistent:
            _, playlist, playlist_index, position, _ = persistent[name]
            entry["playlist"] = (playlist or "").split("\n") if playlist else []
            entry["playlist_index"] = playlist_index
            entry["position"] = position
        if name in live:
            _, watchers, playing, position, playlist_size, updated_at = live[name]
            entry["watchers"] = watchers
            entry["playing"] = bool(playing)
            if entry["position"] is None:
                entry["position"] = position
            entry["playlist_size"] = playlist_size
            entry["updated_at"] = updated_at
        rooms.append(entry)
    return rooms


@app.get("/api/rooms", dependencies=[Depends(require_token)])
def list_rooms():
    return {"rooms": _room_union(get_store())}


@app.post("/api/rooms", dependencies=[Depends(require_token)])
def create_room(payload: dict):
    store = get_store()
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if len(name) > 35:
        raise HTTPException(status_code=400, detail="name too long (max 35)")
    if store.is_permanent(name) or any(r[0] == name for r in store.load_rooms()):
        raise HTTPException(status_code=409, detail="room already exists")
    if payload.get("permanent"):
        store.set_permanent(name)
    else:
        store.save_room(name, "", 0, 0.0, int(time.time()))
    return {"ok": True, "name": name}


@app.get("/api/rooms/{name}", dependencies=[Depends(require_token)])
def get_room(name: str):
    store = get_store()
    for r in _room_union(store):
        if r["name"] == name:
            return r
    raise HTTPException(status_code=404, detail="room not found")


@app.delete("/api/rooms/{name}", dependencies=[Depends(require_token)])
def delete_room(name: str, payload: dict = Body(default=None)):  # noqa: B008
    store = get_store()
    permanent = store.is_permanent(name)
    if permanent and not (payload and payload.get("confirm_permanent")):
        raise HTTPException(
            status_code=409,
            detail="permanent room; resend with confirm_permanent=true",
        )
    store.delete_room(name)
    store.delete_room_motd(name)
    store.delete_room_password(name)
    if permanent:
        store.unset_permanent(name)
    return {"ok": True}


@app.post("/api/rooms/{name}/permanent", dependencies=[Depends(require_token)])
def set_room_permanent(name: str, payload: dict):
    store = get_store()
    if payload.get("permanent"):
        store.set_permanent(name)
    else:
        store.unset_permanent(name)
    return {"ok": True, "name": name, "permanent": bool(payload.get("permanent"))}


@app.post("/api/rooms/{name}/playlist", dependencies=[Depends(require_token)])
def set_room_playlist(name: str, payload: dict):
    store = get_store()
    playlist = payload.get("playlist") or []
    if not isinstance(playlist, list):
        raise HTTPException(status_code=400, detail="playlist must be a list of file names")
    playlist = [str(f) for f in playlist]
    index = payload.get("index")
    if index is not None and not isinstance(index, int):
        raise HTTPException(status_code=400, detail="index must be an integer")
    # a room with an empty playlist is dropped by the server on next write;
    # keep it persistent by storing a marker
    store.save_room(name, "\n".join(playlist), index if index is not None else 0, 0.0, int(time.time()))
    return {"ok": True, "name": name, "playlist": playlist, "index": index}


# ---------------------------------------------------------------------------
# Server settings (MOTD, persistent-rooms notice) — stored in server_info
# ---------------------------------------------------------------------------


def _settings_payload(store):
    return {
        "motd": store.get_server_info("motd"),
        "persistent_notice": store.get_server_info("persistent_notice"),
        "persistent_notice_enabled": store.get_server_info("persistent_notice_enabled") != "0",
    }


@app.get("/api/settings", dependencies=[Depends(require_token)])
def get_settings():
    return _settings_payload(get_store())


@app.put("/api/settings", dependencies=[Depends(require_token)])
def put_settings(payload: dict):
    store = get_store()
    for key in ("motd", "persistent_notice"):
        if key in payload:
            value = str(payload[key] or "")
            store.set_server_info(key, value)
    if "persistent_notice_enabled" in payload:
        store.set_server_info("persistent_notice_enabled", "1" if payload["persistent_notice_enabled"] else "0")
    return _settings_payload(store)


# ---------------------------------------------------------------------------
# Per-room MOTD
# ---------------------------------------------------------------------------


def _room_motd_payload(store, name):
    motd = store.get_room_motd(name)
    if motd is None:
        return None
    return {"room": name, "motd": motd}


@app.get("/api/rooms/{name}/motd", dependencies=[Depends(require_token)])
def get_room_motd(name: str):
    store = get_store()
    if not _room_exists(store, name):
        raise HTTPException(status_code=404, detail="room not found")
    payload = _room_motd_payload(store, name)
    if not payload:
        raise HTTPException(status_code=404, detail="no per-room MOTD set")
    return payload


@app.put("/api/rooms/{name}/motd", dependencies=[Depends(require_token)])
def put_room_motd(name: str, payload: dict):
    store = get_store()
    if not _room_exists(store, name):
        raise HTTPException(status_code=404, detail="room not found")
    store.set_room_motd(name, str(payload.get("motd") or ""), int(time.time()))
    return _room_motd_payload(store, name)


@app.delete("/api/rooms/{name}/motd", dependencies=[Depends(require_token)])
def delete_room_motd(name: str):
    store = get_store()
    store.delete_room_motd(name)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Room passwords (controlled rooms)
# ---------------------------------------------------------------------------

# Mirror of RoomPasswordProvider (syncplay/utils.py): a controlled room is
# named "+<base>:<hash>" where the 12-char hash is derived from the base name,
# the password and the server salt. We duplicate the 5-line computation here
# because admin.py runs as a script (package imports are unavailable); keep it
# in sync with utils.py.
_CONTROLLED_ROOM_REGEX = re.compile(r"^\+(.*):(\w{12})$")
_PASSWORD_REGEX = re.compile(r"[A-Z]{2}-\d{3}-\d{3}")


def _compute_room_hash(room_name, password, salt):
    room_name = room_name.encode("utf8")
    salt = salt.encode("utf8")
    password = password.encode("utf8")
    salt = hashlib.sha256(salt).hexdigest().encode("utf8")
    provisional = hashlib.sha256(room_name + salt).hexdigest().encode("utf8")
    return hashlib.sha1(provisional + salt + password).hexdigest()[:12].upper()


def _controlled_room_name(base, password, salt):
    return "+" + base + ":" + _compute_room_hash(base, password, salt)


def _room_password_payload(store, name):
    row = store.get_room_password(name)
    if not row:
        return None
    password, updated_at = row
    salt = os.environ.get("SYNCPLAY_SALT") or ""
    return {
        "room": name,
        "password": password,
        "updated_at": updated_at,
        "controlled_room": _controlled_room_name(name, password, salt) if salt else None,
    }


def _room_exists(store, name):
    return name in {r["name"] for r in _room_union(store)}


@app.get("/api/rooms/{name}/password", dependencies=[Depends(require_token)])
def get_room_password(name: str):
    """Return the stored password for a controlled room (or 404)."""
    store = get_store()
    if not _room_exists(store, name):
        raise HTTPException(status_code=404, detail="room not found")
    payload = _room_password_payload(store, name)
    if not payload:
        raise HTTPException(status_code=404, detail="no password set")
    return payload


@app.post("/api/rooms/{name}/password", dependencies=[Depends(require_token)])
def set_room_password(name: str, payload: dict):
    """Set/change the password for a controlled room.

    The room name is the base name (without the '+base:hash' form). The server
    derives the actual controlled room name from SYNCPLAY_SALT when a client
    joins with this password. The password must match XX-000-000.
    """
    store = get_store()
    if _CONTROLLED_ROOM_REGEX.match(name):
        raise HTTPException(status_code=400, detail="use the base room name, not +name:hash")
    if not _room_exists(store, name):
        raise HTTPException(status_code=404, detail="room not found")
    password = str(payload.get("password") or "").strip()
    if not password:
        raise HTTPException(status_code=400, detail="password required")
    if not re.match(_PASSWORD_REGEX, password):
        raise HTTPException(status_code=400, detail="password must match XX-000-000")
    store.set_room_password(name, password, int(time.time()))
    return {"ok": True, **_room_password_payload(store, name)}


@app.delete("/api/rooms/{name}/password", dependencies=[Depends(require_token)])
def delete_room_password(name: str):
    get_store().delete_room_password(name)
    return {"ok": True}


# ---------------------------------------------------------------------------
# History + users
# ---------------------------------------------------------------------------


@app.get("/api/history", dependencies=[Depends(require_token)])
def history(
    room: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    rows = get_store().query_history(room=room, q=q, limit=limit)
    return {"rows": [_row_to_dict(r) for r in rows]}


@app.get("/api/users", dependencies=[Depends(require_token)])
def users(limit: int = Query(default=100, ge=1, le=1000)):
    rows = get_store().list_users(limit=limit)
    return {"users": [{"username": u, "first_seen": f, "last_seen": last} for u, f, last in rows]}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


def main():
    global _store, _admin_token
    parser = argparse.ArgumentParser(description="syncplay-history admin")
    parser.add_argument("--port", type=int, default=12347)
    parser.add_argument("--dsn", default=None, help="database DSN (default: resolve_dsn)")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    _store = db.open_store_with_retry(args.dsn)
    _admin_token = os.environ.get("ADMIN_TOKEN") or None
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
