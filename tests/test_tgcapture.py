"""Telegram capture.

Two things are being proved here. The first is that the parser turns a line
typed one-handed on a phone into a correct entry, or refuses it with a
sentence a human can act on. The second, and the reason this file is longer
than the parser deserves, is that the capture path obeys the same
confirm-semantics as every other path: a message creates a *claim*, and the
claim is worth zero credits until a button is pressed.

No test here touches the network. `FakeBot` replaces the single method that
would, which also means every assertion about what the bot "said" is an
assertion about the exact bytes that would have gone to Telegram.
"""

import contextlib
import datetime
import io
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B           # noqa: E402
from cpebot import ledger as L           # noqa: E402
from cpebot import tgcapture as TG       # noqa: E402

CHAT = "1234567890"


class FakeBot(TG.Bot):
    """A Bot with its one network method replaced by a recorder."""

    def __init__(self, *a, **kw):
        TG.Bot.__init__(self, *a, **kw)
        self.sent = []

    def call(self, method, **params):
        self.sent.append((method, params))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    def texts(self):
        return [p.get("text", "") for m, p in self.sent if m == "sendMessage"]

    def last(self):
        return self.texts()[-1] if self.texts() else ""

    def buttons(self):
        for method, p in reversed(self.sent):
            if method == "sendMessage" and p.get("reply_markup"):
                return p["reply_markup"]["inline_keyboard"]
        return []


def message(text, chat=CHAT, mid=1):
    return {"update_id": mid, "message": {"message_id": mid, "text": text,
                                          "chat": {"id": chat}}}


def callback(data, chat=CHAT, cid="cb1"):
    return {"update_id": 99, "callback_query": {
        "id": cid, "data": data, "message": {"chat": {"id": chat}}}}


class CaptureCase(unittest.TestCase):

    def setUp(self):
        self.rules = B.load()
        self.dir = tempfile.mkdtemp(prefix="cpebot-tg-")
        self.led = L.Ledger(os.path.join(self.dir, "t.db"),
                            os.path.join(self.dir, "evidence"))
        self.led.init_schema()
        self.bot = FakeBot("111:NOT-A-REAL-TOKEN", CHAT, self.rules, self.led)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def entries(self, status=None):
        return self.led.list_entries(status=status)


# ---------------------------------------------------------------------------
# the token this module does not have
# ---------------------------------------------------------------------------

class TestItRefusesToInventAToken(CaptureCase):

    def test_missing_env_file_is_a_clean_refusal_not_a_default(self):
        path = os.path.join(self.dir, "nope.env")
        with self.assertRaises(TG.ConfigError) as ctx:
            TG.read_token(path)
        msg = str(ctx.exception)
        self.assertIn(path, msg)
        self.assertIn("TELEGRAM-SETUP", msg)

    def test_main_exits_ex_config_rather_than_starting_tokenless(self):
        os.environ["CPEBOT_TG_ENV"] = os.path.join(self.dir, "absent.env")
        try:
            reload_path = TG.ENV_PATH
            TG.ENV_PATH = os.environ["CPEBOT_TG_ENV"]
            self.assertEqual(TG.main(), 78)
        finally:
            TG.ENV_PATH = reload_path
            os.environ.pop("CPEBOT_TG_ENV", None)

    def test_a_readable_by_others_token_file_is_refused(self):
        path = os.path.join(self.dir, "loose.env")
        with open(path, "w") as fh:
            fh.write("CPEBOT_TG_TOKEN=111:aaaa\n")
        os.chmod(path, 0o644)
        with self.assertRaises(TG.ConfigError) as ctx:
            TG.read_token(path)
        self.assertIn("must not be group- or world-readable",
                      str(ctx.exception))

    def test_it_reads_a_correctly_stashed_token(self):
        path = os.path.join(self.dir, "ok.env")
        with open(path, "w") as fh:
            fh.write("# comment\n\nCPEBOT_TG_TOKEN=\"111:abcdWXYZ\"\n")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        self.assertEqual(TG.read_token(path), "111:abcdWXYZ")

    def test_an_env_file_without_the_key_names_the_key(self):
        path = os.path.join(self.dir, "wrong.env")
        with open(path, "w") as fh:
            fh.write("TELEGRAM_TOKEN=111:abcd\n")
        os.chmod(path, 0o600)
        with self.assertRaises(TG.ConfigError) as ctx:
            TG.read_token(path)
        self.assertIn("CPEBOT_TG_TOKEN", str(ctx.exception))

    def test_mask_shows_four_characters_and_no_more(self):
        self.assertEqual(TG.mask("111:abcdefghWXYZ"), "…WXYZ")
        self.assertNotIn("abcdefgh", TG.mask("111:abcdefghWXYZ"))


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

