import unittest

from laser.config import Config
from laser.engine import Block, Ended, Engine, Notify, site_label
from laser.jev import Verdict
from laser.privacy import clean_ocr, scrub
from laser.sense import Window

EDITOR = Window("0x1", "code", "thesis.tex - Visual Studio Code", 1, 0, 0, 100, 100)
YOUTUBE = Window("0x2", "chromium", "Cat compilation - YouTube - Chromium", 2, 0, 0, 100, 100)
TERMINAL = Window("0x3", "Alacritty", "nvim", 3, 0, 0, 100, 100)
ON = Verdict(0.95, "work", 0.9, 300, 400)
OFF = Verdict(0.03, "entertainment", 0.9, 300, 400)


def run(engine, seconds, window, verdict, start=0.0, step=2.0):
    out, t = [], start
    while t < start + seconds:
        t += step
        out += engine.tick(t, step, window, verdict, away=False)
    return out, t


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(Config())
        self.engine.start("write thesis", now=0.0)

    def test_focus_keeps_level_zero(self):
        acts, _ = run(self.engine, 300, EDITOR, ON)
        self.assertEqual(acts, [])
        self.assertEqual(self.engine.session.level, 0)

    def test_escalation_ladder(self):
        acts, _ = run(self.engine, 27, YOUTUBE, OFF)
        self.assertEqual(self.engine.session.level, 0)
        acts, t = run(self.engine, 34, YOUTUBE, OFF, start=28)
        self.assertEqual(self.engine.session.level, 2)
        self.assertTrue(any(isinstance(a, Notify) and a.offer_allow for a in acts))
        acts, t = run(self.engine, 60, YOUTUBE, OFF, start=t)
        self.assertTrue(self.engine.snapshot(t)["tint"])
        acts, t = run(self.engine, 130, YOUTUBE, OFF, start=t)
        blocks = [a for a in acts if isinstance(a, Block)]
        self.assertEqual(len(blocks), 1)
        self.assertTrue(blocks[0].browser)
        self.assertEqual(self.engine.session.level, 3)  # stays hot after a block

    def test_fog_ramps_on_the_distraction_and_clears_on_switch(self):
        _, t = run(self.engine, 176, YOUTUBE, OFF)
        self.assertIsNone(self.engine.snapshot(t)["fog"])
        _, t = run(self.engine, 4, YOUTUBE, OFF, start=t)
        fog = self.engine.snapshot(t)["fog"]
        self.assertEqual(fog["address"], YOUTUBE.address)
        self.assertLess(fog["amount"], 0.5)
        _, t = run(self.engine, 40, YOUTUBE, OFF, start=t)
        self.assertEqual(self.engine.snapshot(t)["fog"]["amount"], 1.0)
        self.engine.tick(t + 2, 2, EDITOR, ON, away=False)
        self.assertIsNone(self.engine.snapshot(t + 2)["fog"])

    def test_fog_only_never_closes(self):
        self.engine.config.final_step = "fog"
        self.engine.config.apply_strictness()
        acts, t = run(self.engine, 600, YOUTUBE, OFF)
        self.assertFalse(any(isinstance(a, Block) for a in acts))
        self.assertEqual(self.engine.snapshot(t)["fog"]["amount"], 1.0)

    def test_brief_glance_back_does_not_reset(self):
        _, t = run(self.engine, 100, YOUTUBE, OFF)
        _, t = run(self.engine, 4, EDITOR, ON, start=t)
        self.assertGreaterEqual(self.engine.session.bucket, 90)
        _, t = run(self.engine, 60, EDITOR, ON, start=t)
        self.assertEqual(self.engine.session.bucket, 0)

    def test_skipped_block_is_retried_on_next_distraction(self):
        acts, t = run(self.engine, 250, YOUTUBE, OFF)
        self.assertEqual(sum(isinstance(a, Block) for a in acts), 1)
        self.engine.block_skipped()
        self.assertEqual(self.engine.session.blocks, 0)
        acts, _ = run(self.engine, 2, YOUTUBE, OFF, start=t)
        self.assertEqual(sum(isinstance(a, Block) for a in acts), 1)

    def test_never_close_protected_apps(self):
        acts, _ = run(self.engine, 400, TERMINAL, OFF)
        self.assertFalse(any(isinstance(a, Block) for a in acts))

    def test_allow_forgives_and_sticks(self):
        _, t = run(self.engine, 100, YOUTUBE, OFF)
        self.assertEqual(self.engine.allow(YOUTUBE), "YouTube")
        other_video = Window("0x9", "chromium", "Lecture 4 - YouTube - Chromium", 2, 0, 0, 1, 1)
        acts, _ = run(self.engine, 300, other_video, OFF, start=t)
        self.assertEqual(acts, [])
        self.assertEqual(self.engine.session.status, "focused")

    def test_allow_matches_with_or_without_resolved_url(self):
        resolved = Window("0x2", "chromium", "Cat compilation - YouTube - Chromium", 2, 0, 0, 1, 1,
                          url="https://www.youtube.com/watch")
        self.engine.allow(resolved)
        unresolved = Window("0x9", "chromium", "Speedrun - YouTube - Chromium", 2, 0, 0, 1, 1)
        self.assertTrue(self.engine.is_allowed(unresolved))
        other_site = Window("0x9", "chromium", "Home / X - Chromium", 2, 0, 0, 1, 1, url="https://x.com/home")
        self.assertFalse(self.engine.is_allowed(other_site))

    def test_unsure_and_away_do_not_accumulate(self):
        run(self.engine, 300, YOUTUBE, Verdict(0.45, "research", 0.5, 1, 1))
        self.assertEqual(self.engine.session.bucket, 0)
        self.engine.tick(400, 2, YOUTUBE, OFF, away=True)
        self.assertEqual(self.engine.session.status, "away")
        self.assertEqual(self.engine.session.bucket, 0)

    def test_pending_verdict_keeps_previous_status(self):
        run(self.engine, 10, YOUTUBE, OFF)
        self.engine.tick(12, 2, YOUTUBE, None, away=False)
        self.assertEqual(self.engine.session.status, "distracted")

    def test_switching_windows_is_not_billed_before_a_verdict(self):
        _, t = run(self.engine, 10, YOUTUBE, OFF)
        self.engine.tick(t + 2, 2, TERMINAL, None, away=False)
        self.assertEqual(self.engine.session.status, "checking")
        self.assertNotIn("Alacritty", self.engine.session.distractions)

    def test_break(self):
        self.engine.pause(0, 1)
        acts, _ = run(self.engine, 58, YOUTUBE, OFF)
        self.assertEqual(acts, [])
        acts, _ = run(self.engine, 4, YOUTUBE, OFF, start=58)
        self.assertTrue(any(isinstance(a, Notify) and "over" in a.title for a in acts))

    def test_timer_ends_session(self):
        self.engine.start("write thesis", now=0.0, minutes=1)
        acts, _ = run(self.engine, 62, EDITOR, ON)
        ended = [a for a in acts if isinstance(a, Ended)]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0].summary["ended_by"], "timer")
        self.assertIsNone(self.engine.session)


