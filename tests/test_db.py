"""Tests for syncplay.db — the shared backend store (SQLite + Postgres dialects).

Run from the repo root:
    python -m pytest tests/test_db.py -v

Postgres-dependent tests are skipped automatically when no PG instance is
reachable; set SYNCPLAY_TEST_PG_DSN to exercise them against a live DB.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "syncplay"))
import db  # noqa: E402

PG_DSN = os.environ.get("SYNCPLAY_TEST_PG_DSN")


# ---------------------------------------------------------------------------
# DSN handling
# ---------------------------------------------------------------------------


def test_parse_dsn_sqlite_absolute():
    scheme, path = db.parse_dsn("sqlite:////data/syncplay.sqlite")
    assert scheme == "sqlite"
    # 4 slashes: "sqlite:" prefix stripped, rest is the path (sqlite normalizes)
    assert path == "////data/syncplay.sqlite"


def test_parse_dsn_sqlite_memory():
    scheme, path = db.parse_dsn("sqlite::memory:")
    assert scheme == "sqlite"
    assert path == ":memory:"


def test_parse_dsn_postgres_full():
    scheme, params = db.parse_dsn("postgres://u:secret@host:5433/mydb")
    assert scheme == "postgres"
    assert params == {
        "host": "host",
        "port": 5433,
        "user": "u",
        "password": "secret",
        "dbname": "mydb",
    }


def test_parse_dsn_postgresql_alias_no_port():
    scheme, params = db.parse_dsn("postgresql://host/db")
    assert scheme == "postgres"
    assert params["host"] == "host"
    assert params["port"] == 5432
    assert params["dbname"] == "db"


def test_parse_dsn_unsupported_scheme_raises():
    with pytest.raises(ValueError):
        db.parse_dsn("mysql://x/db")


def test_db_path_appends_extension():
    assert db._db_path("/data/x") == "/data/x.sqlite"
    assert db._db_path("/data/x.sqlite") == "/data/x.sqlite"


def test_db_path_memory_untouched():
    assert db._db_path(":memory:") == ":memory:"


def test_redact_dsn_hides_password():
    assert db.redact_dsn("postgres://u:secret@h/db") == "postgres://u:***@h/db"


def test_redact_dsn_no_password_unchanged():
    assert db.redact_dsn("postgres://u@h/db") == "postgres://u@h/db"


def test_resolve_dsn_explicit_wins(monkeypatch):
    monkeypatch.setenv("SYNCPLAY_DSN", "sqlite:////other.sqlite")
    assert db.resolve_dsn("sqlite:////explicit.sqlite") == "sqlite:////explicit.sqlite"


def test_resolve_dsn_env_precedence(monkeypatch):
    monkeypatch.setenv("SYNCPLAY_DSN", "sqlite:////sync.sqlite")
    monkeypatch.setenv("HISTORY_DSN", "sqlite:////legacy.sqlite")
    assert db.resolve_dsn() == "sqlite:////sync.sqlite"


def test_resolve_dsn_history_dsn_fallback(monkeypatch):
    monkeypatch.delenv("SYNCPLAY_DSN", raising=False)
    monkeypatch.setenv("HISTORY_DSN", "sqlite:////legacy.sqlite")
    assert db.resolve_dsn() == "sqlite:////legacy.sqlite"


def test_resolve_dsn_default(monkeypatch):
    monkeypatch.delenv("SYNCPLAY_DSN", raising=False)
    monkeypatch.delenv("HISTORY_DSN", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    assert db.resolve_dsn() == db.DEFAULT_DSN


def test_dsn_from_db_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "10.0.0.5")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_USER", "syncplay")
    monkeypatch.setenv("DB_PASS", "pw")
    monkeypatch.setenv("DB_NAME", "spdb")
    assert db.dsn_from_db_env() == "postgres://syncplay:pw@10.0.0.5:5432/spdb"


def test_dsn_from_db_env_none_when_no_host(monkeypatch):
    monkeypatch.delenv("DB_HOST", raising=False)
    assert db.dsn_from_db_env() is None


# ---------------------------------------------------------------------------
# SQLite store behavior
# ---------------------------------------------------------------------------


def _store():
    return db.BackendStore("sqlite::memory:")


def test_schema_creates_all_tables():
    s = _store()
    tables = {r[0] for r in s.query(
        "select name from sqlite_master where type='table'")}
    assert {"users", "watch_history", "persistent_rooms",
            "permanent_rooms", "room_passwords", "server_info", "room_status"} <= tables


def test_schema_idempotent():
    s = _store()
    s2 = db.BackendStore("sqlite::memory:")  # re-creating on a fresh DB is trivially fine
    assert s.dialect == "sqlite" and s2.dialect == "sqlite"


def test_reopen_existing_db_non_destructive(tmp_path):
    """Opening a DB that already has users/watch_history rows (e.g. one created
    by an older release as history.sqlite) must not lose them."""
    path = str(tmp_path / "existing.sqlite")
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute(
        "create table if not exists users (username text primary key, first_seen integer, last_seen integer)"
    )
    conn.execute(
        "create table if not exists watch_history (room text not null, file text, size integer,"
        " watched real, percentage real, duration integer, stopped_at integer default 0,"
        " started_at integer, last_seen integer, primary key (room, file))"
    )
    conn.execute("insert into users values ('olduser', 1, 2)")
    conn.execute(
        "insert into watch_history values ('oldroom', 'oldfile.mkv', 10, 5.0, 50.0, 10, 0, 1, 2)"
    )
    conn.commit()
    conn.close()

    # Build the DSN so the path param is exactly the platform path (Linux
    # /data/x.sqlite or Windows C:\...). Avoids the /// prefix mangling on Windows.
    dsn = "sqlite:" + os.path.abspath(path)
    s = db.BackendStore(dsn)
    assert s.query("select username from users") == [("olduser",)]
    assert s.query("select room, file from watch_history") == [("oldroom", "oldfile.mkv")]
    # new tables exist too
    assert s.query("select count(*) from server_info")[0][0] == 0
    assert s.query("select count(*) from permanent_rooms")[0][0] == 0
    s.close()


def test_upsert_user_updates_last_seen():
    s = _store()
    s.upsert_user("alice", 100)
    s.upsert_user("alice", 200)
    rows = s.query("select username, first_seen, last_seen from users")
    assert rows == [("alice", 100, 200)]


def test_upsert_watch_keeps_max_watched_and_resets_stopped():
    s = _store()
    s.upsert_watch("room", "f.mkv", 1000, 10, 1000, 100)
    s.upsert_watch("room", "f.mkv", 1000, 500, 1000, 200)
    s.upsert_watch("room", "f.mkv", 1000, 300, 1000, 300)  # backward seek
    row = s.query("select watched, percentage, duration, stopped_at from watch_history")[0]
    assert row[0] == 500.0          # watched = max seen so far
    # percentage reflects the last processed position (300/1000), not max
    assert abs(row[1] - 30.0) < 1e-9
    assert row[2] == 1000
    assert row[3] == 0              # upsert resets stopped_at


def test_upsert_watch_percentage_capped_at_100():
    s = _store()
    s.upsert_watch("room", "f.mkv", 1000, 9999, 1000, 100)
    row = s.query("select watched, percentage from watch_history")[0]
    assert row[0] == 9999.0
    assert abs(row[1] - 100.0) < 1e-9


def test_upsert_watch_no_duration_leaves_percentage_null():
    s = _store()
    s.upsert_watch("room", "f.mkv", 1000, 50, None, 100)
    row = s.query("select watched, percentage from watch_history")[0]
    assert row[0] == 50.0
    assert row[1] is None


def test_record_stopped_sets_stopped_at():
    s = _store()
    s.upsert_watch("room", "f.mkv", 1000, 250, 1000, 100)
    s.record_stopped("room", "f.mkv", 250, 500)
    row = s.query("select watched, stopped_at from watch_history")[0]
    assert row[0] == 250.0
    assert row[1] == 500


def test_record_stopped_ignores_missing_file():
    s = _store()
    s.record_stopped("room", None, 100, 500)
    assert s.query("select count(*) from watch_history")[0][0] == 0


def test_persistent_rooms_roundtrip():
    s = _store()
    s.save_room("r", "a.mkv\nb.mkv", 1, 42.0, 100)
    s.save_room("r", "a.mkv", 0, 10.0, 200)  # overwrite
    rows = s.load_rooms()
    assert len(rows) == 1
    assert rows[0] == ("r", "a.mkv", 0, 10.0, 200)


def test_delete_room():
    s = _store()
    s.save_room("r", "a.mkv", 0, 0, 100)
    s.delete_room("r")
    assert s.load_rooms() == []


def test_permanent_rooms():
    s = _store()
    s.set_permanent("sexta")
    s.set_permanent("sexta")  # idempotent
    assert s.list_permanent() == ["sexta"]
    assert s.is_permanent("sexta") is True
    assert s.is_permanent("nope") is False
    s.unset_permanent("sexta")
    assert s.list_permanent() == []


def test_seed_permanent_skips_blanks():
    s = _store()
    s.seed_permanent(["", "a", None, "b"])
    assert s.list_permanent() == ["a", "b"]


def test_room_password_roundtrip():
    s = _store()
    assert s.get_room_password("room") is None
    s.set_room_password("room", "AB-123-456", 100)
    s.set_room_password("room", "CD-789-012", 200)  # change
    row = s.get_room_password("room")
    assert row[0] == "CD-789-012" and row[1] == 200
    assert s.list_room_passwords() == {"room": "CD-789-012"}
    s.delete_room_password("room")
    assert s.get_room_password("room") is None


def test_server_info():
    s = _store()
    s.set_server_info("version", "1.7.7")
    s.set_server_info("version", "1.7.8")  # upsert
    s.set_server_info("started_at", "1234")
    assert s.server_info() == {"version": "1.7.8", "started_at": "1234"}
    assert s.get_server_info("version") == "1.7.8"
    assert s.get_server_info("missing") is None
    assert s.get_server_info("missing", "dflt") == "dflt"


def test_room_status_snapshot_replaces():
    s = _store()
    s.snapshot_room_status([("a", 2, 1, 42.0, 2, 100)])
    s.snapshot_room_status([("b", 0, 0, 0.0, 0, 200)])
    assert s.list_room_status() == [("b", 0, 0, 0.0, 0, 200)]


def test_query_history_filters():
    s = _store()
    s.upsert_watch("alpha", "one.mkv", 10, 5, 10, 1)
    s.upsert_watch("beta", "two.mkv", 10, 5, 10, 2)
    assert len(s.query_history()) == 2
    assert len(s.query_history(room="alpha")) == 1
    assert len(s.query_history(q="one.mkv")) == 1
    assert len(s.query_history(limit=1)) == 1


def test_db_ok_true():
    assert _store().db_ok() is True


# ---------------------------------------------------------------------------
# Postgres (gated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not PG_DSN, reason="set SYNCPLAY_TEST_PG_DSN to run")
def test_postgres_store_roundtrip():
    s = db.BackendStore(PG_DSN)
    assert s.dialect == "postgres"
    assert s.ph == "%s"
    s.upsert_user("pgalice", 1)
    s.upsert_watch("pgroom", "pg.mkv", 10, 5, 10, 1)
    s.save_room("pgroom", "pg.mkv", 0, 5.0, 1)
    s.set_permanent("pgperm")
    s.set_server_info("version", "pg")
    s.snapshot_room_status([("pgroom", 1, 1, 5.0, 1, 1)])
    assert s.count_users() >= 1
    assert s.load_rooms()
    assert "pgperm" in s.list_permanent()
    assert s.get_server_info("version") == "pg"
    assert s.list_room_status()
    s.close()
