# Telegram capture — bring your own bot token

The Telegram capture path is complete and tested, and it ships with no token.
That is deliberate: a bot token is a credential, and the only person who
should create, hold or rotate it is the person running the bot.

Until the token file below exists, `cpebot-tg.service` exits **78
(EX_CONFIG)** with a one-line instruction and writes nothing.

Placeholders used below: `<INSTALL_DIR>` is your checkout, `<CHAT_ID>` is your
numeric Telegram chat id, `<YOUR_BOT_TOKEN>` is the value BotFather gives you.

---

## 1. Create a bot

In Telegram, message [@BotFather](https://t.me/BotFather) and send `/newbot`.
Name it whatever you like. BotFather replies with the bot's token. **That
reply is the only place the token should exist in full — do not paste it into
a chat with anyone, a ticket, an issue, a log, or this repository.**

Optionally, `/setcommands` and paste:

```
help - how to log an activity
tags - the tag vocabulary
markers - topic markers (these drive category floors)
pending - what is waiting for confirmation
status - real-capture count vs the kill-switch threshold
```

## 2. Store the token

Exact key name, exact mode. Type the token into the file with an editor, or
with a heredoc as below — but never pass it as a command-line argument, where
it lands in shell history and the process table.

```bash
umask 077
cat > <INSTALL_DIR>/.env.telegram <<'EOF'
CPEBOT_TG_TOKEN=<YOUR_BOT_TOKEN>
EOF
chmod 600 <INSTALL_DIR>/.env.telegram
```

Format rules, all enforced at start-up:

| Rule | What happens if it is wrong |
|---|---|
| File at `<INSTALL_DIR>/.env.telegram` (override with `CPEBOT_TG_ENV`) | exit 78, names the path |
| Key is exactly `CPEBOT_TG_TOKEN` | exit 78, names the key |
| Mode `0600` — no group or other bits | exit 78, refuses to read it |
| Quotes optional; `#` comments and blank lines ignored | — |

`.gitignore` already excludes `.env`, `.env.*` and anything whose name
contains "token" in any letter case, so the file cannot be committed by
accident. Check it:

```bash
git -C <INSTALL_DIR> check-ignore -v .env.telegram
```

## 3. Lock it to your chat

Until a chat id is set, the bot refuses every chat, so nobody (you
included) can write to your compliance record through it, and it logs a
warning saying so when it starts. Find your numeric chat id with a helper
bot such as [@userinfobot](https://t.me/userinfobot); the bot itself logs
only the last four digits of any chat it ignores.

Set it in the unit:

```
Environment=CPEBOT_TG_CHAT=<CHAT_ID>
```

## 4. Install and start

Fill in the placeholders in `systemd/cpebot-tg.service` first, then:

```bash
install -m 0644 <INSTALL_DIR>/systemd/cpebot-tg.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cpebot-tg.service
systemctl --user status cpebot-tg.service --no-pager
```

A healthy first log line reads `Registrar telegram capture running (…)`, with the
token and chat id each shown only as their last four characters. That masked
form is the only form this program will ever print.

## 5. Prove it works

Send the bot:

```
2h Test capture #training @domain_security
```

You should get a preview ending **"Captured, NOT counted"** with Confirm and
Discard buttons. Press Confirm, then check the web app — the entry appears in
the dashboard totals. Press Discard on a second one and confirm it does *not*.

Then **void** both test entries from the web app (open each entry, "Void this
entry") so they can never credit anything. Nothing in this ledger is deleted —
a voided entry keeps its capture history, is excluded from totals, duplicate
checks and reports forever, and cannot transition back.

## If the token leaks

`/revoke` in BotFather issues a new one and invalidates the old immediately.
Store the new one as in step 2 and restart the unit. The token is the only
secret in this system.
