import unittest

from laser import privacy

SCREEN = """John Smith <john.smith@example.com> wrote:
Re: Salary review for Q3 — call me on +44 7700 900123 or see https://intranet.acme.com/hr?id=5521
@yasserdo shared IBAN GB29NWBK60161331926819 and token tok_fake_51HxYzAbCdEfGhIjKlMnOpQr
the encoder stack applies attention to every token in the sequence and the attention weights"""


class PrivacyTest(unittest.TestCase):
    def build(self, level, cls="google-chrome", title="Salary review - John Smith - Docs - Google Chrome",
              url="https://docs.example.org/guide/attention"):
        return privacy.build_state(level, "write thesis", cls, title, url, SCREEN, privacy.SENSITIVE_DEFAULT)

    def test_strict_is_site_only(self):
        self.assertEqual(self.build("strict"), {"declared_task": "write thesis", "site": "docs.example.org"})

    def test_balanced_has_no_personal_data(self):
        blob = str(self.build("balanced")).lower()
        for leak in ("smith", "example.com", "7700", "intranet", "5521", "yasserdo", "gb29", "tok_fake", "acme", "q3"):
            self.assertNotIn(leak, blob)
        self.assertIn("attention", blob)  # the topic survives
        self.assertIn("encoder", blob)

    def test_full_masks_personal_data(self):
        blob = str(self.build("full"))
        for leak in ("john.smith@example.com", "7700 900123", "intranet.acme.com", "@yasserdo",
                     "GB29NWBK60161331926819", "tok_fake_51HxYz"):
            self.assertNotIn(leak, blob)
        self.assertIn("attention", blob)

    def test_sensitive_sites_are_strict_at_every_level(self):
        for level in privacy.LEVELS:
            state = self.build(level, url="https://mail.google.com/mail/u/0/#inbox")
            self.assertEqual(state, {"declared_task": "write thesis", "site": "mail.google.com"})
        webapp = privacy.build_state("full", "t", "chrome-web.whatsapp.com__-Default", "(3) Mom", "https://web.whatsapp.com/",
                                     SCREEN, privacy.SENSITIVE_DEFAULT)
        self.assertEqual(webapp, {"declared_task": "t", "site": "web.whatsapp.com"})

    def test_unknown_tabs_never_send_the_screen(self):
        browsers = ["google-chrome"]
        for level in ("balanced", "full"):
            state = privacy.build_state(level, "t", "google-chrome", "Quarterly plan - Google Chrome", "", SCREEN,
                                        privacy.SENSITIVE_DEFAULT, browsers)
            self.assertEqual(set(state), {"declared_task", "site", "title_words"})
        self.assertFalse(privacy.needs_screen("full", "google-chrome", "", privacy.SENSITIVE_DEFAULT,
                                              "Quarterly plan", browsers))

    def test_sensitive_titles_without_a_url(self):
        for title in ("Inbox (3) - you@x.com - Gmail - Google Chrome", "(2) WhatsApp - Google Chrome",
                      "Chase Bank - Accounts - Google Chrome"):
            state = privacy.build_state("full", "t", "google-chrome", title, "", SCREEN, privacy.SENSITIVE_DEFAULT,
                                        ["google-chrome"])
            self.assertEqual(state, {"declared_task": "t", "site": "google-chrome"}, title)

    def test_screen_is_only_needed_when_used(self):
        s = privacy.SENSITIVE_DEFAULT
        self.assertFalse(privacy.needs_screen("strict", "code", "", s))
        self.assertFalse(privacy.needs_screen("full", "google-chrome", "https://web.whatsapp.com/", s))
        self.assertTrue(privacy.needs_screen("balanced", "code", "", s))

    def test_keywords_keep_topic_words_in_any_case(self):
        words = privacy.keywords("Transformers use Attention. The attention mechanism weighs tokens.")
        self.assertIn("attention", words)
        self.assertIn("transformers", words)


if __name__ == "__main__":
    unittest.main()
