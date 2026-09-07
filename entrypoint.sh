#!/usr/bin/env python3
import os
import subprocess

# config.env holds non-secret settings (committed); .secrets.env holds only
# secrets (gitignored). Both are optional. Real environment variables always
# take precedence over values from these files.


def loadEnvFile(path):
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            # existing environment wins over file values
            if os.environ.get(key) in (None, ""):
                os.environ[key] = value


def env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def toFlag(name, flag, default="false"):
    # map an env var to a boolean flag; truthy values enable it
    if env(name, default).lower() in ("1", "true", "yes", "on"):
        return [flag]
    return []


loadEnvFile("/app/config.env")
loadEnvFile("/app/.secrets.env")

upstream_port = env("SYNCPLAY_PORT", "12345")
proxy_port = env("PROXY_PORT", "12346")

# The database connection (DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME) is read
# by the proxy directly from the environment. The password therefore never
# appears on any command line.
server = ["python3", "/app/syncplayServer.py", "--port", upstream_port]

# The original server reads --password and --salt from SYNCPLAY_PASSWORD /
# SYNCPLAY_SALT itself, so we do not re-pass them here. Every other option
# defaults to the server's own default when its env var is unset.
if env("SYNCPLAY_MOTD_FILE"):
    server += ["--motd-file", env("SYNCPLAY_MOTD_FILE")]
if env("SYNCPLAY_ROOMS_DB_FILE"):
    server += ["--rooms-db-file", env("SYNCPLAY_ROOMS_DB_FILE")]
if env("SYNCPLAY_PERMANENT_ROOMS_FILE"):
    server += ["--permanent-rooms-file", env("SYNCPLAY_PERMANENT_ROOMS_FILE")]
if env("SYNCPLAY_STATS_DB_FILE"):
    server += ["--stats-db-file", env("SYNCPLAY_STATS_DB_FILE")]
if env("SYNCPLAY_TLS_PATH"):
    server += ["--tls", env("SYNCPLAY_TLS_PATH")]
if env("SYNCPLAY_MAX_CHAT_MESSAGE_LENGTH"):
    server += ["--max-chat-message-length", env("SYNCPLAY_MAX_CHAT_MESSAGE_LENGTH")]
if env("SYNCPLAY_MAX_USERNAME_LENGTH"):
    server += ["--max-username-length", env("SYNCPLAY_MAX_USERNAME_LENGTH")]
if env("SYNCPLAY_IPV4_ONLY", "false").lower() in ("1", "true", "yes", "on"):
    server += ["--ipv4-only"]
if env("SYNCPLAY_IPV6_ONLY", "false").lower() in ("1", "true", "yes", "on"):
    server += ["--ipv6-only"]
if env("SYNCPLAY_INTERFACE_IPV4"):
    server += ["--interface-ipv4", env("SYNCPLAY_INTERFACE_IPV4")]
if env("SYNCPLAY_INTERFACE_IPV6"):
    server += ["--interface-ipv6", env("SYNCPLAY_INTERFACE_IPV6")]
server += toFlag("SYNCPLAY_ISOLATE_ROOMS", "--isolate-rooms")
server += toFlag("SYNCPLAY_DISABLE_READY", "--disable-ready")
server += toFlag("SYNCPLAY_DISABLE_CHAT", "--disable-chat")

server_proc = subprocess.Popen(server)
proxy_proc = subprocess.Popen(
    [
        "python3",
        "/app/syncplay/history.py",
        "--port",
        proxy_port,
        "--upstream-host",
        "127.0.0.1",
        "--upstream-port",
        upstream_port,
    ]
)
try:
    server_proc.wait()
except KeyboardInterrupt:
    pass
finally:
    for proc in (proxy_proc, server_proc):
        if proc.poll() is None:
            proc.terminate()
