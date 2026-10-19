FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY syncplay/ /app/syncplay/
COPY syncplayServer.py /app/syncplayServer.py
COPY entrypoint.sh /app/entrypoint.sh
# config.env is NOT baked in: the make/docker-run flow bind-mounts it (see
# Makefile) and the compose flow passes plain environment variables. Baking
# it in would shadow both. entrypoint.sh handles its absence gracefully.
# .secrets.env is gitignored and mounted in at run time; entrypoint.sh handles
# its absence gracefully.

VOLUME ["/data"]

# Non-secret defaults live in config.env (mounted in). Secrets live in
# .secrets.env (mounted in). Keep the image ENV minimal; no secrets here.
ENV PYTHONUNBUFFERED=1 \
    HISTORY_DSN=sqlite:////data/history.sqlite

EXPOSE 12346

# Probe the proxy port (the client-facing port). Hardcodes the default
# PROXY_PORT; if you change PROXY_PORT, update this too.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import socket,sys; s=socket.socket(); s.settimeout(3); sys.exit(0 if s.connect_ex(('127.0.0.1',12346))==0 else 1)"

ENTRYPOINT ["python3", "/app/entrypoint.sh"]
