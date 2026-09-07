# Syncplay watch-history (Docker)

A single container runs the unmodified Syncplay server and a tiny transparent
TCP proxy in front of it. The proxy forwards all client traffic to the server
without altering it, while recording what is being watched into a SQLite
database. No client-side changes are required.

## What it records

- `users` – every username that has ever connected (`first_seen`, `last_seen`).
- `watch_history` – one row per (room, file):
  - `room`, `file`, `size`, `duration`
  - `watched` – furthest position reached in seconds
  - `percentage` – `watched / duration * 100`
  - `stopped_at` – timestamp when the connection for that (room, file) closed
  - `started_at`, `last_seen`

The watch status is owned by the room (keyed by room + file), as requested.

## Database (DSN)

The backend is selected with a single DSN, via the `HISTORY_DSN` environment
variable (or the `--dsn` flag of `history.py`).

- **Default (SQLite):** `sqlite:////data/history.sqlite`
- **Postgres:** `postgres://user:password@host:5432/dbname`

```sh
# SQLite (default) – persisted in the /data volume
docker run -d --name syncplay-history \
  -p 12346:12346 \
  -v syncplay_history_data:/data \
  syncplay-history

# Postgres
docker run -d --name syncplay-history \
  -p 12346:12346 \
  -e HISTORY_DSN='postgres://user:password@dbhost:5432/syncplay_history' \
  syncplay-history
```

SQLite is the default, so no configuration is needed for the common case.
Postgres/MariaDB support is ready to add behind the same DSN interface; MariaDB
and MySQL are deferred for now.

## Build and run

```sh
docker build -t syncplay-history .
```