class HelpersTest(unittest.TestCase):
    def test_site_label(self):
        browsers = Config().rules.browsers
        self.assertEqual(site_label(YOUTUBE, browsers), "YouTube")
        self.assertEqual(site_label(Window("a", "google-chrome", "WhatsApp - Google Chrome", 0, 0, 0, 0, 0), browsers), "WhatsApp")
        self.assertEqual(site_label(EDITOR, browsers), "code")
        memes = Window("a", "google-chrome", "/r/Memes the original since 2008 - Google Chrome", 0, 0, 0, 0, 0,
                       url="https://www.reddit.com/r/memes/")
        self.assertEqual(site_label(memes, browsers), "reddit.com")

    def test_webapp_class(self):
        from laser.sense import webapp_url
        self.assertEqual(webapp_url("chrome-web.whatsapp.com__-Default"), "https://web.whatsapp.com/")
        self.assertEqual(webapp_url("chrome-discord.com__channels_@me-Default"), "https://discord.com/")
        self.assertEqual(webapp_url("google-chrome"), "")

    def test_profile_from_cmdline(self):
        from laser import sense
        chrome = "/opt/google/chrome/chrome --ozone-platform=wayland --user-data-dir=/tmp/p q --no-first-run"
        self.assertEqual(sense._PROFILE_ARG["chromium"].search(chrome).group(1), "/tmp/p q")
        nul = "chromium\x00--user-data-dir=/tmp/x\x00--app=https://a.b"
        self.assertEqual(sense._PROFILE_ARG["chromium"].search(nul).group(1), "/tmp/x")
        ff = "firefox\x00-profile\x00/home/u/ff\x00--new-window"
        self.assertEqual(sense._PROFILE_ARG["firefox"].search(ff).group(1), "/home/u/ff")

    def test_loading_titles(self):
        from laser.sense import is_loading
        tab = lambda t: Window("a", "google-chrome", f"{t} - Google Chrome", 0, 0, 0, 0, 0)
        self.assertTrue(is_loading(tab("Untitled")))
        self.assertTrue(is_loading(tab("youtube.com/results?search_query=funny+cats")))
        self.assertFalse(is_loading(tab("funny cats - YouTube")))
        self.assertFalse(is_loading(tab("Hacker News")))
        self.assertFalse(is_loading(Window("a", "code", "Untitled", 0, 0, 0, 0, 0)))

    def test_scrub(self):
        text = scrub("mail me at a.b@example.com, card 4111 1111 1111 1111, key tok_fake_abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("example.com", text)
        self.assertNotIn("4111", text)
        self.assertNotIn("tok_fake", text)

    def test_clean_ocr_drops_noise(self):
        self.assertEqual(clean_ocr("‘\\ @ ..\nThe encoder consists of\n%9 ’ }"), "The encoder consists of")


if __name__ == "__main__":
    unittest.main()
