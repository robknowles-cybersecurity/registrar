"""Telegram capture.

The point of a chat capture path is that logging an activity has to be
cheaper than not logging it. Two seconds on a phone in the car park after a
chapter meeting, or the entry never gets made and the ledger is a fiction.

    8h SANS threat modelling workshop #training @domain_security

That is the whole interaction. What comes back is a message with Confirm and
Discard buttons, because nothing in this system auto-commits — a capture is
a claim, and a claim becomes a credit only when a human says so.

**This module ships without a token and will not start without one.** The
token is a bot credential; it is not generated here, not defaulted, and not
placed in the repository. `docs/TELEGRAM-SETUP.md` has the exact path,
format and permissions for bringing your own bot token. Until that file exists this
process exits 78 (EX_CONFIG) with an instruction and touches nothing.

    CPEBOT_TG_ENV    default <repo>/.env.telegram   (0600, gitignored)
    CPEBOT_TG_CHAT   numeric chat id allowed to capture; anything else is
                     ignored, so a leaked bot username cannot write here.
                     Unset, the bot refuses EVERY chat and warns at start-up.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import bodies as B
from . import ledger as L

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.environ.get("CPEBOT_TG_ENV", os.path.join(REPO, ".env.telegram"))
API = "https://api.telegram.org/bot%s/%s"
POLL_TIMEOUT = 50
EX_CONFIG = 78

HELP = (
    "Registrar capture\n\n"
    "Send an activity in one line:\n"
    "  8h SANS threat modelling workshop #training @domain_security\n\n"
    "Pieces, all optional except a title:\n"
    "  2026-08-14   a leading date; defaults to today\n"
    "  8h / 1.5h    hours\n"
    "  x3           count, for per-item activities\n"
    "  #tag         what kind of activity (see /tags)\n"
    "  @marker      what it was ABOUT — this is what drives category floors\n"
    "  +cissp       restrict to one credential; omit to let all eligible claim it\n"
    "  ~Provider    who ran it\n\n"
    "Nothing counts until you confirm it. /pending lists what is waiting."
)


class ConfigError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# token handling
# ---------------------------------------------------------------------------

def read_token(path=None):
    """Read the bot token from its env file. Never logs or returns it whole.

    Raises ConfigError with an actionable message if the file is absent,
    world-readable, or does not contain the key.
    """
    path = path or ENV_PATH
    if not os.path.isfile(path):
        raise ConfigError(
            "no token file at %s — Registrar does not create one. See "
            "docs/TELEGRAM-SETUP.md for the exact format, then chmod 600 it."
            % path)
    mode = os.stat(path).st_mode & 0o777
    if mode & 0o077:
        raise ConfigError(
            "%s is mode %o; a bot token must not be group- or world-readable. "
            "chmod 600 it and restart." % (path, mode))
    token = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "CPEBOT_TG_TOKEN":
                token = value.strip().strip('"').strip("'")
    if not token:
        raise ConfigError("%s has no CPEBOT_TG_TOKEN= line" % path)
    return token


def mask(token):
    """The only representation of a token this module will ever print."""
    return "…%s" % token[-4:] if token and len(token) > 4 else "…"


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def parse_message(text, rules, today=None):
    """Turn one line of chat into an entry payload, or raise ValueError."""
    import datetime
    import re

    today = today or datetime.date.today()
    words = (text or "").split()
    if not words:
        raise ValueError("empty message")

    date = str(today)
    hours, count = 0.0, 1
    tags, markers, applies, provider, title = [], [], [], [], []
    for w in words:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
            date = w
        elif re.fullmatch(r"\d+(\.\d+)?h", w, re.I):
            hours = float(w[:-1])
        elif re.fullmatch(r"[xX]\d+", w):
            count = int(w[1:])
        elif w.startswith("#") and len(w) > 1:
            tags.append(w[1:].lower())
        elif w.startswith("@") and len(w) > 1:
            markers.append(w[1:].lower())
        elif w.startswith("+") and len(w) > 1:
            applies.append(w[1:].lower())
        elif w.startswith("~") and len(w) > 1:
            provider.append(w[1:])
        else:
            title.append(w)

    if not title:
        raise ValueError("no title — say what the activity was")
    bad = [t for t in tags if t not in rules.tags]
    if bad:
        raise ValueError("unknown tag(s): %s. /tags lists them."
                         % ", ".join(bad))
    bad = [m for m in markers if m not in rules.markers]
    if bad:
        raise ValueError("unknown marker(s): %s. /markers lists them."
                         % ", ".join(bad))
    bad = [c for c in applies if c not in rules.ids()]
    if bad:
        raise ValueError("unknown credential(s): %s" % ", ".join(bad))
    if not tags:
        raise ValueError("no #tag — without one nothing can claim this entry. "
                         "/tags lists them.")
    return {
        "activity_date": date, "title": " ".join(title),
        "provider": " ".join(provider), "hours": hours, "count": count,
        "tags": tags, "markers": markers, "applies_to": applies or None,
    }


def preview(payload, rules, led=None):
    """What this capture would be worth, per credential, before confirming."""
    lines = ["%s — %s" % (payload["activity_date"], payload["title"])]
    if payload["provider"]:
        lines.append("provider: %s" % payload["provider"])
    bits = []
    if payload["hours"]:
        bits.append("%gh" % payload["hours"])
    if payload["count"] != 1:
        bits.append("x%d" % payload["count"])
    bits.append("#" + " #".join(payload["tags"]))
    if payload["markers"]:
        bits.append("@" + " @".join(payload["markers"]))
    lines.append(" ".join(bits))
    lines.append("")
    any_claim = False
    for cred in rules.credentials:
        if payload["applies_to"] and cred.id not in payload["applies_to"]:
            continue
        buckets = cred.buckets_for(payload["tags"], payload["markers"])
        if not buckets:
            continue
        any_claim = True
        credits = buckets[0].credits_for(payload["hours"], payload["count"])
        lines.append("  %s: %g %s (%s)" % (cred.name, credits,
                                           cred.unit_label, buckets[0].name))
    if not any_claim:
        lines.append("  nothing claims this — check the #tag.")
    lines.append("")
    lines.append("Captured, NOT counted. Confirm to make it count.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram transport
# ---------------------------------------------------------------------------

class Bot(object):

    def __init__(self, token, chat_id=None, rules=None, led=None):
        self._token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.rules = rules or B.load()
        self.led = led or L.Ledger()
        self.offset = None

    def call(self, method, **params):
        url = API % (self._token, method)
        data = urllib.parse.urlencode(
            dict((k, v if isinstance(v, str) else json.dumps(v))
                 for k, v in params.items() if v is not None)).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=70) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The URL contains the token. Never let it reach a log line.
            body = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError("telegram %s failed: HTTP %d %s"
                               % (method, exc.code, body))

    def send(self, chat_id, text, keyboard=None):
        return self.call("sendMessage", chat_id=str(chat_id), text=text,
                         reply_markup={"inline_keyboard": keyboard}
                         if keyboard else None)

    def allowed(self, chat_id):
        """Only the configured chat may write to the ledger. With no chat
        configured, no chat may."""
        return self.chat_id is not None and str(chat_id) == self.chat_id

    # -- handlers ---------------------------------------------------------

    def handle_update(self, update):
        if "callback_query" in update:
            return self._callback(update["callback_query"])
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat = str(msg.get("chat", {}).get("id"))
        text = (msg.get("text") or "").strip()
        if not self.allowed(chat):
            print("ignored message from unauthorised chat …%s" % chat[-4:],
                  flush=True)
            return
        if not text:
            return
        if text.startswith("/"):
            return self._command(chat, text)
        return self._capture(chat, text, msg)

    def _command(self, chat, text):
        cmd = text.split()[0].lower().lstrip("/").split("@")[0]
        if cmd in ("start", "help"):
            return self.send(chat, HELP)
        if cmd == "tags":
            return self.send(chat, "Tags:\n" + "\n".join(
                "  #%s — %s" % (t, self.rules.tag_labels.get(t, t))
                for t in sorted(self.rules.tags)))
        if cmd == "markers":
            return self.send(chat, "Markers (these drive category floors):\n"
                             + "\n".join(
                                 "  @%s — %s" % (m,
                                                 self.rules.marker_labels.get(m, m))
                                 for m in sorted(self.rules.markers)))
        if cmd == "pending":
            rows = self.led.list_entries(status=L.CAPTURED, limit=20)
            if not rows:
                return self.send(chat, "Nothing pending. Everything captured "
                                       "has been confirmed or rejected.")
            return self.send(chat, "Awaiting confirmation:\n" + "\n".join(
                "  %s %s (%gh)" % (r["activity_date"], r["title"], r["hours"])
                for r in rows))
        if cmd == "status":
            cc = self.led.capture_counts()
            return self.send(chat, "%d real captures in %d days (threshold %d): "
                                   "%s" % (cc["real"], cc["window_days"],
                                           cc["threshold"],
                                           "below" if cc["falsified"] else "above"))
        return self.send(chat, "Unknown command. /help")

    def _capture(self, chat, text, msg):
        try:
            payload = parse_message(text, self.rules)
        except ValueError as exc:
            return self.send(chat, "Could not read that: %s\n\n%s"
                             % (exc, HELP))
        try:
            uuid = self.led.add_entry(
                capture_source="telegram",
                capture_ref="tg:%s" % msg.get("message_id"), **payload)
        except L.LedgerError as exc:
            return self.send(chat, "Rejected: %s" % exc)
        dupes = self.led.soft_duplicates(uuid)
        body = preview(payload, self.rules, self.led)
        if dupes:
            body += ("\n\n⚠ Possible duplicate of %d existing entr%s on the "
                     "same date and provider. Check before confirming."
                     % (len(dupes), "y" if len(dupes) == 1 else "ies"))
        return self.send(chat, body, keyboard=[[
            {"text": "Confirm", "callback_data": "confirm:%s" % uuid},
            {"text": "Discard", "callback_data": "reject:%s" % uuid},
        ]])

    def _callback(self, cq):
        chat = str(cq.get("message", {}).get("chat", {}).get("id"))
        data = cq.get("data") or ""
        if not self.allowed(chat):
            return
        action, _, uuid = data.partition(":")
        try:
            if action == "confirm":
                try:
                    self.led.confirm(uuid)
                    note = "Confirmed. It counts now."
                except L.SoftDuplicate as dup:
                    self.led.confirm(uuid, force=True)
                    note = ("Confirmed over %d possible duplicate(s) — review "
                            "them on the web app if that was wrong."
                            % len(dup.matches))
            elif action == "reject":
                self.led.set_status(uuid, L.REJECTED)
                note = "Discarded. Nothing was counted."
            else:
                note = "Unknown action."
        except L.LedgerError as exc:
            note = "Could not do that: %s" % exc
        self.call("answerCallbackQuery", callback_query_id=cq["id"], text=note)
        return self.send(chat, note)

    # -- loop -------------------------------------------------------------

    def poll_once(self):
        result = self.call("getUpdates", offset=self.offset,
                           timeout=str(POLL_TIMEOUT))
        updates = result.get("result", [])
        for u in updates:
            self.offset = u["update_id"] + 1
            try:
                self.handle_update(u)
            except Exception as exc:            # one bad update must not stop
                print("update %s failed: %s" % (u.get("update_id"), exc),
                      flush=True)
        return len(updates)

    def announce(self):
        print("Registrar telegram capture running (token %s, chat %s)"
              % (mask(self._token),
                 ("…%s" % self.chat_id[-4:]) if self.chat_id else "NONE"),
              flush=True)
        if self.chat_id is None:
            print("WARNING: CPEBOT_TG_CHAT is not set, so every chat is "
                  "refused and nothing will be captured. Set it to your "
                  "numeric chat id (docs/TELEGRAM-SETUP.md, step 3).",
                  flush=True)

    def run(self):
        self.announce()
        backoff = 1
        while True:
            try:
                self.poll_once()
                backoff = 1
            except KeyboardInterrupt:
                return
            except Exception as exc:
                print("poll failed (%s); retrying in %ds" % (exc, backoff),
                      flush=True)
                time.sleep(backoff)
                backoff = min(60, backoff * 2)


def main():
    try:
        token = read_token()
    except ConfigError as exc:
        sys.stderr.write("registrar-tg: %s\n" % exc)
        return EX_CONFIG
    bot = Bot(token, os.environ.get("CPEBOT_TG_CHAT"))
    bot.led.init_schema()
    bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