Run it as described in the [Database (DSN)](#database-dsn) section above.

- Clients connect to the **proxy** on port **12346**.
- The Syncplay server listens internally on **12345**.

### Makefile shortcuts

The `GNUmakefile` has Docker targets (run from the repo root):

| Command | What it does |
|---|---|
| `make up` | `docker rm -f` + start the container, then print logs. Mounts `config.env` and `.secrets.env` (if present). |
| `make rerun` | Same as `up` (remove + start, no rebuild). |
| `make build` | Build the image. |
| `make rebuild` | Build the image, then `up`. |
| `make logs` | Show the last 500 log lines. |
| `make down` | Stop and remove the container. |

Typical loop after changing `history.py`/`Dockerfile`/`entrypoint.sh`:

```sh
make rebuild
```

### Configuration files

Configuration is split so secrets never sit next to ordinary settings:

| File | Committed? | Holds | Loaded by |
|---|---|---|---|
| `config.env` | yes | ports, DB host/port/user/dbname, all non-secret server options | `entrypoint.sh` |
| `.secrets.env` | **no** (gitignored) | `DB_PASS`, optional `SYNCPLAY_PASSWORD` / `SYNCPLAY_SALT` | `entrypoint.sh` |

- Copy `secrets.env.example` to `.secrets.env` and fill in the password.
- Both files are bind-mounted into the container read-only, so the **password is
  never on a command line** and never shows in `make -n` output.
- Real environment variables (`docker run -e KEY=...`) override values from
  these files.
- If `DB_HOST` is empty (or `.secrets.env` is absent), the proxy falls back to
  the built-in SQLite default `sqlite:////data/history.sqlite`.

The Makefile mounts these files at run time, so you edit them on the host and
just `make rerun` — no rebuild needed when only the config changes.

## Server configuration (environment variables)

Every Syncplay server option is exposed as an environment variable. Unset
variables fall back to the server's own defaults, so the common case needs none
of them. `--password` and `--salt` are read directly from the environment by the
unmodified server code; the rest are mapped by `entrypoint.sh`.

| Setting (env var) | Server option | Default | Where / Notes |
|---|---|---|---|
| `SYNCPLAY_PORT` | `--port` | `12345` | `config.env`. Internal port the proxy forwards to (not published). |
| `SYNCPLAY_PASSWORD` | `--password` | — | `.secrets.env`. MD5-hashed by the server. |
| `SYNCPLAY_SALT` | `--salt` | random per start | `.secrets.env`. Set a stable value so controlled-room operator passwords survive restarts. |
| `SYNCPLAY_ISOLATE_ROOMS` | `--isolate-rooms` | `false` | Boolean (`true`/`1`/`yes`/`on`). |
| `SYNCPLAY_DISABLE_READY` | `--disable-ready` | `false` | Boolean. |
| `SYNCPLAY_DISABLE_CHAT` | `--disable-chat` | `false` | Boolean. |
| `SYNCPLAY_MOTD_FILE` | `--motd-file` | — | Path inside the container to the MOTD file. |
| `SYNCPLAY_ROOMS_DB_FILE` | `--rooms-db-file` | — | Enables room persistence (SQLite file, e.g. `/data/rooms.sqlite`). |
| `SYNCPLAY_PERMANENT_ROOMS_FILE` | `--permanent-rooms-file` | — | Requires room persistence. One room name per line. |
| `SYNCPLAY_MAX_CHAT_MESSAGE_LENGTH` | `--max-chat-message-length` | `150` | Integer. |
| `SYNCPLAY_MAX_USERNAME_LENGTH` | `--max-username-length` | `16` | Integer. |
| `SYNCPLAY_STATS_DB_FILE` | `--stats-db-file` | — | Enables server statistics (SQLite file). |
| `SYNCPLAY_TLS_PATH` | `--tls` | — | Path to a directory holding the TLS certificate files. |
| `SYNCPLAY_IPV4_ONLY` | `--ipv4-only` | `false` | Boolean. |
| `SYNCPLAY_IPV6_ONLY` | `--ipv6-only` | `false` | Boolean. |
| `SYNCPLAY_INTERFACE_IPV4` | `--interface-ipv4` | — | Bind interface for IPv4. |
| `SYNCPLAY_INTERFACE_IPV6` | `--interface-ipv6` | — | Bind interface for IPv6. |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_NAME` | — | — | `config.env`. Non-secret DB connection parts. Leave `DB_HOST` empty for the SQLite default. |
| `DB_PASS` | — | — | `.secrets.env`. Watch-history DB password. |
| `PROXY_PORT` | — | `12346` | `config.env`. The port clients connect to (the proxy). |
| `HISTORY_DSN` | — | `sqlite:////data/history.sqlite` | Optional full DSN override; takes precedence over the `DB_*` parts. |

File-based options (`--motd-file`, `--rooms-db-file`, `--permanent-rooms-file`,
`--stats-db-file`, `--tls`) take a **path inside the container**. Mount or copy
the files in, e.g. `docker run -v /host/certs:/app/tls -e
SYNCPLAY_TLS_PATH=/app/tls ...`.

Example with a few extra options set (the password stays in `.secrets.env`):

```sh
# put non-secret extras in config.env, e.g.
#   SYNCPLAY_ROOMS_DB_FILE=/data/rooms.sqlite
#   SYNCPLAY_MAX_USERNAME_LENGTH=30
# and put secrets in .secrets.env, e.g.
#   DB_PASS=secret
#   SYNCPLAY_SALT=my-stable-salt
docker run -d --name syncplay-history \
  -p 12346:12346 \
  -v syncplay_history_data:/data \
  -v $(pwd)/config.env:/app/config.env:ro \
  -v $(pwd)/.secrets.env:/app/.secrets.env:ro \
  syncplay-history
```

(Or just use `make up`, which does exactly this.)

## How it works

- `syncplay/history.py` is a minimal Twisted TCP proxy. It forwards bytes both
  ways between the client and the server, and parses the newline-delimited JSON
  Syncplay protocol to capture `Hello` (username), `Set.room`, `Set.file`
  (name/size/duration) and `State` (playstate position).
- `entrypoint.sh` starts both the server and the proxy in the same container.
- The Syncplay client/server code is not modified.

## Inspecting the history

Always query the database **inside the running container**.

```sh
docker exec syncplay-history python3 -c "
import sqlite3
c = sqlite3.connect('/data/history.sqlite')
print(c.execute('select * from users').fetchall())
for r in c.execute('select room,file,size,watched,percentage,duration,stopped_at from watch_history'):
    print(r)
"
```

### Why not `docker cp` the file out?

The database uses SQLite **WAL mode** (for concurrent reads). Recent writes
live in `history.sqlite-wal` until a checkpoint merges them into the main file.
Copying only `history.sqlite` while the container runs (or right after a stop)
can give an outdated or empty database — the classic `no such table` trap.

To get a consistent copy for offline inspection, checkpoint first:

```sh
docker exec syncplay-history python3 -c "import sqlite3; c = sqlite3.connect('/data/history.sqlite'); c.execute('pragma wal_checkpoint(TRUNCATE)'); c.close()"
docker cp syncplay-history:/data/history.sqlite ./history.sqlite
```

The proxy also runs an automatic `TRUNCATE` checkpoint every 30 seconds, so the
main file is at most ~30 s behind the WAL.

## Testing without a real client

`test_client.py` is a tiny synthetic Syncplay client that plays a short
deterministic session through the proxy, so you can confirm recording works
without the GUI client:

```sh
# from the host, with the container running
docker cp test_client.py syncplay-history:/tmp/tc.py
docker exec syncplay-history python3 /tmp/tc.py --username tester --room test-room
```

Then inspect the database (see above) — you should see a `tester` user and a
`watch_history` row for the room/file.

## Linting

The watch-history code we own is linted with [ruff](https://docs.astral.sh/ruff/)
(config in `ruff.toml`). The original Syncplay sources are **excluded on purpose**
— they are upstream code and must not be modified, so we don't lint them.

```sh
ruff check          # lints only syncplay/history.py and test_client.py
```

`ruff` is the linter that honors the project config. The editor's built-in type
checker (pyright/ty) uses its own interpreter, so to avoid false "could not be
resolved" errors for `twisted` / `psycopg2`, point it at the project venv.

### Local development venv (uv)

A `pyproject.toml` declares the runtime deps (mirroring `requirements.txt` 1:1,
so the dev environment matches the headless Docker image), and a `.venv` is
created for it. Zed's uv integration auto-detects `.venv` as the project
interpreter once the LSP is (re)started.

```sh
uv venv            # create .venv (once)
uv sync            # install the server deps (matches the image)
uv sync --extra gui # also install the PySide GUI client (desktop only)
```

If the editor still shows `twisted` / `psycopg2` "could not be resolved" after
setting this up, restart the Python LSP (command palette → *Restart Server*, or
reopen the folder) so it re-resolves the interpreter to `.venv/bin/python`.

(`pyrightconfig.json` is also present for anyone running standalone pyright.)

## Notes

- The schema is intentionally simple so the data can be migrated between
  backends (SQLite / Postgres, and MariaDB/MySQL later).
- No housekeeping/cleanup is performed.
