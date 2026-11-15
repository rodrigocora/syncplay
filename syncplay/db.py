"""Shared backend store for the watch-history feature.

One database, one DSN, two consumers:

* the history proxy (watch_history, users) -- see history.py
* the syncplay server (persistent_rooms, permanent_rooms, server_info,
  room_status) -- see server.py
* the admin web app (all of the above, read + room management) -- see admin.py

The default backend is SQLite (``sqlite:////data/syncplay.sqlite``); Postgres
is supported through ``DB_*`` env vars or an explicit DSN. Every table is
created with ``create table if not exists`` so opening an existing database
(e.g. a history.sqlite created by an older release) is non-destructive.

DSN resolution order (used by every consumer, and by ``resolve_dsn``):

1. an explicit ``--dsn``/``--backend-dsn`` argument
2. ``DB_*`` env vars (DB_HOST set) -> postgres DSN
3. ``SYNCPLAY_DSN``
4. ``HISTORY_DSN`` (legacy alias)
5. the SQLite default

The password is never passed on a command line: the ``DB_*`` path builds the
DSN inside the process from the environment, and ``redact_dsn`` hides it from
logs.
"""

import os
import re
import sqlite3
import threading
import time

DEFAULT_DSN = "sqlite:////data/syncplay.sqlite"


# ---------------------------------------------------------------------------
# DSN handling
# ---------------------------------------------------------------------------


def _db_path(path):
    if path == ":memory:":
        return path
    if not path.lower().endswith(".sqlite"):
        path += ".sqlite"
    return path


def dsn_from_db_env():
    """Build a postgres DSN from discrete DB_* env vars.

    Returns None when DB_HOST is not set (i.e. the discrete vars are not in
    use). The password comes from the environment only, so it never appears on
    a command line.
    """
    host = os.environ.get("DB_HOST")
    if not host:
        return None
    port = os.environ.get("DB_PORT") or "5432"
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASS")
    dbname = os.environ.get("DB_NAME") or "history"
    userinfo = "" if not user else (f"{user}@" if not password else f"{user}:{password}@")
    return f"postgres://{userinfo}{host}:{port}/{dbname}"


def parse_dsn(dsn):
    """Return (scheme, params). scheme is 'sqlite' or 'postgres'."""
    scheme = dsn.split(":", 1)[0].lower()
    if scheme == "sqlite":
        # sqlite:///relative.sqlite  |  sqlite:////absolute.sqlite  |  sqlite::memory:
        return "sqlite", dsn[len("sqlite:"):] or ":memory:"
    if scheme in ("postgres", "postgresql"):
        m = re.match(
            r"^postgres(?:ql)?://(?:([^:@/]*)(?::([^@/]*))?@)?([a-zA-Z0-9.\-_]+)(?::(\d+))?(/[^?#]+)?", dsn
        )
        if not m:
            raise ValueError("invalid DSN: " + dsn)
        user, password, host, port, path = m.groups()
        return "postgres", {
            "host": host,
            "port": int(port or 5432),
            "user": user,
            "password": password,
            "dbname": (path or "/history").lstrip("/"),
        }
    raise ValueError("unsupported DSN scheme: " + dsn)


def resolve_dsn(explicit=None):
    """Resolve the effective DSN, following the documented precedence.

    ``explicit`` may be a DSN string, None, or "auto" (meaning: resolve from
    the environment as if no argument was given).
    """
    if explicit and explicit != "auto":
        return explicit
    return dsn_from_db_env() or os.environ.get("SYNCPLAY_DSN") or os.environ.get("HISTORY_DSN") or DEFAULT_DSN


def redact_dsn(dsn):
    """Hide the password in user:pass@host so it is safe to log."""
    return re.sub(r"(?<=://)([^:/@]+):([^@/]+)@", r"\1:***@", dsn)


# ---------------------------------------------------------------------------
# Backend store
# ---------------------------------------------------------------------------


