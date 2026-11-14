# Syncplay watch-history (Docker)

A single container runs the unmodified Syncplay server, a tiny transparent TCP
proxy in front of it, and the admin web app. The proxy forwards all client
traffic to the server without altering it, while recording what is being
watched into a shared backend database. No client-side changes are required.

## What it records

- `users` – every username that has ever connected (`first_seen`, `last_seen`).
- `watch_history` – one row per (room, file):
  - `room`, `file`, `size`, `duration`
  - `watched` – furthest position reached in seconds
  - `percentage` – `watched / duration * 100`
  - `stopped_at` – timestamp when the connection for that (room, file) closed
  - `started_at`, `last_seen`
- `persistent_rooms` – saved room playlists/positions (written by the server).
- `permanent_rooms` – undeletable room names (the DB is the source of truth;
  seeded once from the legacy `permanent_rooms.txt` on first run).
- `server_info` – server version, `started_at`, and a heartbeat (`updated_at`).
- `room_status` – live snapshot of every room (watchers, play state, position).

The watch status is owned by the room (keyed by room + file), as requested.

## Database (DSN)

One shared backend serves rooms, watch history, and the admin app. It is
selected with a single DSN, resolved in this order:

1. `DB_*` env vars (`DB_HOST` set) → Postgres
2. `SYNCPLAY_DSN` (full DSN string)
3. `HISTORY_DSN` (legacy alias)
4. built-in SQLite default `sqlite:////data/syncplay.sqlite`

- **Default (SQLite):** `sqlite:////data/syncplay.sqlite`
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
  -e SYNCPLAY_DSN='postgres://user:password@dbhost:5432/syncplay_history' \
  syncplay-history
```

SQLite is the default, so no configuration is needed for the common case.
The supported backends are SQLite and Postgres only; MariaDB and MySQL are out
of scope. The previous per-feature files (`history.sqlite`, `rooms.sqlite`,
`permanent_rooms.txt`) are replaced by this single DB.

## Build and run

```sh
docker build -t syncplay-history .
```

Run it as described in the [Database (DSN)](#database-dsn) section above.

- Clients connect to the **proxy** on port **12346**.
- The Syncplay server listens internally on **12345**.

### Docker Compose

`compose.yaml` runs the same image with all settings in its `environment:`
block (no `config.env` mount). Write the values you need there, then:

```sh
cp secrets.env.example .secrets.env   # when using a server database
docker compose up -d --build
docker compose down
```

The variable reference lives in the [README](README.md#quick-start-docker-compose).

### Behind a reverse proxy

The proxy speaks raw TCP, not HTTP — the reverse proxy must forward at the
TCP/stream layer (Caddy `tcp` site block, nginx `stream`, etc.). `compose.yaml`
binds the port to `127.0.0.1`, so only a same-host proxy can reach the server;
point it at `127.0.0.1:12346`.

The service also attaches to the external `proxy-tier` network (create it on
the host first, e.g. `docker network create proxy-tier`). A containerized
reverse proxy on that network targets `syncplay-history:12346` instead of the
loopback port.

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
| `SYNCPLAY_PERMANENT_ROOMS_FILE` | `--permanent-rooms-file` | — | Legacy one-time seed of permanent rooms (read only when the `permanent_rooms` table is empty; the DB is the source of truth). |
| `SYNCPLAY_MAX_CHAT_MESSAGE_LENGTH` | `--max-chat-message-length` | `150` | Integer. |
| `SYNCPLAY_MAX_USERNAME_LENGTH` | `--max-username-length` | `16` | Integer. |
| `SYNCPLAY_STATS_DB_FILE` | `--stats-db-file` | — | Enables server statistics (SQLite file). |
| `SYNCPLAY_TLS_PATH` | `--tls` | — | Path to a directory holding the TLS certificate files. |
| `SYNCPLAY_IPV4_ONLY` | `--ipv4-only` | `false` | Boolean. |
| `SYNCPLAY_IPV6_ONLY` | `--ipv6-only` | `false` | Boolean. |
| `SYNCPLAY_INTERFACE_IPV4` | `--interface-ipv4` | — | Bind interface for IPv4. |
| `SYNCPLAY_INTERFACE_IPV6` | `--interface-ipv6` | — | Bind interface for IPv6. |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_NAME` | — | — | `config.env`. Non-secret DB connection parts. Leave `DB_HOST` empty for the SQLite default. |
| `DB_PASS` | — | — | `.secrets.env`. Backend DB password. |
| `PROXY_PORT` | — | `12346` | `config.env`. The port clients connect to (the proxy). |
| `ADMIN_PORT` | — | `12347` | `config.env`. Admin web app port (loopback only). |
| `ADMIN_TOKEN` | — | unset (open) | `.secrets.env`. Optional bearer token for the admin API. |
| `SYNCPLAY_DSN` | — | `sqlite:////data/syncplay.sqlite` | Optional full DSN; used when `DB_HOST` is not set. |
| `HISTORY_DSN` | — | — | Legacy alias of `SYNCPLAY_DSN` (same precedence slot). |

File-based options (`--motd-file`, `--permanent-rooms-file`, `--stats-db-file`,
`--tls`) take a **path inside the container**. Mount or copy the files in, e.g.
`docker run -v /host/certs:/app/tls -e SYNCPLAY_TLS_PATH=/app/tls ...`.

Example with a few extra options set (the password stays in `.secrets.env`):