class TestParsing(CaptureCase):

    def parse(self, text, today=datetime.date(2026, 8, 30)):
        return TG.parse_message(text, self.rules, today)

    def test_the_line_from_the_help_text_parses(self):
        p = self.parse("8h SANS threat modelling workshop #training "
                       "@domain_security")
        self.assertEqual(p["hours"], 8.0)
        self.assertEqual(p["title"], "SANS threat modelling workshop")
        self.assertEqual(p["tags"], ["training"])
        self.assertEqual(p["markers"], ["domain_security"])
        self.assertEqual(p["activity_date"], "2026-08-30")

    def test_a_leading_date_overrides_today(self):
        p = self.parse("2026-08-14 2h Chapter meeting #chapter_meeting")
        self.assertEqual(p["activity_date"], "2026-08-14")

    def test_count_and_provider_and_credential_restriction(self):
        p = self.parse("x3 Read three chapters #reading ~OReilly +cissp")
        self.assertEqual(p["count"], 3)
        self.assertEqual(p["provider"], "OReilly")
        self.assertEqual(p["applies_to"], ["cissp"])
        self.assertEqual(p["title"], "Read three chapters")

    def test_fractional_hours(self):
        self.assertEqual(self.parse("1.5h Webinar #training")["hours"], 1.5)

    def test_no_tag_is_refused_because_nothing_could_claim_it(self):
        with self.assertRaises(ValueError) as ctx:
            self.parse("2h Some talk")
        self.assertIn("#tag", str(ctx.exception))

    def test_no_title_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.parse("2h #training")
        self.assertIn("title", str(ctx.exception))

    def test_an_unknown_tag_names_itself_and_points_at_help(self):
        with self.assertRaises(ValueError) as ctx:
            self.parse("2h Talk #sparkles")
        self.assertIn("sparkles", str(ctx.exception))
        self.assertIn("/tags", str(ctx.exception))

    def test_an_unknown_marker_is_refused_rather_than_silently_dropped(self):
        # Silently dropping a marker would silently drop a floor contribution.
        with self.assertRaises(ValueError) as ctx:
            self.parse("2h Talk #training @not_a_domain")
        self.assertIn("not_a_domain", str(ctx.exception))


# ---------------------------------------------------------------------------
# capture semantics
# ---------------------------------------------------------------------------

class TestNothingAutoCommits(CaptureCase):

    def capture(self, text="8h SANS threat modelling #training "
                           "@domain_security"):
        self.bot.handle_update(message(text))
        rows = self.entries()
        return rows[0]["uuid"]

    def test_a_message_lands_as_captured_not_confirmed(self):
        u = self.capture()
        self.assertEqual(self.led.get_entry(u)["status"], L.CAPTURED)
        self.assertEqual(self.led.get_entry(u)["capture_source"], "telegram")

    def test_the_reply_says_so_out_loud(self):
        self.capture()
        self.assertIn("Captured, NOT counted", self.bot.last())

    def test_the_reply_previews_what_it_would_be_worth(self):
        self.capture()
        self.assertIn("CISSP", self.bot.last())
        self.assertIn("CPE", self.bot.last())

    def test_confirm_button_moves_it_to_confirmed(self):
        u = self.capture()
        self.bot.handle_update(callback("confirm:%s" % u))
        self.assertEqual(self.led.get_entry(u)["status"], L.CONFIRMED)
        self.assertIn("It counts now", self.bot.last())

    def test_discard_button_rejects_it(self):
        u = self.capture()
        self.bot.handle_update(callback("reject:%s" % u))
        self.assertEqual(self.led.get_entry(u)["status"], L.REJECTED)
        self.assertIn("Nothing was counted", self.bot.last())

    def test_the_buttons_carry_the_entry_uuid(self):
        u = self.capture()
        data = [b["callback_data"] for row in self.bot.buttons() for b in row]
        self.assertEqual(sorted(data), ["confirm:%s" % u, "reject:%s" % u])

    def test_a_capture_counts_toward_the_kill_switch_number(self):
        before = self.led.capture_counts()["real"]
        self.capture()
        self.assertEqual(self.led.capture_counts()["real"], before + 1)

    def test_a_soft_duplicate_is_flagged_at_capture_not_blocked(self):
        first = self.led.add_entry(activity_date="2026-08-14", title="Day one",
                                   hours=8, provider="SANS", tags=["training"])
        self.led.set_status(first, L.CONFIRMED)
        self.bot.handle_update(message(
            "2026-08-14 8h Day one again #training ~SANS"))
        self.assertIn("Possible duplicate", self.bot.last())
        self.assertEqual(len(self.entries()), 2)     # flagged, still written

    def test_confirming_over_a_soft_duplicate_says_what_it_did(self):
        first = self.led.add_entry(activity_date="2026-08-14", title="Day one",
                                   hours=8, provider="SANS", tags=["training"])
        self.led.set_status(first, L.CONFIRMED)
        self.bot.handle_update(message(
            "2026-08-14 8h Day one again #training ~SANS"))
        u = self.entries(status=L.CAPTURED)[0]["uuid"]
        self.bot.handle_update(callback("confirm:%s" % u))
        self.assertEqual(self.led.get_entry(u)["status"], L.CONFIRMED)
        self.assertIn("duplicate", self.bot.last())

    def test_a_bad_line_writes_nothing(self):
        self.bot.handle_update(message("2h Some talk with no tag"))
        self.assertEqual(self.entries(), [])
        self.assertIn("Could not read that", self.bot.last())


