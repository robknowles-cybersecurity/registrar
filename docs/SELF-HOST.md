# Self-hosting Registrar with Docker Compose

One command, one SQLite volume, no secrets.

```bash
git clone https://github.com/robknowles-cybersecurity/registrar.git
cd registrar
cp .env.example .env          # optional: change the host bind, port or time zone
docker compose up -d --build
docker compose ps             # STATUS should read "healthy" within ~30 s
```

Then open <http://127.0.0.1:8762/>. Open **Settings** and record the date
each of your credentials' cycles began.

## What runs

- One container, built from the `Dockerfile` in the checkout: the Python
  standard library on a digest-pinned `python:3.13.16-slim` base. Nothing is
  pip-installed.
- The app runs as an unprivileged user (uid 10001) on a read-only root
  filesystem, with every Linux capability dropped and `no-new-privileges`.
- Your ledger (`registrar.db`) and evidence files live in the named volume
  `registrar-data`, mounted at `/data`. Removing the container keeps them;
  `docker compose down -v` deletes them.
- A health check calls `/health` inside the container every 30 seconds.

## Settings (`.env`)

| Variable | Default | |
|---|---|---|
| `REGISTRAR_HOST_BIND` | `127.0.0.1` | host address the port is published on |
| `REGISTRAR_HOST_PORT` | `8762` | host port |
| `TZ` | `America/New_York` | what "today" means for cycles and deadlines |

**Registrar has no login.** Inside the container it listens on the
container's own interfaces; the host-side publish address is the boundary.
Keep `REGISTRAR_HOST_BIND` on loopback, or set it to the address of one
private interface you already trust (a VPN or overlay network). Never a
public address, never `0.0.0.0`.

Nothing in `.env` is secret, and nothing secret belongs there: Registrar
stores no portal logins and needs no API keys.

## Checks

```bash
docker inspect registrar --format '{{.State.Health.Status}}'   # healthy
docker exec registrar python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8762/health').status)"   # 200
ss -ltnp | grep 8762         # 127.0.0.1:8762 (or your private address), never 0.0.0.0
```

## Backups

The ledger is your compliance record. Stop the container first so SQLite
folds its write-ahead log into the database file, then copy the volume:

```bash
docker compose stop
docker run --rm -v registrar_registrar-data:/data:ro -v "$PWD":/backup \
  python:3.13.16-slim tar czf /backup/registrar-backup.tgz -C /data .
docker compose start
```

The volume name is `<project>_registrar-data`; `docker volume ls` shows the
project prefix (the checkout directory's name by default).

## Demo data

To try it with invented sample data instead of an empty ledger:

```bash
docker compose down -v
docker run --rm -v registrar_registrar-data:/data \
  -v "$PWD/tools:/src/tools:ro" -v "$PWD/fixtures:/src/fixtures:ro" \
  -e PYTHONPATH=/app registrar:local \
  python3 /src/tools/demo.py --db /data/registrar.db
docker compose up -d
```

## Upgrading

```bash
git pull
docker compose up -d --build
```

The schema migrates itself on start; migrations are additive only.
