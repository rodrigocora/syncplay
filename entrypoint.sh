#!/usr/bin/env python3
import os
import signal
import socket
import subprocess
import sys
import time

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


def wait_for_port(host, port, proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def terminate_all(procs):
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()


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

# Start the server and wait until it is actually listening before bringing up
# the proxy, so early client connections are never pointed at a dead upstream.
server_proc = subprocess.Popen(server)
if not wait_for_port("127.0.0.1", int(upstream_port), server_proc):
    print(f"server not ready (rc={server_proc.poll()}) on port {upstream_port}; aborting")
    terminate_all([server_proc])
    sys.exit(1)

proxy_cmd = [
    "python3",
    "/app/syncplay/history.py",
    "--port",
    proxy_port,
    "--upstream-host",
    "127.0.0.1",
    "--upstream-port",
    upstream_port,
]
proxy_proc = subprocess.Popen(proxy_cmd)


def _request_stop(signum, _frame):
    # A clean container stop (SIGTERM/SIGINT) exits 0 so orchestrators do not
    # treat a normal shutdown as a crash.
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)

# Supervise both children. The proxy is the less critical one: restart it a
# bounded number of times on unexpected death. The server is the source of
# truth: if it exits, tear everything down (exit 0 only for a clean stop).
PROXY_MAX_RESTARTS = 5
proxy_restarts = 0
exit_code = 0
try:
    while True:
        server_rc = server_proc.poll()
        if server_rc is not None:
            exit_code = 0 if server_rc == 0 else 1
            print(f"server exited rc={server_rc}; shutting down")
            break
        proxy_rc = proxy_proc.poll()
        if proxy_rc is not None:
            if proxy_restarts >= PROXY_MAX_RESTARTS:
                exit_code = 1
                print(f"proxy exited rc={proxy_rc}; too many restarts; shutting down")
                break
            proxy_restarts += 1
            delay = min(2 * proxy_restarts, 10)
            print(f"proxy exited rc={proxy_rc}; restarting ({proxy_restarts}/{PROXY_MAX_RESTARTS}) in {delay}s")
            time.sleep(delay)
            proxy_proc = subprocess.Popen(proxy_cmd)
        time.sleep(0.5)
except SystemExit:
    pass
finally:
    terminate_all([server_proc, proxy_proc])
    sys.exit(exit_code)
