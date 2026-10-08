import tempfile
import unittest
from pathlib import Path
from unittest import mock

from laser import look

THEME = {
    ("getoption", "general:col.active_border"): "gradient data: ff81a1c1 0deg\nset: true",
    ("getoption", "decoration:dim_inactive"): "bool: false\nset: false",
    ("getoption", "decoration:dim_strength"): "float: 0.500000\nset: false",
}


class LookTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        mock.patch.object(look, "SAVED_FILE", Path(tmp.name) / "look.json").start()
        mock.patch.object(look, "_hyprctl", side_effect=self.fake).start()
        self.addCleanup(mock.patch.stopall)
        self.look = look.Look()

    def fake(self, *args):
        self.calls.append(args)
        return THEME.get(args, "ok")

    def evals(self):
        return [a[1] for a in self.calls if a[0] == "eval"]

    def test_focus_applies_once_and_restores_theme(self):
        for _ in range(5):
            self.look.update("full", "focused")
        self.assertEqual(len(self.evals()), 1)
        self.assertIn("rgba(39ff88ee)", self.evals()[0])
        self.assertIn("dim_inactive = true", self.evals()[0])
        self.assertTrue(look.SAVED_FILE.exists())
        self.look.update("full", None)  # session over
        self.assertIn('"rgba(81a1c1ff)"', self.evals()[-1])
        self.assertIn("dim_inactive = false", self.evals()[-1])
        self.assertIn("dim_strength = 0.50", self.evals()[-1])
        self.assertFalse(look.SAVED_FILE.exists())

    def test_drift_turns_border_red(self):
        self.look.update("full", "focused")
        self.look.update("full", "distracted")
        self.assertIn("rgba(ff3b3bee)", self.evals()[-1])

    def test_subtle_keeps_the_users_dimming(self):
        self.look.update("subtle", "focused")
        self.assertIn("dim_inactive = false", self.evals()[0])

    def test_off_and_breaks_change_nothing(self):
        self.look.update("off", "focused")
        self.look.update("full", "break")
        self.assertEqual(self.evals(), [])

    def test_recovers_originals_after_a_crash(self):
        self.look.update("full", "focused")
        fresh = look.Look()  # a restarted daemon finds the saved originals
        fresh.update("full", None)
        self.assertIn('"rgba(81a1c1ff)"', self.evals()[-1])

    def test_only_hex_colours_reach_lua(self):
        with self.assertRaises(ValueError):
            look._lua_gradient(['rgba(00ff00ff)" }) os.execute("x'], 0)


if __name__ == "__main__":
    unittest.main()