class BackendStore:
    """One store class for both dialects; DDL and SQL branch on the dialect.

    Public methods are synchronous and safe to call from any thread (a lock
    serializes access). The Twisted server wraps calls with deferToThread; the
    proxy and the admin app call them directly.
    """

    def __init__(self, dsn):
        scheme, params = parse_dsn(dsn)
        self.dsn = dsn
        self.dialect = scheme
        self._lock = threading.RLock()
        if scheme == "sqlite":
            self.ph = "?"
            self._conn = sqlite3.connect(_db_path(params), check_same_thread=False, timeout=30)
            self._conn.execute("pragma journal_mode = WAL")
            self._conn.execute("pragma wal_autocheckpoint = 100")
        else:
            self.ph = "%s"
            import psycopg2

            self._psycopg2 = psycopg2
            self._conn_host = params["host"]
            self._conn_port = params["port"]
            self._conn_user = params["user"]
            self._conn_password = params["password"]
            self._conn_dbname = params["dbname"]
            self._conn = psycopg2.connect(
                host=self._conn_host,
                port=self._conn_port,
                user=self._conn_user,
                password=self._conn_password,
                dbname=self._conn_dbname,
            )
            # autocommit so a single failed statement cannot poison later writes
            self._conn.autocommit = True
        self._create_schema()

    # -- low level ----------------------------------------------------------

    def _create_schema(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                for ddl in self._DDL_SQLITE if self.dialect == "sqlite" else self._DDL_POSTGRES:
                    cur.execute(ddl)
                if self.dialect == "sqlite":
                    self._conn.commit()
            finally:
                cur.close()

    def _ensure_conn(self):
        """Reconnect if Postgres dropped the connection (idle timeout, restart).

        The store keeps one long-lived connection; without this, a closed
        connection poisons every later call with 'connection already closed'.
        Callers already hold self._lock; do not acquire it here.
        """
        if self.dialect != "postgres":
            return
        try:
            if self._conn.closed:
                raise self._psycopg2.InterfaceError("connection closed")
            self._conn.cursor().execute("select 1")
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = self._psycopg2.connect(
                host=self._conn_host,
                port=self._conn_port,
                user=self._conn_user,
                password=self._conn_password,
                dbname=self._conn_dbname,
            )
            self._conn.autocommit = True
            self._create_schema()

    def _exec(self, query, params=()):
        with self._lock:
            self._ensure_conn()
            cur = self._conn.cursor()
            try:
                cur.execute(query, params)
                if self.dialect == "sqlite":
                    self._conn.commit()
            except Exception as e:
                if self.dialect == "postgres" and not self._conn.autocommit:
                    self._conn.rollback()
                print(f"backend store error ({self.dialect}):", e)
            finally:
                cur.close()

    def query(self, sql, params=()):
        """Return all rows for a SELECT (used by tests/admin/simulator)."""
        with self._lock:
            self._ensure_conn()
            cur = self._conn.cursor()
            try:
                cur.execute(sql, params)
                return cur.fetchall()
            finally:
                cur.close()

    def execute(self, sql, params=()):
        """Run a write statement (used by tests/simulator)."""
        self._exec(sql, params)

    def close(self):
        with self._lock:
            self._conn.close()

    def db_ok(self):
        try:
            self.query("select 1")
            return True
        except Exception:
            return False

    def wal_checkpoint(self):
        """Force a WAL checkpoint (SQLite only); keeps the main file readable
        when copied out of the container."""
        if self.dialect != "sqlite":
            return
        try:
            with self._lock:
                self._conn.execute("pragma wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as e:
            print("history store checkpoint error:", e)

    # -- users --------------------------------------------------------------

    def upsert_user(self, username, now):
        self._exec(
            "insert into users (username, first_seen, last_seen) values (?, ?, ?) "
            "on conflict(username) do update set last_seen = excluded.last_seen".replace("?", self.ph),
            (username, now, now),
        )

    def list_users(self, limit=100):
        return self.query(
            "select username, first_seen, last_seen from users order by last_seen desc limit " + str(int(limit))
        )

    def count_users(self):
        return self.query("select count(*) from users")[0][0]

    # -- watch history ------------------------------------------------------

    @property
    def _max_expr(self):
        # sqlite: scalar max(); postgres: greatest()
        return "max" if self.dialect == "sqlite" else "greatest"

    def upsert_watch(self, room, file, size, position, duration, now):
        watched = position if position is not None else 0.0
        percentage = None
        if duration:
            percentage = min(round(watched / float(duration) * 100.0, 4), 100.0)
        mx = self._max_expr
        self._exec(
            "insert into watch_history (room, file, size, watched, percentage, duration, started_at, last_seen) "
            "values (?, ?, ?, ?, ?, ?, ?, ?) "
            "on conflict(room, file) do update set "
            " size = coalesce(excluded.size, size),"
            f" watched = {mx}(coalesce(watched, 0), coalesce(excluded.watched, 0)),"
            " percentage = coalesce(excluded.percentage, percentage),"
            " duration = coalesce(excluded.duration, duration),"
            " stopped_at = 0,"
            " last_seen = excluded.last_seen".replace("?", self.ph),
            (room, file, size, watched, percentage, duration, now, now),
        )

    def record_stopped(self, room, file, position, now):
        if not file:
            return
        watched = position if position is not None else 0.0
        mx = self._max_expr
        self._exec(
            "insert into watch_history (room, file, watched, duration, stopped_at, started_at, last_seen) "
            "values (?, ?, ?, 0, ?, ?, ?) "
            "on conflict(room, file) do update set "
            f" watched = {mx}(coalesce(watched, 0), coalesce(excluded.watched, 0)),"
            " stopped_at = excluded.stopped_at,"
            " last_seen = excluded.last_seen".replace("?", self.ph),
            (room, file, watched, now, now, now),
        )

    def query_history(self, room=None, q=None, limit=100):
        where, params = [], []
        if room:
            where.append(f"room = {self.ph}")
            params.append(room)
        if q:
            where.append(f"(room like {self.ph} or file like {self.ph})")
            params += [f"%{q}%", f"%{q}%"]
        sql = (
            "select room, file, size, watched, percentage, duration, stopped_at, started_at, last_seen "
            "from watch_history"
        )
        if where:
            sql += " where " + " and ".join(where)
        sql += " order by last_seen desc limit " + str(int(limit))
        return self.query(sql, params)

    # -- persistent rooms ---------------------------------------------------

    def save_room(self, name, playlist, playlist_index, position, last_update):
        self._exec(
            "insert into persistent_rooms (name, playlist, playlistIndex, position, lastSavedUpdate) "
            "values (?, ?, ?, ?, ?) "
            "on conflict(name) do update set "
            " playlist = excluded.playlist,"
            " playlistIndex = excluded.playlistIndex,"
            " position = excluded.position,"
            " lastSavedUpdate = excluded.lastSavedUpdate".replace("?", self.ph),
            (name, playlist, playlist_index, position, last_update),
        )

    def delete_room(self, name):
        self._exec("delete from persistent_rooms where name = " + self.ph, (name,))

    def load_rooms(self):
        return self.query(
            "select name, playlist, playlistIndex, position, lastSavedUpdate from persistent_rooms"
        )

    # -- permanent rooms ----------------------------------------------------

    def set_permanent(self, name):
        self._exec(
            "insert into permanent_rooms (name) values (?) on conflict(name) do nothing".replace("?", self.ph),
            (name,),
        )

    def unset_permanent(self, name):
        self._exec("delete from permanent_rooms where name = " + self.ph, (name,))

    def list_permanent(self):
        return [r[0] for r in self.query("select name from permanent_rooms order by name")]

    def seed_permanent(self, names):
        """Idempotent one-time seed from the legacy text file (no longer used)."""
        for name in names:
            if name:
                self.set_permanent(name)

    def is_permanent(self, name):
        return bool(self.query("select 1 from permanent_rooms where name = " + self.ph, (name,)))

    # -- room passwords (controlled rooms) ----------------------------------

    def set_room_password(self, room, password, now):
        self._exec(
            "insert into room_passwords (room, password, updated_at) values (?, ?, ?) "
            "on conflict(room) do update set password = excluded.password, updated_at = excluded.updated_at"
            .replace("?", self.ph),
            (room, password, now),
        )

    def get_room_password(self, room):
        rows = self.query("select password, updated_at from room_passwords where room = " + self.ph, (room,))
        return rows[0] if rows else None

    def delete_room_password(self, room):
        self._exec("delete from room_passwords where room = " + self.ph, (room,))

    def list_room_passwords(self):
        return {room: password for room, password, _ in
                self.query("select room, password, updated_at from room_passwords order by room")}

    # -- per-room MOTD ------------------------------------------------------

    def set_room_motd(self, room, motd, now):
        self._exec(
            "insert into room_motds (name, motd, updated_at) values (?, ?, ?) "
            "on conflict(name) do update set motd = excluded.motd, updated_at = excluded.updated_at"
            .replace("?", self.ph),
            (room, motd, now),
        )

    def get_room_motd(self, room):
        rows = self.query("select motd from room_motds where name = " + self.ph, (room,))
        return rows[0][0] if rows else None

    def delete_room_motd(self, room):
        self._exec("delete from room_motds where name = " + self.ph, (room,))

    # -- server health ------------------------------------------------------

    def set_server_info(self, key, value):
        self._exec(
            "insert into server_info (key, value) values (?, ?) "
            "on conflict(key) do update set value = excluded.value".replace("?", self.ph),
            (key, str(value)),
        )

    def get_server_info(self, key, default=None):
        rows = self.query("select value from server_info where key = " + self.ph, (key,))
        return rows[0][0] if rows else default

    def server_info(self):
        return {key: value for key, value in self.query("select key, value from server_info")}

    def snapshot_room_status(self, rows):
        """Replace the room_status table with the given (room, watchers,
        playing, position, playlist_size, updated_at) tuples."""
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("delete from room_status")
                for row in rows:
                    cur.execute(
                        "insert into room_status (room, watchers, playing, position, playlist_size, updated_at) "
                        "values (?, ?, ?, ?, ?, ?)".replace("?", self.ph),
                        row,
                    )
                if self.dialect == "sqlite":
                    self._conn.commit()
            except Exception as e:
                if self.dialect == "postgres" and not self._conn.autocommit:
                    self._conn.rollback()
                print("room_status snapshot error:", e)
            finally:
                cur.close()

    def list_room_status(self):
        return self.query(
            "select room, watchers, playing, position, playlist_size, updated_at from room_status order by room"
        )

    # -- schema (dialect-specific DDL) --------------------------------------

    _DDL_SQLITE = [
        "create table if not exists users (username text primary key, first_seen integer, last_seen integer)",
        "create table if not exists watch_history ("
        " room text not null,"
        " file text,"
        " size integer,"
        " watched real,"
        " percentage real,"
        " duration integer,"
        " stopped_at integer default 0,"
        " started_at integer,"
        " last_seen integer,"
        " primary key (room, file)"
        ") without rowid",
        "create table if not exists persistent_rooms ("
        " name text primary key,"
        " playlist text,"
        " playlistIndex integer,"
        " position real,"
        " lastSavedUpdate integer"
        ")",
        "create table if not exists permanent_rooms (name text primary key)",
        "create table if not exists room_passwords ("
        " room text primary key,"
        " password text,"
        " updated_at integer"
        ")",
        "create table if not exists server_info (key text primary key, value text)",
        "create table if not exists room_motds ("
        " name text primary key,"
        " motd text,"
        " updated_at integer"
        ")",
        "create table if not exists room_status ("
        " room text primary key,"
        " watchers integer,"
        " playing integer,"
        " position real,"
        " playlist_size integer,"
        " updated_at integer"
        ")",
    ]

    _DDL_POSTGRES = [
        "create table if not exists users ("
        " username text primary key,"
        " first_seen bigint, last_seen bigint)",
        "create table if not exists watch_history ("
        " room text not null,"
        " file text,"
        " size bigint,"
        " watched double precision,"
        " percentage double precision,"
        " duration bigint,"
        " stopped_at bigint default 0,"
        " started_at bigint,"
        " last_seen bigint,"
        " primary key (room, file))",
        "create table if not exists persistent_rooms ("
        " name text primary key,"
        " playlist text,"
        " playlistIndex integer,"
        " position double precision,"
        " lastSavedUpdate bigint"
        ")",
        "create table if not exists permanent_rooms (name text primary key)",
        "create table if not exists room_passwords ("
        " room text primary key,"
        " password text,"
        " updated_at bigint"
        ")",
        "create table if not exists server_info (key text primary key, value text)",
        "create table if not exists room_motds ("
        " name text primary key,"
        " motd text,"
        " updated_at bigint"
        ")",
        "create table if not exists room_status ("
        " room text primary key,"
        " watchers integer,"
        " playing integer,"
        " position double precision,"
        " playlist_size integer,"
        " updated_at bigint"
        ")",
    ]


def open_store(dsn=None):
    """Open a BackendStore for the effective DSN (see resolve_dsn)."""
    return BackendStore(resolve_dsn(dsn))


def open_store_with_retry(dsn=None, attempts=5, delay=2.0):
    """The DB may come up after the proxy does; retry briefly before giving up
    with a clear, actionable error instead of a bare traceback."""
    last_error = None
    effective = resolve_dsn(dsn)
    for attempt in range(1, attempts + 1):
        try:
            return BackendStore(effective)
        except Exception as e:
            last_error = e
            if attempt < attempts:
                print(f"backend store not ready ({redact_dsn(effective)}): {e}; retry {attempt}/{attempts} in {delay}s")
                time.sleep(delay)
    print(f"backend store unavailable at {redact_dsn(effective)} after {attempts} attempts: {last_error}")
    raise SystemExit(1)
