#!/usr/bin/env python3
"""E2E smoke test for the admin web app.

Exercises the live admin API (health, room CRUD, permanent protection,
history, users, token gating when ADMIN_TOKEN is set). Run in-container:

    python3 /tmp/test_admin_smoke.py
"""
import json
import os
import time
import urllib.request

HOST = "127.0.0.1"
ADMIN_PORT = int(os.environ.get("ADMIN_PORT", "12347"))
TOKEN = os.environ.get("ADMIN_TOKEN")


def req(path, method="GET", body=None, token=None):
    url = f"http://{HOST}:{ADMIN_PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def main():
    results = []
    ts = int(time.time())
    room = f"sp-admin-{ts}"

    # 1. health
    status, h = req("/api/health", token=TOKEN)
    results.append(check("health 200", status == 200, f"status={status}"))
    results.append(check("health has server info", "server" in h and "rooms" in h and "users" in h))

    # 2. token gating (only meaningful when ADMIN_TOKEN set)
    if TOKEN:
        status, _ = req("/api/health")
        results.append(check("401 without token", status == 401, f"status={status}"))
        status, _ = req("/api/health", token="wrong")
        results.append(check("401 with wrong token", status == 401, f"status={status}"))
    else:
        results.append(check("open access (no token set)", True))

    # 3. create room
    status, j = req("/api/rooms", method="POST", body={"name": room}, token=TOKEN)
    results.append(check("create room", status == 200 and j.get("name") == room, f"status={status}"))

    # 4. duplicate -> 409
    status, _ = req("/api/rooms", method="POST", body={"name": room}, token=TOKEN)
    results.append(check("duplicate 409", status == 409, f"status={status}"))

    # 5. permanent room cannot be deleted
    perm = f"{room}-perm"
    req("/api/rooms", method="POST", body={"name": perm, "permanent": True}, token=TOKEN)
    status, _ = req(f"/api/rooms/{perm}", method="DELETE", token=TOKEN)
    results.append(check("delete permanent 409", status == 409, f"status={status}"))

    # 6. delete non-permanent room
    status, _ = req(f"/api/rooms/{room}", method="DELETE", token=TOKEN)
    results.append(check("delete room 200", status == 200, f"status={status}"))

    # 7. set playlist
    req("/api/rooms", method="POST", body={"name": room}, token=TOKEN)
    status, _ = req(f"/api/rooms/{room}/playlist", method="POST",
                    body={"playlist": ["a.mkv", "b.mkv"], "index": 0}, token=TOKEN)
    results.append(check("set playlist", status == 200, f"status={status}"))
    status, j = req(f"/api/rooms/{room}", token=TOKEN)
    results.append(check("playlist persisted", j.get("playlist") == ["a.mkv", "b.mkv"], str(j.get("playlist"))))

    # 8. history + users
    status, j = req("/api/history", token=TOKEN)
    results.append(check("history 200", status == 200 and "rows" in j, f"status={status}"))
    status, j = req("/api/users", token=TOKEN)
    results.append(check("users 200", status == 200 and "users" in j, f"status={status}"))

    # cleanup
    req(f"/api/rooms/{room}", method="DELETE", token=TOKEN)
    req(f"/api/rooms/{perm}", method="DELETE", token=TOKEN)

    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
