# Main Goal

Store the history of watched files(not need to check metada, filename is ok), or if stopped what was the minute. The application doens't need to resume or do anything. Maybe later on. I just want to know what I watched with my friends.

- [x] Create a Dockerfile to:
  - [x] Install the simplest proxy tool that is able to intercecept request and filter.
  - [x] Route the resquests to the syncplay server, that must be in the same container.
  - [x] In the future this data will be stored in postgres and mariadb databases, but for now let's work with sqlite.
  - [x] room, file, size, watched and duration is the fields I want. watch can be either time os percentage, or have both fields.
  - [x] Do not alter anything on the client side.
  - [x] No housekeeping routine is needed for now.
  - [x] Connection should be configured to sqlite as default.
  - [x] You my register the usernames, but the watch status is owned by the room.
  - [x] Do the lest possible modification in the original code.
  - [x] Create a DSN(SQLite/MySQL/MariaDB/Postgres) var.
- [x] Organize the files and clean what is not need anymore.
- [x] Expose all syncplay server options via environment variables.
- [x] Split config into config.env (committed, non-secret) and .secrets.env (gitignored, passwords only); password never on the command line.
- [x] Persistent rooms: enable `SYNCPLAY_ROOMS_DB_FILE` + `SYNCPLAY_PERMANENT_ROOMS_FILE`, `data/` bind mount, `data/permanent_rooms.txt` (SextaDosAnimes); document in README.md. (added by AI, 2026-09-19)
- [x] Document the BuildKit DNS build failure (IPv6-only host resolver) and the `daemon.json` fix in DOCKER.md. (added by AI, 2026-09-19)
- [x] Backend scope fixed to SQLite/Postgres (MariaDB/MySQL out of scope); all-Postgres deployment without the SQLite rooms DB (no mixed backends); proxy port bound to 127.0.0.1 for reverse-proxy exposure. (added by AI, 2026-09-19)