class TestTheChatIdIsTheBoundary(CaptureCase):

    def test_a_stranger_cannot_write_to_the_ledger(self):
        self.bot.handle_update(message("8h Free credits #training",
                                       chat="9999"))
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.bot.sent, [])          # not even a reply

    def test_a_stranger_cannot_confirm_someone_elses_entry(self):
        u = self.led.add_entry(activity_date="2026-08-14", title="Mine",
                               hours=1, tags=["training"])
        self.bot.handle_update(callback("confirm:%s" % u, chat="9999"))
        self.assertEqual(self.led.get_entry(u)["status"], L.CAPTURED)

    def test_without_a_configured_chat_id_every_chat_is_refused(self):
        bot = FakeBot("111:x", None, self.rules, self.led)
        for chat in ("9999", CHAT, "None", ""):
            self.assertFalse(bot.allowed(chat), chat)
        bot.handle_update(message("8h Free credits #training", chat="9999"))
        u = self.led.add_entry(activity_date="2026-08-14", title="Mine",
                               hours=1, tags=["training"])
        bot.handle_update(callback("confirm:%s" % u, chat="9999"))
        self.assertEqual([e["uuid"] for e in self.entries()], [u])
        self.assertEqual(self.led.get_entry(u)["status"], L.CAPTURED)
        self.assertEqual(bot.sent, [])

    def test_without_a_configured_chat_id_it_warns_once_at_startup(self):
        bot = FakeBot("111:x", None, self.rules, self.led)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bot.announce()
        lines = out.getvalue().splitlines()
        warnings = [l for l in lines if l.startswith("WARNING")]
        self.assertEqual(len(warnings), 1, lines)
        self.assertIn("CPEBOT_TG_CHAT is not set", warnings[0])
        self.assertIn("every chat is refused", warnings[0])

    def test_with_a_configured_chat_id_only_that_chat_is_allowed(self):
        self.assertTrue(self.bot.allowed(CHAT))
        self.assertTrue(self.bot.allowed(int(CHAT)))
        self.assertFalse(self.bot.allowed("9999"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.bot.announce()
        self.assertNotIn("WARNING", out.getvalue())
        self.assertIn("chat …%s" % CHAT[-4:], out.getvalue())
        self.bot.handle_update(message("8h Real credits #training"))
        self.assertEqual(len(self.entries()), 1)


class TestCommands(CaptureCase):

    def test_help_documents_the_grammar_it_actually_parses(self):
        self.bot.handle_update(message("/help"))
        text = self.bot.last()
        for token in ("#tag", "@marker", "+cissp", "~Provider", "x3"):
            self.assertIn(token, text)

    def test_tags_lists_the_real_vocabulary(self):
        self.bot.handle_update(message("/tags"))
        for t in self.rules.tags:
            self.assertIn("#%s" % t, self.bot.last())

    def test_markers_explains_why_markers_matter(self):
        self.bot.handle_update(message("/markers"))
        self.assertIn("floors", self.bot.last())

    def test_pending_lists_only_unconfirmed_entries(self):
        self.bot.handle_update(message("8h Waiting #training"))
        done = self.led.add_entry(activity_date="2026-08-01", title="Settled",
                                  hours=2, tags=["training"])
        self.led.set_status(done, L.CONFIRMED)
        self.bot.handle_update(message("/pending"))
        self.assertIn("Waiting", self.bot.last())
        self.assertNotIn("Settled", self.bot.last())

    def test_pending_is_honest_about_an_empty_queue(self):
        self.bot.handle_update(message("/pending"))
        self.assertIn("Nothing pending", self.bot.last())

    def test_status_reports_the_falsifier(self):
        self.bot.handle_update(message("/status"))
        self.assertIn("threshold", self.bot.last())

    def test_an_unknown_command_is_not_parsed_as_an_activity(self):
        self.bot.handle_update(message("/frobnicate"))
        self.assertEqual(self.entries(), [])
        self.assertIn("Unknown command", self.bot.last())


class TestTheLoopSurvivesOneBadUpdate(CaptureCase):

    def test_a_handler_exception_does_not_stop_polling(self):
        calls = {"n": 0}

        def boom(update):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("kaboom")

        self.bot.handle_update = boom
        self.bot.call = lambda m, **k: {"result": [{"update_id": 1},
                                                   {"update_id": 2}]}
        self.assertEqual(self.bot.poll_once(), 2)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self.bot.offset, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
