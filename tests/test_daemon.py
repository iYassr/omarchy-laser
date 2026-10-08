import unittest
from unittest import mock

from laser.config import Config
from laser.daemon import Daemon
from laser.jev import Verdict
from laser.sense import Window

OFF = Verdict(0.03, "communication", 0.9, 300, 400)


class FakeJev:
    ipv4_only = False
    api_key = "test"

    def __init__(self):
        self.calls = 0
        self.states = []

    def classify(self, state):
        self.calls += 1
        self.states.append(state)
        return OFF

    def close(self):
        pass


def chat(unread: int) -> Window:
    return Window("0xchat", "chrome-web.whatsapp.com__-Default", f"({unread}) WhatsApp", 1, 0, 0, 10, 10,
                  url="https://web.whatsapp.com/")


class VerdictSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.daemon = Daemon(Config())
        self.daemon.jev = self.fake = FakeJev()
        self.daemon.key_checked = 1e12  # never rebuild the fake
        self.daemon.engine.start("write thesis", now=0)
        self.screen = mock.patch("laser.sense.screen_text", return_value="the encoder stack").start()
        mock.patch("laser.daemon.Daemon._record_usage").start()  # never write to the real data dir
        self.addCleanup(mock.patch.stopall)

    def test_churning_title_is_still_judged(self):
        verdicts = [self.daemon._verdict("write thesis", chat(n), now=2.0 * n) for n in range(1, 16)]
        self.assertIsNone(verdicts[0])  # first sight of the window: debounce
        self.assertTrue(all(v is OFF for v in verdicts[1:]), verdicts)
        self.assertLessEqual(self.fake.calls, 6)  # ~every 6s, not every tick

    def test_alt_tab_past_a_window_is_free(self):
        self.daemon._verdict("write thesis", chat(1), now=0)
        self.assertEqual(self.fake.calls, 0)

    def test_unchanged_window_uses_cache_until_stale(self):
        for t in range(0, 60, 2):
            self.daemon._verdict("write thesis", chat(1), now=t)
        self.assertEqual(self.fake.calls, 1)  # a confident verdict holds for a minute
        self.daemon._verdict("write thesis", chat(1), now=63)
        self.assertEqual(self.fake.calls, 2)

    def test_sensitive_apps_are_never_captured(self):
        self.daemon._verdict("write thesis", chat(1), now=0)
        self.daemon._verdict("write thesis", chat(1), now=2)
        self.assertEqual(self.fake.states[-1], {"declared_task": "write thesis", "site": "web.whatsapp.com"})
        self.screen.assert_not_called()

    def test_strict_never_captures_the_screen(self):
        self.daemon.config.privacy = "strict"
        doc = Window("0xdoc", "code", "thesis.tex - Visual Studio Code", 1, 0, 0, 10, 10)
        self.daemon._verdict("write thesis", doc, now=0)
        self.daemon._verdict("write thesis", doc, now=2)
        self.assertEqual(self.fake.states[-1], {"declared_task": "write thesis", "site": "code"})
        self.screen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
