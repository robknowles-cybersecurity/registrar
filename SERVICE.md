# SERVICE — Registrar (self-host card)

A service card to fill in for your own deployment. Every `<PLACEHOLDER>` is
yours to replace; nothing here describes a real host.

**Purpose:** continuing-education registrar — Telegram capture + web UI over
one SQLite ledger.

**Host:** `<HOSTNAME>`

**Bind address and port:**
- `cpebot-web.service` — `<BIND_ADDRESS>:<PORT>` (default `127.0.0.1:8762`).
  Use loopback or one private interface you already trust. Never `0.0.0.0`,
  never a public interface: there is no authentication.
- Allowed host names: `localhost`, `127.0.0.1`, `[::1]` and the bind address
  always; add any name you browse to it by (`<HOST_NAMES>`) in
  `CPEBOT_ALLOWED_HOSTS`, comma-separated. Requests with any other `Host`
  header get `400 Bad Host header` (DNS-rebinding guard).
- `cpebot-tg.service` — no listener (Telegram long-poll client, not a server).

**Units:** systemd `--user` units, templates in `systemd/`:
- `cpebot-web.service` → `python3 -m cpebot.web`
- `cpebot-tg.service` → `python3 -m cpebot.tgcapture`

Installed under `~/.config/systemd/user/` of `<SERVICE_USER>`, with
`WorkingDirectory=<INSTALL_DIR>`. Enable lingering
(`loginctl enable-linger <SERVICE_USER>`) so they run without a login session.

**Paths:**
- Code: `<INSTALL_DIR>` (a git checkout)
- Rules: `<INSTALL_DIR>/bodies.yaml` (`CPEBOT_BODIES`)
- Ledger: `<DATA_DIR>/cpebot.db` (`CPEBOT_DB`) — the compliance record
- Evidence: `<DATA_DIR>/evidence` (`CPEBOT_EVIDENCE`)
- Telegram token file: `<INSTALL_DIR>/.env.telegram` (`CPEBOT_TG_ENV`, mode
  0600, gitignored) — see `docs/TELEGRAM-SETUP.md`

Keep `<DATA_DIR>` inside the checkout's `var/` (gitignored) or outside the
checkout entirely. It must never be committed.

**How it is deployed:** git checkout at `<INSTALL_DIR>`, run in place by the
user units. No build step, no virtualenv; Python 3 standard library (PyYAML
optional).

**Verify command:**
- web: `curl -fsS http://<BIND_ADDRESS>:<PORT>/health`
- web, deeper: `python3 tools/verify_live.py http://<BIND_ADDRESS>:<PORT>`
  (point it at a demo ledger; it creates entries)
- Telegram: `journalctl --user -u cpebot-tg -n 20` — look for the
  `telegram capture running` line (token shown masked to its last four).

**Upstream/downstream:** Upstream: none required (the optional LLM
validator is a disabled seam). Downstream: Telegram Bot API (outbound HTTPS
only); browsers on `<BIND_ADDRESS>`.

**Backup:** back up `<DATA_DIR>` (ledger + evidence) with the tool of your
choice. The ledger is SQLite in WAL mode; copy it with `sqlite3 .backup` or
stop the units first so `-wal`/`-shm` are folded in.

**Owner:** `<SERVICE_USER>` (user-scoped units; no root in the path).
