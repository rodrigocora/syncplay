"""Local integration smoke: server + proxy + synthetic client, no Docker.

Starts the real syncplay server (Twisted) on a random port and the real proxy
(history.py) in front of it, with a temp SQLite backend. Then connects a
synthetic client and checks that:

- the proxy records users + watch_history rows (the protocol path)
- the server persists rooms to the shared DB (HealthPublisher + rooms)
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "syncplay"))

SERVER_PORT = 18901
PROXY_PORT = 18902
DB = os.path.join(tempfile.gettempdir(), "syncplay-int-test.sqlite")
if os.path.exists(DB):
    os.remove(DB)

os.environ["SYNCPLAY_DSN"] = "sqlite:" + DB
os.environ["SYNCPLAY_PORT"] = str(SERVER_PORT)
os.environ["PROXY_PORT"] = str(PROXY_PORT)

server_proc = None
proxy_proc = None
results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def wait_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main():
    global server_proc, proxy_proc
    server_proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "syncplayServer.py"), "--port", str(SERVER_PORT), "--backend-dsn", "auto"],
        cwd=ROOT,
    )
    if not wait_port(SERVER_PORT):
        print("server did not start")
        return 1
    print("server up on", SERVER_PORT)

    proxy_proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "syncplay", "history.py"), "--port", str(PROXY_PORT),
         "--upstream-host", "127.0.0.1", "--upstream-port", str(SERVER_PORT)],
        cwd=os.path.join(ROOT, "syncplay"),
    )
    if not wait_port(PROXY_PORT):
        print("proxy did not start")
        return 1
    print("proxy up on", PROXY_PORT)
    time.sleep(1)

    s = socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=5)

    def send(msg):
        s.sendall((json.dumps(msg) + "\r\n").encode())

    send({"Hello": {"username": "int-user", "room": {"name": "int-room"}}})
    time.sleep(0.4)
    send({"Set": {"room": {"name": "int-room"}}})
    send({"Set": {"file": {"name": "movie.mkv", "duration": 3600, "size": 1000}}})
    time.sleep(0.4)
    for pos in (30.0, 60.0, 90.0):
        send({"State": {"playstate": {"position": pos, "paused": False}}})
        time.sleep(0.5)
    s.close()
    # the HealthPublisher snapshots room_status every 30s; wait for the first tick
    print("waiting for the first health tick (32s)...")
    time.sleep(32)

    import db

    store = db.open_store_with_retry()
    users = store.list_users()
    check("user recorded", any(u[0] == "int-user" for u in users), str(users))
    hist = store.query_history(room="int-room")
    check("watch_history row recorded", len(hist) == 1, str(hist))
    if hist:
        check("watched=90 (max)", hist[0][3] == 90.0, f"watched={hist[0][3]}")
    info = store.server_info()
    check("server_info heartbeat present", "updated_at" in info and "version" in info, str(info))
    check("room_status snapshot present", len(store.list_room_status()) >= 1, str(store.list_room_status()))
    store.close()

    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        for p in (server_proc, proxy_proc):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
    sys.exit(rc)
