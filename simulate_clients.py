#!/usr/bin/env python3
"""Multi-scenario synthetic client to exercise the watch-history proxy.

Spawns threaded Syncplay clients against the live proxy, runs a matrix of
scenarios (concurrency, seeks, percentage caps, protocol noise, reconnect,
stop-then-rewatch), then queries the store and prints a PASS/FAIL table.

It resolves the backend exactly like the proxy does -- DB_* env vars, then
HISTORY_DSN, then the SQLite default -- by loading the mounted config.env and
.secrets.env, so it always asserts against the store actually in use. The
password therefore never appears on any command line.

Usage (in-container, with the proxy running):
    python3 /app/simulate_clients.py
"""
import argparse
import json
import os
import socket
import sqlite3
import threading
import time

SETTLE = 0.4


def load_env_file(path):
    # Mirror entrypoint.sh: a value already present in the environment wins.
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if os.environ.get(key) in (None, ""):
                os.environ[key] = value


def resolve_backend():
    # Same precedence as history.py main(): DB_* vars, then HISTORY_DSN, then default.
    host = os.environ.get("DB_HOST")
    if host:
        return "postgres", dict(
            host=host,
            port=int(os.environ.get("DB_PORT") or 5432),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASS"),
            dbname=os.environ.get("DB_NAME") or "history",
        )
    dsn = os.environ.get("HISTORY_DSN") or "sqlite:////data/history.sqlite"
    path = dsn.split("://", 1)[1] if "://" in dsn else dsn
    return "sqlite", path


