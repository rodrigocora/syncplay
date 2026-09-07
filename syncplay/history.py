import argparse
import json
import os
import re
import sqlite3
import time

from twisted.internet import reactor
from twisted.internet.endpoints import TCP4ClientEndpoint, TCP4ServerEndpoint
from twisted.internet.protocol import Factory, Protocol

DEFAULT_DSN = "sqlite:////data/history.sqlite"


def _db_path(path):
    if not path.lower().endswith(".sqlite"):
        path += ".sqlite"
    return path


def _dsnFromDbEnv():
    # Build a postgres DSN from discrete DB_* env vars. The password comes
    # from the environment only, so it never appears on a command line.
    # Returns None if the host is not set (i.e. discrete vars are not in use).
    host = os.environ.get("DB_HOST")
    if not host:
        return None
    port = os.environ.get("DB_PORT") or "5432"
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASS")
    dbname = os.environ.get("DB_NAME") or "history"
    userinfo = "" if not user else (f"{user}@" if not password else f"{user}:{password}@")
    return f"postgres://{userinfo}{host}:{port}/{dbname}"


def _parseDsn(dsn):
    scheme = dsn.split(":", 1)[0].lower()
    if scheme == "sqlite":
        # sqlite:///relative.sqlite  |  sqlite:////absolute.sqlite  |  sqlite:/:memory:
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


def _redactDsn(dsn):
    # hide the password in user:pass@host so it is safe to log
    return re.sub(r"(?<=://)([^:/@]+):([^@/]+)@", r"\1:***@", dsn)


class HistoryStore:
    """SQLite-backed store."""

    def __init__(self, dbPath):
        self._dbPath = dbPath
        self._createSchema()

    def _createSchema(self):
        self._conn = sqlite3.connect(self._dbPath, check_same_thread=False)
        # WAL mode for concurrent reads (e.g. docker exec queries); checkpoint
        # periodically so the main .sqlite file stays readable when copied out.
        self._conn.execute("pragma journal_mode = WAL")
        self._conn.execute("pragma wal_autocheckpoint = 100")
        self._conn.execute(
            "create table if not exists users (username text primary key, first_seen integer, last_seen integer)"
        )
        self._conn.execute(
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
            ") without rowid"
        )
        self._conn.commit()

    def _exec(self, query, params=()):
        try:
            self._conn.execute(query, params)
            self._conn.commit()
        except sqlite3.Error as e:
            self._conn.rollback()
            print("history store error:", e)

    def upsertUser(self, username, now):
        self._exec(
            "insert into users (username, first_seen, last_seen) values (?, ?, ?) "
            "on conflict(username) do update set last_seen = excluded.last_seen",
            (username, now, now),
        )

    def upsertWatch(self, room, file, size, position, duration, now):
        watched = position if position is not None else 0.0
        percentage = None
        if duration:
            percentage = min(round(watched / float(duration) * 100.0, 4), 100.0)
        self._exec(
            "insert into watch_history (room, file, size, watched, percentage, duration, started_at, last_seen) "
            "values (?, ?, ?, ?, ?, ?, ?, ?) "
            "on conflict(room, file) do update set "
            " size = coalesce(excluded.size, size),"
            " watched = max(coalesce(watched, 0), coalesce(excluded.watched, 0)),"
            " percentage = coalesce(excluded.percentage, percentage),"
            " duration = coalesce(excluded.duration, duration),"
            " stopped_at = 0,"
            " last_seen = excluded.last_seen",
            (room, file, size, watched, percentage, duration, now, now),
        )

    def recordStopped(self, room, file, position, now):
        if not file:
            return
        watched = position if position is not None else 0.0
        self._exec(
            "insert into watch_history (room, file, watched, duration, stopped_at, started_at, last_seen) "
            "values (?, ?, ?, 0, ?, ?, ?) "
            "on conflict(room, file) do update set "
            " watched = max(coalesce(watched, 0), coalesce(excluded.watched, 0)),"
            " stopped_at = excluded.stopped_at,"
            " last_seen = excluded.last_seen",
            (room, file, watched, now, now, now),
        )