```sh
# put non-secret extras in config.env, e.g.
#   SYNCPLAY_MAX_USERNAME_LENGTH=30
# and put secrets in .secrets.env, e.g.
#   DB_PASS=secret
#   SYNCPLAY_SALT=my-stable-salt
#   ADMIN_TOKEN=my-admin-token
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
- `syncplay/server.py` (a small modification of the upstream server) persists
  rooms and permanent rooms in the same backend DB, and publishes a heartbeat
  + `room_status` snapshot every 30 s.
- `syncplay/admin.py` is the FastAPI admin web app (port 12347, loopback only).
- `entrypoint.sh` starts and supervises the server, the proxy, and the admin
  app in the same container.
- The Syncplay client code is not modified.

## Admin web app

The admin app exposes health, room management, watch history, and users over
HTTP on port **12347**, bound to `127.0.0.1` — expose it through the same
same-host reverse proxy as the main service (a Caddy `handle`/location block or
nginx `location /` proxying to `127.0.0.1:12347`).

- **No login required by default.** Set `ADMIN_TOKEN` (in `.secrets.env`) to
  require an `X-Admin-Token` header (or `?token=` query param) on all `/api/*`
  endpoints. The static page itself stays open; the API calls on it carry the
  token when the page is opened as `/?token=...`.
- **Room passwords are stored plaintext** in the backend DB (`room_passwords`
  table) so an operator can view/change them; the server itself never reads
  that table — it verifies from the room-name hash + salt. If you set room
  passwords, set `ADMIN_TOKEN` (or put auth on the reverse-proxy location) so
  in-container access to `127.0.0.1:12347` cannot enumerate them. The admin
  also inherits `SYNCPLAY_SALT` from `.secrets.env` to display the derived
  `+room:hash` name — keep it in sync with the server's salt.
- **Changes apply on restart.** Room/playlist/permanent changes are written to
  the backend DB and read by the server at its next startup (`docker compose
  restart syncplay`).

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | The admin page (static, vanilla JS). |
| `/api/health` | GET | Server version, uptime, heartbeat age, `alive` flag, DB status, active/permanent room counts, user count. |
| `/api/rooms` | GET | All rooms (persistent + permanent + live status). |
| `/api/rooms` | POST | Create a room (`{name, permanent?}`). 409 if it exists. |
| `/api/rooms/{name}` | GET | One room incl. its playlist. |
| `/api/rooms/{name}` | DELETE | Delete a non-permanent room. 409 if permanent. |
| `/api/rooms/{name}/permanent` | POST | Set/unset permanent (`{permanent}`). |
| `/api/rooms/{name}/playlist` | POST | Set playlist (`{playlist: [...], index?}`). |
| `/api/rooms/{name}/password` | GET | Stored controlled-room password (404 if room missing / none set). |
| `/api/rooms/{name}/password` | POST | Set/change password (`{password}`, format `XX-000-000`). 404 if room unknown, 400 on a `+name:hash` form. |
| `/api/rooms/{name}/password` | DELETE | Remove the stored password. |
| `/api/history` | GET | Watch history (`?room=`, `?q=`, `?limit=`). |
| `/api/users` | GET | Usernames with first/last seen (`?limit=`). |

## Inspecting the history

Always query the database **inside the running container** (or through the
admin app).

```sh
docker exec syncplay-history python3 -c "
import sys; sys.path.insert(0, '/app/syncplay')
import db
s = db.open_store_with_retry()
print('users:', s.list_users())
print('history:', s.query_history())
print('rooms:', s.load_rooms())
print('permanent:', s.list_permanent())
"
```

With a Postgres DSN, the same container code resolves the DSN from the mounted
`.secrets.env` + `config.env` (the password is never on a command line).

### Why not `docker cp` the file out?

The database uses SQLite **WAL mode** (for concurrent reads). Recent writes
live in `syncplay.sqlite-wal` until a checkpoint merges them into the main file.
Copying only `syncplay.sqlite` while the container runs (or right after a stop)
can give an outdated or empty database — the classic `no such table` trap.

To get a consistent copy for offline inspection, checkpoint first:

```sh
docker exec syncplay-history python3 -c "import sqlite3; c = sqlite3.connect('/data/syncplay.sqlite'); c.execute('pragma wal_checkpoint(TRUNCATE)'); c.close()"
docker cp syncplay-history:/data/syncplay.sqlite ./syncplay.sqlite
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

`simulate_clients.py` runs the S1–S10 scenario matrix against the live proxy.
`test_rooms_persistence.py` and `test_admin_smoke.py` are the E2E checks for
rooms persistence and the admin API. Host-side unit tests live in `tests/`
(`make test`).

## Linting

The watch-history code we own is linted with [ruff](https://docs.astral.sh/ruff/)
(config in `ruff.toml`). The original Syncplay sources are **excluded on purpose**
— they are upstream code and must not be modified, so we don't lint them.

```sh
ruff check          # lints only the files listed in ruff.toml
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

## Troubleshooting

### Build fails: `Failed to resolve 'pypi.org'` during `pip install`

Build steps run in a BuildKit sandbox before any container starts. If the host's `/etc/resolv.conf` lists only IPv6 nameservers (common on cloud VPSes), the sandbox cannot reach them and `pip` fails DNS resolution — even though `ping` from the host works. Regular containers are often unaffected: Docker substitutes a default resolver for them, but BuildKit does not.

Fix: give the daemon an explicit IPv4 resolver in `/etc/docker/daemon.json`, then restart Docker:

```json
{
  "dns": ["9.9.9.9", "149.112.112.112"]
}
```

```sh
sudo systemctl restart docker
```

## Notes

- The schema is intentionally simple so the data can be migrated between the
  two supported backends (SQLite / Postgres).
- No housekeeping/cleanup is performed.
- On SELinux systems (Fedora) the `config.env`/`.secrets.env` mounts carry the
  `,z` label flag; without it the container cannot read the files and the
  proxy silently falls back to SQLite.
