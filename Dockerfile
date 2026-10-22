# Registrar web app, self-hosted. Standard library only: nothing is pip-installed.
# Base image pinned by digest; see docs/DEPENDENCIES.md.
FROM python:3.13.16-slim@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CPEBOT_DB=/data/registrar.db \
    CPEBOT_EVIDENCE=/data/evidence \
    CPEBOT_BODIES=/app/bodies.yaml \
    CPEBOT_PACKETMAPS=/app/packetmaps \
    CPEBOT_PORT=8762 \
    CPEBOT_BIND=0.0.0.0

# Inside the container the app listens on all of the container's own
# interfaces; docker-compose.yml publishes that port on the host's loopback
# only. The host-side bind is the boundary, as it is without Docker.

RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin registrar \
 && mkdir -p /data/evidence /app \
 && chown -R registrar:registrar /data && chmod 0700 /data

WORKDIR /app
COPY --chown=root:root cpebot/ /app/cpebot/
COPY --chown=root:root packetmaps/ /app/packetmaps/
COPY --chown=root:root bodies.yaml LICENSE NOTICE /app/

USER registrar
VOLUME ["/data"]
EXPOSE 8762

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python3", "-c", "import sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8762/health', timeout=4); sys.exit(0 if r.status == 200 else 1)"]

CMD ["python3", "-m", "cpebot.web"]