class PostgresHistoryStore:
    """Postgres store using psycopg2 (lazy import)."""

    def __init__(self, params):
        import psycopg2

        self._psycopg2 = psycopg2
        self._conn = psycopg2.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            dbname=params["dbname"],
        )
        # autocommit so a single failed statement cannot abort the connection's
        # transaction and poison every later write
        self._conn.autocommit = True
        self._createSchema()

    def _createSchema(self):
        cur = self._conn.cursor()
        cur.execute(
            "create table if not exists users ("
            " username text primary key,"
            " first_seen bigint, last_seen bigint)"
        )
        cur.execute(
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
            " primary key (room, file))"
        )

    def _exec(self, query, params=()):
        cur = self._conn.cursor()
        try:
            cur.execute(query, params)
        except self._psycopg2.Error as e:
            if not self._conn.autocommit:
                self._conn.rollback()
            print("history store error:", e)

    def upsertUser(self, username, now):
        self._exec(
            "insert into users (username, first_seen, last_seen) values (%s, %s, %s) "
            "on conflict(username) do update set last_seen = excluded.last_seen",
            (username, now, now),
        )

    def upsertWatch(self, room, file, size, position, duration, now):
        watched = position if position is not None else 0.0
        percentage = None
        if duration:
            percentage = min(round(watched / float(duration) * 100.0, 4), 100.0)
        self._exec(
            "insert into watch_history (room, file, size, watched, percentage, duration, started_at, last_seen) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict(room, file) do update set "
            " size = coalesce(excluded.size, watch_history.size),"
            " watched = greatest(coalesce(watch_history.watched, 0), coalesce(excluded.watched, 0)),"
            " percentage = coalesce(excluded.percentage, watch_history.percentage),"
            " duration = coalesce(excluded.duration, watch_history.duration),"
            " stopped_at = 0,"
            " last_seen = excluded.last_seen",
            (room, file, size, watched, percentage, duration, now, now),
        )

    def recordStopped(self, room, file, position, now):
        if not file:
            return
        watched = position if position is not None else 0.0
        self._exec(
            "insert into watch_history (room, file, watched, duration, stopped_at, started_at, last_seen) "
            "values (%s, %s, %s, 0, %s, %s, %s) "
            "on conflict(room, file) do update set "
            " watched = greatest(coalesce(watch_history.watched, 0), coalesce(excluded.watched, 0)),"
            " stopped_at = excluded.stopped_at,"
            " last_seen = excluded.last_seen",
            (room, file, watched, now, now, now),
        )


def create_store(dsn):
    if dsn == "auto":
        dsn = _dsnFromDbEnv() or DEFAULT_DSN
    scheme, rest = _parseDsn(dsn)
    if scheme == "sqlite":
        return HistoryStore(_db_path(rest))
    if scheme == "postgres":
        return PostgresHistoryStore(rest)
    raise ValueError("unsupported DSN: " + dsn)


class RecordingState:
    def __init__(self, store):
        self._store = store
        self._username = None
        self._room = None
        self._file = None
        self._size = None
        self._duration = None
        self._lastKnownPosition = 0.0

    def consumeLine(self, line):
        try:
            message = json.loads(line.decode("utf-8").strip())
        except (ValueError, UnicodeDecodeError):
            return
        now = int(time.time())
        for command, payload in message.items():
            if command == "Hello":
                self._username = payload.get("username")
                if self._username:
                    self._store.upsertUser(self._username, now)
                # the room name is in the Hello; a real client usually never
                # sends a separate Set.room, so capture it here
                room = payload.get("room")
                if isinstance(room, dict) and room.get("name"):
                    self._room = room.get("name")
            elif command == "Set":
                for key, value in payload.items():
                    if key == "room" and isinstance(value, dict):
                        self._room = value.get("name")
                    elif key == "file" and isinstance(value, dict):
                        self._file = value.get("name")
                        self._size = value.get("size")
                        self._duration = value.get("duration")
            elif command == "State":
                playstate = payload.get("playstate", {})
                position = playstate.get("position")
                if position is not None:
                    self._lastKnownPosition = position
                if self._file and self._room:
                    self._store.upsertWatch(
                        self._room, self._file, self._size, position, self._duration, now
                    )

    def onDisconnect(self):
        if self._room and self._file:
            self._store.recordStopped(self._room, self._file, self._lastKnownPosition, int(time.time()))


