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

ENTRYPOINT ["python3", "/app/entrypoint.sh"]