class Store:
    def __init__(self, kind, params):
        self.ph = "%s" if kind == "postgres" else "?"
        if kind == "postgres":
            import psycopg2

            self._conn = psycopg2.connect(**params)
        else:
            self._conn = sqlite3.connect(params, timeout=15)

    def query(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self._conn.commit()
        cur.close()

    def wh_row(self, room, file):
        rows = self.query(
            f"select size, watched, percentage, duration, stopped_at "
            f"from watch_history where room={self.ph} and file={self.ph}",
            (room, file),
        )
        if not rows:
            return None
        size, watched, percentage, duration, stopped_at = rows[0]
        return (
            size,
            float(watched) if watched is not None else None,
            float(percentage) if percentage is not None else None,
            duration,
            stopped_at,
        )

    def wh_count(self, room, file):
        return self.query(
            f"select count(*) from watch_history where room={self.ph} and file={self.ph}",
            (room, file),
        )[0][0]

    def user_row(self, username):
        rows = self.query(f"select first_seen, last_seen from users where username={self.ph}", (username,))
        return rows[0] if rows else None

    def close(self):
        self._conn.close()


class Client:
    """One synthetic Syncplay client: connect, send scripted lines, optional hold."""

    def __init__(self, host, port, username, room, script, hold=0.0):
        self.host = host
        self.port = port
        self.username = username
        self.room = room
        self.script = script
        self.hold = hold
        self.error = None

    def run(self):
        try:
            s = socket.create_connection((self.host, self.port), timeout=5)
        except OSError as e:
            self.error = f"connect: {e}"
            return
        try:
            self._line(s, {"Hello": {"username": self.username, "room": {"name": self.room}}})
            for item in self.script:
                if item[0] == "RAW":
                    s.sendall((item[1] + "\r\n").encode())
                else:
                    self._line(s, {item[0]: item[1]})
                time.sleep(0.15)
            if self.hold > 0:
                time.sleep(self.hold)
        except OSError as e:
            self.error = str(e)
        finally:
            s.close()

    @staticmethod
    def _line(s, msg):
        s.sendall((json.dumps(msg) + "\r\n").encode())


def run_clients(clients):
    threads = [threading.Thread(target=c.run) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    time.sleep(SETTLE)
    for c in clients:
        if c.error:
            print(f"  ! client error ({c.username}): {c.error}")


def state(pos):
    return ("State", {"playstate": {"position": pos, "paused": False}})


def set_file(name, duration=None, size=123456):
    payload = {"name": name, "size": size}
    if duration is not None:
        payload["duration"] = duration
    return ("Set", {"file": payload})


def main():
    parser = argparse.ArgumentParser(description="Multi-scenario watch-history proxy simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=12346)
    args = parser.parse_args()

    load_env_file("/app/config.env")
    load_env_file("/app/.secrets.env")
    kind, params = resolve_backend()
    store = Store(kind, params)
    print(f"backend: {kind}")

    ts = int(time.time())
    P = f"sp-sim-{ts}-{os.getpid()}"
    results = []

    def check(label, ok, detail=""):
        results.append((label, bool(ok), detail))

    # S1: concurrency/max -- 3 clients, same room+file, positions 100/250/180
    r1, f1 = f"{P}-r1", "f1.mkv"
    run_clients([
        Client(args.host, args.port, f"{P}-u1a", r1, [set_file(f1, 3600), state(100)]),
        Client(args.host, args.port, f"{P}-u1b", r1, [set_file(f1, 3600), state(250)]),
        Client(args.host, args.port, f"{P}-u1c", r1, [set_file(f1, 3600), state(180)]),
    ])
    row = store.wh_row(r1, f1)
    check("S1 single (room,file) row", store.wh_count(r1, f1) == 1, f"count={store.wh_count(r1, f1)}")
    check("S1 watched=max -> 250", row and row[1] == 250, f"watched={row[1] if row else None}")
    # percentage reflects the last-processed ping's position, not max(watched),
    # so concurrent clients leave it at any of the three positions' values.
    s1_pcts = {round(p / 3600 * 100.0, 4) for p in (100, 180, 250)}
    ok_pct = row and row[2] is not None and any(abs(row[2] - v) < 1e-9 for v in s1_pcts)
    check("S1 pct matches a client position", ok_pct, f"pct={row[2] if row else None}")

    # S2: same room, two different files
    r2, fa, fb = f"{P}-r2", "a.mkv", "b.mkv"
    run_clients([
        Client(args.host, args.port, f"{P}-u2a", r2, [set_file(fa, 1000), state(500)]),
        Client(args.host, args.port, f"{P}-u2b", r2, [set_file(fb, 2000), state(1000)]),
    ])
    ra, rb = store.wh_row(r2, fa), store.wh_row(r2, fb)
    check("S2 two rows (per file)", bool(ra and rb), f"a={ra is not None} b={rb is not None}")
    check("S2 a.watched=500", ra and ra[1] == 500, f"watched={ra[1] if ra else None}")
    check("S2 b.watched=1000", rb and rb[1] == 1000, f"watched={rb[1] if rb else None}")

    # S3: two rooms, same file
    r3a, r3b, fc = f"{P}-r3a", f"{P}-r3b", "c.mkv"
    run_clients([
        Client(args.host, args.port, f"{P}-u3a", r3a, [set_file(fc, 1000), state(300)]),
        Client(args.host, args.port, f"{P}-u3b", r3b, [set_file(fc, 1000), state(700)]),
    ])
    rc_a, rc_b = store.wh_row(r3a, fc), store.wh_row(r3b, fc)
    check("S3 two rows (per room)", bool(rc_a and rc_b), f"r3a={rc_a is not None} r3b={rc_b is not None}")
    check("S3 r3a.watched=300", rc_a and rc_a[1] == 300, f"watched={rc_a[1] if rc_a else None}")
    check("S3 r3b.watched=700", rc_b and rc_b[1] == 700, f"watched={rc_b[1] if rc_b else None}")

    # S4: seek backward 300 -> 120 -> 280, watched must stay at the max (300)
    r4, fd = f"{P}-r4", "d.mkv"
    run_clients([Client(args.host, args.port, f"{P}-u4", r4, [
        set_file(fd, 1000), state(300), state(120), state(280),
    ])])
    row = store.wh_row(r4, fd)
    check("S4 seek-back keeps max 300", row and row[1] == 300, f"watched={row[1] if row else None}")

    # S5: position beyond duration -> percentage capped at 100
    r5, fe = f"{P}-r5", "e.mkv"
    run_clients([Client(args.host, args.port, f"{P}-u5", r5, [set_file(fe, 1000), state(9999)])])
    row = store.wh_row(r5, fe)
    check("S5 watched=9999", row and row[1] == 9999, f"watched={row[1] if row else None}")
    check("S5 pct capped at 100", row and row[2] is not None and abs(row[2] - 100.0) < 1e-9,
          f"pct={row[2] if row else None}")

    # S6: no duration -> percentage stays NULL
    r6, fg = f"{P}-r6", "g.mkv"
    run_clients([Client(args.host, args.port, f"{P}-u6", r6, [set_file(fg, None), state(500)])])
    row = store.wh_row(r6, fg)
    check("S6 watched=500", row and row[1] == 500, f"watched={row[1] if row else None}")
    check("S6 pct NULL (no duration)", row and row[2] is None, f"pct={row[2] if row else None}")

    # S7: no State, just disconnect -> watched=0, stopped_at set
    r7, fh = f"{P}-r7", "h.mkv"
    run_clients([Client(args.host, args.port, f"{P}-u7", r7, [set_file(fh, 1000)])])
    row = store.wh_row(r7, fh)
    check("S7 row exists", row is not None, "")
    check("S7 watched=0", row and row[1] == 0, f"watched={row[1] if row else None}")
    check("S7 stopped_at set", row and row[4] not in (None, 0), f"stopped_at={row[4] if row else None}")

    # S8: unrecorded commands + malformed line ignored, proxy survives
    r8, fi = f"{P}-r8", "i.mkv"
    run_clients([Client(args.host, args.port, f"{P}-u8", r8, [
        set_file(fi, 1000),
        ("Chat", {"message": "hello"}),
        ("List", {}),
        state(400),
        ("RAW", "{this is not json"),
    ])])
    row = store.wh_row(r8, fi)
    check("S8 noise ignored, watched=400", row and row[1] == 400, f"watched={row[1] if row else None}")
    check("S8 pct=40.0", row and row[2] is not None and abs(row[2] - 40.0) < 1e-9, f"pct={row[2] if row else None}")
    probe = socket.socket()
    probe.settimeout(3)
    alive = probe.connect_ex((args.host, args.port)) == 0
    probe.close()
    check("S8 proxy still listening", alive, f"alive={alive}")

    # S9: reconnect -- same user, two sessions; max accumulates, last_seen updates
    r9, fj, u9 = f"{P}-r9", "j.mkv", f"{P}-u9"
    run_clients([Client(args.host, args.port, u9, r9, [set_file(fj, 1000), state(200)])])
    time.sleep(1.1)
    t_before_s2 = int(time.time())
    run_clients([Client(args.host, args.port, u9, r9, [set_file(fj, 1000), state(600)])])
    row = store.wh_row(r9, fj)
    user = store.user_row(u9)
    check("S9 cross-session max -> 600", row and row[1] == 600, f"watched={row[1] if row else None}")
    check("S9 last_seen updated on reconnect", user and user[1] >= t_before_s2,
          f"first/last={user} before_s2={t_before_s2}")

    # S10: stop then rewatch -> stopped_at resets to 0 while rewatching
    r10, fk = f"{P}-r10", "k.mkv"
    run_clients([Client(args.host, args.port, f"{P}-u10a", r10, [set_file(fk, 1000), state(100)])])
    row = store.wh_row(r10, fk)
    check("S10 stopped_at set after stop", row and row[4] not in (None, 0),
          f"stopped_at={row[4] if row else None}")
    c10 = Client(args.host, args.port, f"{P}-u10b", r10, [set_file(fk, 1000), state(150)], hold=2.5)
    t10 = threading.Thread(target=c10.run)
    t10.start()
    time.sleep(1.2)  # within the 2.5s hold: session2 connected, State processed
    row = store.wh_row(r10, fk)
    check("S10 stopped_at reset to 0", row and row[4] in (0, None), f"stopped_at={row[4] if row else None}")
    check("S10 watched=150", row and row[1] == 150, f"watched={row[1] if row else None}")
    t10.join(timeout=15)

    # --- report ---
    print()
    width = max(len(label) for label, _, _ in results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    for label, ok, detail in results:
        line = f"[{'PASS' if ok else 'FAIL'}] {label.ljust(width)}"
        if detail:
            line += f"  ({detail})"
        print(line)
    print(f"\n{passed} passed, {failed} failed")

    # --- cleanup (namespaced rows only) ---
    ph = store.ph
    store.execute(f"delete from watch_history where room like {ph}", (P + "%",))
    store.execute(f"delete from users where username like {ph}", (P + "%",))
    leftover = store.query(f"select count(*) from watch_history where room like {ph}", (P + "%",))[0][0]
    print(f"cleanup: removed namespace {P}; leftover watch_history rows = {leftover}")
    store.close()

    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