class UpstreamProtocol(Protocol):
    def __init__(self, clientProtocol):
        self._client = clientProtocol

    def connectionMade(self):
        self._client._onUpstreamConnected(self)

    def dataReceived(self, data):
        self._client._sendToClient(data)

    def connectionLost(self, reason):
        self._client._teardown()


class UpstreamFactory(Factory):
    def __init__(self, clientProtocol):
        self._clientProtocol = clientProtocol

    def buildProtocol(self, addr):
        return UpstreamProtocol(self._clientProtocol)


class ClientProtocol(Protocol):
    def __init__(self, host, port, store):
        self._host = host
        self._port = port
        self._store = store
        self._state = RecordingState(store)
        self._upstream = None
        self._buffer = b""
        self._lineBuffer = b""

    def connectionMade(self):
        try:
            TCP4ClientEndpoint(reactor, self._host, self._port).connect(
                UpstreamFactory(self)
            )
        except OSError:
            self.transport.loseConnection()

    def _onUpstreamConnected(self, upstream):
        self._upstream = upstream
        if self._buffer:
            self._upstream.transport.write(self._buffer)
            self._buffer = b""
            self._record(b"")

    def _sendToClient(self, data):
        if self.transport and not self.transport.disconnecting:
            self.transport.write(data)

    def dataReceived(self, data):
        if self._upstream and self._upstream.transport and not self._upstream.transport.disconnecting:
            self._upstream.transport.write(data)
        else:
            self._buffer += data
        self._record(data)

    def _record(self, data):
        self._lineBuffer += data
        if b"\n" not in self._lineBuffer:
            return
        *complete, remainder = self._lineBuffer.split(b"\n")
        self._lineBuffer = remainder
        for line in complete:
            line = line.strip(b"\r")
            if line:
                self._state.consumeLine(line)

    def connectionLost(self, reason):
        self._state.onDisconnect()
        self._teardown()

    def _teardown(self):
        if self._upstream and self._upstream.transport and not self._upstream.transport.disconnecting:
            self._upstream.transport.loseConnection()


class ClientFactory(Factory):
    def __init__(self, host, port, store):
        self._host = host
        self._port = port
        self._store = store

    def buildProtocol(self, addr):
        return ClientProtocol(self._host, self._port, self._store)


def _checkpointLoop(store):
    while True:
        time.sleep(30)
        try:
            store._conn.execute("pragma wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as e:
            print("history store checkpoint error:", e)


def run(proxyPort, host, port, dsn):
    store = create_store(dsn)
    factory = ClientFactory(host, port, store)
    TCP4ServerEndpoint(reactor, proxyPort, interface="0.0.0.0").listen(factory)
    from twisted.internet import threads

    if isinstance(store, HistoryStore):
        threads.deferToThread(_checkpointLoop, store)
    print(f"history proxy listening on {proxyPort}, forwarding to {host}:{port}, dsn={_redactDsn(dsn)}")
    reactor.run()


def main():
    parser = argparse.ArgumentParser(description="Syncplay watch-history proxy")
    parser.add_argument("--port", type=int, default=12346, help="proxy listen port")
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=12345)
    parser.add_argument(
        "--dsn",
        default=None,
        help="database DSN. If omitted, builds one from the DB_* env vars, "
        "falling back to HISTORY_DSN, then the SQLite default",
    )
    args = parser.parse_args()
    if args.dsn:
        dsn = args.dsn
    else:
        # prefer discrete DB_* vars, then HISTORY_DSN, then SQLite default
        dsn = _dsnFromDbEnv() or os.environ.get("HISTORY_DSN") or DEFAULT_DSN
    run(args.port, args.upstream_host, args.upstream_port, dsn)


if __name__ == "__main__":
    main()
