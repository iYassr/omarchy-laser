"""What leaves the machine. Every byte sent to Jev is built here, per privacy level.

  strict    site or app name only. The screen is never captured.
  balanced  + common dictionary words from the window title and keywords from the screen:
            no sentences, names, numbers, emails, handles or links.
  full      + the window title, the URL path and up to 1200 characters of on-screen text,
            with emails, numbers, secrets and handles masked.

Sensitive apps and sites (chat, email, banking, password managers) are always sent as strict,
whatever the level: their titles and screens are where personal data lives.
"""

from __future__ import annotations

import collections
import os
import re
from urllib.parse import urlsplit

LEVELS = ("strict", "balanced", "full")

SENSITIVE_DEFAULT = [
    # messaging
    "web.whatsapp.com", "whatsapp", "telegram", "org.telegram.desktop", "signal", "discord.com", "discord",
    "slack", "app.slack.com", "messenger.com", "teams.microsoft.com", "web.telegram.org", "chat.google.com",
    # email
    "mail.google.com", "outlook.live.com", "outlook.office.com", "mail.proton.me", "thunderbird", "evolution",
    # money, health, passwords
    "paypal.com", "wise.com", "revolut.com", "1password", "bitwarden", "keepassxc", "org.keepassxc.KeePassXC",
]

_DICTIONARIES = ("/usr/share/dict/words", "/usr/share/dict/cracklib-small", "/usr/share/dict/american-english")
_STOP = set("""
the and for are but not you all any can had her was one our out has have this that with from they will your
what when which their there been more than into them then some just also about would could should these those
other only over such very most here were where who why how its his him she hers yours mine ours their theirs
get got use used using new now see more less like make made via per yes off own same each both few may might
""".split())
_SUFFIX = re.compile(r"\s+[-–—|·]\s+[^-–—|·]+$")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+")
_HANDLE = re.compile(r"(?<!\w)[@#][\w.]{2,}")
_SECRET = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{10,30}\b")
_DIGITS = re.compile(r"\+?\d[\d \-().]{4,}\d")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_SPACES = re.compile(r"[ \t]+")


def _load_words() -> frozenset[str]:
    for path in _DICTIONARIES:
        if os.path.exists(path):
            with open(path, errors="ignore") as f:
                return frozenset(w.strip().lower() for w in f if w.strip().isalpha())
    return frozenset()


def _load_names() -> frozenset[str]:
    path = os.path.join(os.path.dirname(__file__), "names.txt")
    with open(path) as f:
        return frozenset(line.strip() for line in f if line.strip() and not line.startswith("#"))


WORDS = _load_words()
NAMES = _load_names()  # "john", "smith", "mark", "grace": dictionary words that are also people


def scrub(text: str) -> str:
    """Mask personal and secret data in free text (used by the full level)."""
    for pattern, mask in ((_EMAIL, "<email>"), (_URL, "<link>"), (_HANDLE, "<handle>"), (_SECRET, "<secret>"),
                          (_IBAN, "<iban>"), (_IP, "<ip>"), (_DIGITS, "<number>")):
        text = pattern.sub(mask, text)
    return text


def _is_word(word: str) -> bool:
    w = word.lower()
    if len(w) < 3 or w in _STOP or w in NAMES:
        return False
    if not WORDS:  # no dictionary installed: only lowercase words survive (names are capitalized)
        return word.islower()
    return w in WORDS or (w.endswith("s") and w[:-1] in WORDS) or (w.endswith("es") and w[:-2] in WORDS)


def keywords(text: str, limit: int = 40) -> list[str]:
    """The most frequent dictionary words on screen, lowercased: the topic without the sentences."""
    text = scrub(text)  # drop emails/links/handles first so their parts can't leak as "words"
    counts = collections.Counter(w.lower() for w in re.findall(r"(?<![\w<@#./-])[A-Za-z]{3,20}(?![\w>@./-])", text)
                                 if _is_word(w))
    return [w for w, _ in counts.most_common(limit)]


def title_words(title: str, limit: int = 12) -> list[str]:
    page = _SUFFIX.sub("", title)
    seen: list[str] = []
    for w in re.findall(r"[A-Za-z]{3,20}", scrub(page)):
        if _is_word(w) and w.lower() not in seen:
            seen.append(w.lower())
    return seen[:limit]


def clean_ocr(raw: str) -> str:
    """Keep lines that look like words; OCR of images and video frames yields mostly noise."""
    lines = []
    for line in raw.splitlines():
        line = _SPACES.sub(" ", line).strip()
        letters = sum(c.isalpha() for c in line)
        if letters >= 4 and letters >= len(line) * 0.5:
            lines.append(line)
    return "\n".join(lines)


def site_of(cls: str, url: str) -> str:
    host = (urlsplit(url).hostname or "") if url else ""
    return host.removeprefix("www.") or cls


# Words in a window title that mark chat, mail, banking or password pages even when the tab's
# URL is unknown (private windows keep no history; a fresh visit may not be saved yet).
SENSITIVE_TITLE_WORDS = {
    "whatsapp", "telegram", "signal", "discord", "slack", "messenger", "teams", "gmail", "outlook",
    "inbox", "mail", "proton", "thunderbird", "chat", "messages", "dm", "paypal", "wise", "revolut",
    "bank", "banking", "1password", "bitwarden", "keepassxc", "vault", "password", "passwords",
}
_TITLE_TOKENS = re.compile(r"[a-z0-9]+")


def is_sensitive(cls: str, url: str, sensitive: list[str], title: str = "") -> bool:
    site = site_of(cls, url).lower()
    names = {s.lower() for s in sensitive}
    if cls.lower() in names or site in names or any(site.endswith("." + s) for s in names if "." in s):
        return True
    return not SENSITIVE_TITLE_WORDS.isdisjoint(_TITLE_TOKENS.findall(title.lower()))


def unresolved_tab(cls: str, url: str, browsers: tuple[str, ...] | list[str]) -> bool:
    """A browser tab whose address we couldn't find: we don't know which site it is."""
    return not url and cls in browsers


def needs_screen(level: str, cls: str, url: str, sensitive: list[str], title: str = "",
                 browsers: tuple[str, ...] | list[str] = ()) -> bool:
    """Whether the screen should be OCR'd at all. Never for strict, sensitive, or unknown tabs."""
    return (level != "strict" and not is_sensitive(cls, url, sensitive, title)
            and not unresolved_tab(cls, url, browsers))


def build_state(level: str, task: str, cls: str, title: str, url: str, screen: str, sensitive: list[str],
                browsers: tuple[str, ...] | list[str] = ()) -> dict:
    """The exact JSON object sent to Jev."""
    state: dict = {"declared_task": task, "site": site_of(cls, url)}
    if level == "strict" or is_sensitive(cls, url, sensitive, title):
        return state
    if unresolved_tab(cls, url, browsers):  # unknown site: title keywords at most, never the screen
        state["title_words"] = " ".join(title_words(title))
        return state
    if level == "balanced":
        state["title_words"] = " ".join(title_words(title))
        state["screen_keywords"] = " ".join(keywords(screen))
        return state
    parts = urlsplit(url) if url else None
    state["window_title"] = scrub(title)
    if parts and parts.path not in ("", "/"):
        state["url_path"] = scrub(parts.path)[:120]
    state["visible_text"] = scrub(screen)[:1200]
    return state
