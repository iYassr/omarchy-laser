"""Reading the desktop: the focused window, its on-screen text, and whether anyone is there."""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from laser.privacy import clean_ocr


@dataclass(frozen=True)
class Window:
    address: str
    cls: str
    title: str
    pid: int
    x: int
    y: int
    width: int
    height: int
    url: str = ""  # for browsers, the tab's address without query or fragment, when it could be resolved

    @property
    def domain(self) -> str:
        host = urlsplit(self.url).hostname or ""
        return host.removeprefix("www.")

    @property
    def key(self) -> tuple[str, str]:
        return (self.cls, self.title)

    def geometry(self) -> str:
        return f"{self.x},{self.y} {self.width}x{self.height}"


def _run(cmd: list[str], timeout: float = 3.0, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **kwargs)


def active_window() -> Window | None:
    try:
        out = _run(["hyprctl", "activewindow", "-j"]).stdout
        data = json.loads(out or b"{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None
    if not data.get("address"):
        return None
    (x, y), (w, h) = data.get("at", (0, 0)), data.get("size", (0, 0))
    window = Window(
        address=data["address"],
        cls=data.get("class") or data.get("initialClass") or "",
        title=data.get("title") or "",
        pid=int(data.get("pid") or 0),
        x=int(x), y=int(y), width=int(w), height=int(h),
    )
    url = webapp_url(window.cls) or tab_url(window.cls, window.title, window.pid)
    return replace(window, url=url) if url else window


# Chromium-family "--app=" windows (Omarchy web apps: WhatsApp, Discord, ...) carry their site in
# the class: chrome-web.whatsapp.com__-Default, chrome-discord.com__channels_@me-Default.
_WEBAPP_CLASS = re.compile(r"^(?:chrome|brave|msedge|vivaldi|chromium)-([a-z0-9.-]+\.[a-z]{2,})__")


def webapp_url(cls: str) -> str:
    match = _WEBAPP_CLASS.match(cls)
    return f"https://{match.group(1)}/" if match else ""


# Browser history databases, read in place with immutable=1: no copy, no lock, ~1ms.
# Chromium-family timestamps are µs since 1601, Firefox's µs since 1970; both sort the same way.
_HISTORY = {
    "google-chrome": ("~/.config/google-chrome/*/History", "chromium"),
    "chromium": ("~/.config/chromium/*/History", "chromium"),
    "brave-browser": ("~/.config/BraveSoftware/Brave-Browser/*/History", "chromium"),
    "vivaldi-stable": ("~/.config/vivaldi/*/History", "chromium"),
    "microsoft-edge": ("~/.config/microsoft-edge/*/History", "chromium"),
    "firefox": ("~/.mozilla/firefox/*/places.sqlite", "firefox"),
    "librewolf": ("~/.librewolf/*/places.sqlite", "firefox"),
    "zen": ("~/.zen/*/places.sqlite", "firefox"),
}
_QUERIES = {
    "chromium": "SELECT url, last_visit_time FROM urls WHERE title = ? ORDER BY last_visit_time DESC LIMIT 1",
    "firefox": "SELECT url, last_visit_date FROM moz_places WHERE title = ? ORDER BY last_visit_date DESC LIMIT 1",
}
_BROWSER_SUFFIX = re.compile(r"\s+[-–—]\s+[^-–—]+$")
_url_cache: dict[tuple[str, str, int], str] = {}
_url_misses: dict[tuple[str, str, int], float] = {}
MISS_RETRY_SECONDS = 8


_PROFILE_ARG = {
    # Chromium rewrites its argv into one space-separated string, so match on the whole command line.
    "chromium": re.compile(r"--user-data-dir=(.+?)(?=\s--|\x00|$)"),
    "firefox": re.compile(r"(?:^|[\s\x00])--?profile[\s\x00]+(.+?)(?=\s-|\x00|$)"),
}


def _history_files(cls: str, pid: int) -> list[str]:
    """History databases of the browser instance that owns the window.

    A browser started with a custom profile (--user-data-dir, -profile) keeps its history there,
    so the owning process's command line wins over the default locations.
    """
    pattern, flavor = _HISTORY[cls]
    try:
        cmdline = open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace").strip("\x00")
    except OSError:
        cmdline = ""
    match = _PROFILE_ARG[flavor].search(cmdline)
    if match:
        path = os.path.expanduser(match.group(1))
        return glob.glob(os.path.join(path, "*", "History")) if flavor == "chromium" else [os.path.join(path, "places.sqlite")]
    return glob.glob(os.path.expanduser(pattern))


def tab_url(cls: str, title: str, pid: int = 0) -> str:
    """Find the focused tab's URL by looking its page title up in the browser's history."""
    if cls not in _HISTORY or not title:
        return ""
    key = (cls, title, pid)
    if key in _url_cache:
        return _url_cache[key]
    if time.monotonic() - _url_misses.get(key, -1e9) < MISS_RETRY_SECONDS:
        return ""
    flavor = _HISTORY[cls][1]
    page_title = _BROWSER_SUFFIX.sub("", title)
    best, best_time = "", -1
    for db in _history_files(cls, pid):
        try:
            # closing(): sqlite3's own context manager only ends the transaction and would leak the
            # connection (and its page cache) on every lookup.
            with closing(sqlite3.connect(f"file:{db}?immutable=1", uri=True, timeout=0.2)) as con:
                row = con.execute(_QUERIES[flavor], (page_title,)).fetchone()
        except sqlite3.Error:
            continue
        if row and (row[1] or 0) > best_time:
            best, best_time = row[0], row[1] or 0
    parts = urlsplit(best)
    url = f"{parts.scheme}://{parts.netloc}{parts.path}"[:160] if parts.netloc else ""
    if len(_url_cache) > 500 or len(_url_misses) > 500:
        _url_cache.clear()
        _url_misses.clear()
    if url:
        _url_cache[key] = url
    else:  # retried shortly: the visit may not be written to history yet
        _url_misses[key] = time.monotonic()
    return url


_LOADING_TITLES = {"", "untitled", "new tab", "about:blank", "loading…", "loading..."}


def is_loading(window: Window) -> bool:
    """A browser tab still loading shows a placeholder or its bare address as the title; judging
    (and caching a verdict for) that would only smear one page's verdict onto the next."""
    if window.cls not in _HISTORY:
        return False
    page = _BROWSER_SUFFIX.sub("", window.title).strip()
    return page.lower() in _LOADING_TITLES or (" " not in page and ("/" in page or "." in page))


def screen_text(window: Window) -> str:
    """OCR the focused window. The screenshot is piped straight to tesseract and never written to disk."""
    if window.width <= 0 or window.height <= 0:
        return ""
    env = {**os.environ, "OMP_THREAD_LIMIT": "2"}
    try:
        shot = _run(["grim", "-g", window.geometry(), "-t", "ppm", "-"], timeout=5)
        if shot.returncode != 0:
            return ""
        ocr = _run(["nice", "-n", "10", "tesseract", "stdin", "stdout", "--psm", "3"],
                   timeout=15, input=shot.stdout, env=env)
    except (subprocess.SubprocessError, OSError):
        return ""
    return clean_ocr(ocr.stdout.decode(errors="replace"))


def is_away() -> bool:
    """True while the screen is locked or the Omarchy idle cycle (screensaver) has begun."""
    try:
        locked = _run(["omarchy-shell", "lock", "isLocked"], timeout=2).stdout.strip()
        if locked == b"true":
            return True
        idle = json.loads(_run(["omarchy-shell", "idle", "status"], timeout=2).stdout or b"{}")
        return bool(idle.get("idle") or idle.get("inIdleCycle"))
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return False
