<!---
# Copyright (C) 2019 Syncplay
# This file is licensed under the MIT license - http://opensource.org/licenses/MIT

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
-->

# Syncplay
![GitHub Actions build status](https://github.com/Syncplay/syncplay/workflows/Build/badge.svg)

Solution to synchronize video playback across multiple instances of mpv, VLC, MPC-HC, MPC-BE and mplayer2 over the Internet.

## Official website
https://syncplay.pl

## Download
https://syncplay.pl/download/

## What does it do

Syncplay synchronises the position and play state of multiple media players so that the viewers can watch the same thing at the same time.
This means that when one person pauses/unpauses playback or seeks (jumps position) within their media player then this will be replicated across all media players connected to the same server and in the same 'room' (viewing session).
When a new person joins they will also be synchronised. Syncplay also includes text-based chat so you can discuss a video as you watch it (or you could use third-party Voice over IP software to talk over a video).

## What it doesn't do

Syncplay is not a file sharing service.

## Watch history (this fork)

This fork adds a **watch-history** feature: a single Docker container runs the unmodified Syncplay server behind a tiny transparent TCP proxy. The proxy forwards all client traffic unchanged while recording what is being watched into a database. No client-side changes are required — existing clients just point at the proxy port.

It records:

- `users` — every username that has ever connected.
- `watch_history` — one row per (room, file): file name, size, duration, furthest position reached (`watched`, seconds), `percentage`, and timestamps.

### Quick start (Docker Compose)

```sh
cp secrets.env.example .secrets.env   # only needed when using a server database
docker compose up -d --build
```

Clients connect to port **12346** (the proxy). The database defaults to SQLite inside the container, persisted in the named volume (`syncplay_syncplay_history_data` — the compose project prefix plus the volume name). Stop with `docker compose down`.

### Plain Docker / Makefile

```sh
make build && make up
```

(See [DOCKER.md](DOCKER.md) for the `docker run`/`make` targets.)

### Configuration

- `config.env` (committed) — ports and all non-secret server options.
- `.secrets.env` (gitignored) — `DB_PASS`, optional `SYNCPLAY_PASSWORD` / `SYNCPLAY_SALT`.
- Database backend selected with `HISTORY_DSN`: default `sqlite:////data/history.sqlite`, or `postgres://user:password@host:5432/dbname` for Postgres.

Every Syncplay server option is exposed as an environment variable; [DOCKER.md](DOCKER.md) has the full reference (option table, DSN, inspecting the database, WAL notes).

### Testing without the GUI client

`test_client.py` is a synthetic client that plays a short deterministic session through the proxy:

```sh
docker cp test_client.py syncplay-history:/tmp/tc.py
docker exec syncplay-history python3 /tmp/tc.py --username tester --room test-room
```

You should then see a `tester` user and a `watch_history` row (see [DOCKER.md](DOCKER.md#inspecting-the-history)).

## License

This project, the Syncplay released binaries, and all the files included in this repository unless stated otherwise in the header of the file, are licensed under the [Apache License, version 2.0](https://www.apache.org/licenses/LICENSE-2.0.html). A copy of this license is included in the LICENSE file of this repository. Licenses and attribution notices for third-party media are set out in [third-party-notices.txt](syncplay/resources/third-party-notices.txt).

## Authors
* *Initial concept and core internals developer* - Uriziel.
* *GUI design and current lead developer* - Et0h.
* *Original SyncPlay code* - Tomasz Kowalczyk (Fluxid), who developed SyncPlay at https://github.com/fluxid/syncplay
* *Other contributors* - See http://syncplay.pl/about/development/
