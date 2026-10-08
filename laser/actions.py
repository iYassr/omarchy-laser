"""Side effects on the desktop: notifications and closing distractions."""

from __future__ import annotations

import html
import re
import subprocess
import threading
from collections.abc import Callable

from laser.engine import Block, Notify
from laser.sense import active_window

APP = "Laser"
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]+$")
ICON = "dialog-warning"


class Notifier:
    def __init__(self):
        self._last_id: str | None = None
        self._lock = threading.Lock()

    def send(self, note: Notify, on_allow: Callable[[], None] | None = None) -> None:
        """Fire and forget. A notification with an action blocks until answered, so it runs on a thread."""
        threading.Thread(target=self._send, args=(note, on_allow), daemon=True).start()

    def _send(self, note: Notify, on_allow: Callable[[], None] | None) -> None:
        cmd = ["notify-send", "-a", APP, "-u", note.urgency, "-p"]
        if note.urgency != "critical":
            cmd += ["-t", "12000"]
        with self._lock:
            if self._last_id:  # replace the previous nudge instead of stacking them
                cmd += ["-r", self._last_id]
        body = note.body
        if note.offer_allow and on_allow:
            # Omarchy's toasts have no buttons; clicking the card fires the "default" action.
            cmd += ["-A", "default=It's for my task"]
            body += "\nClick here if this is actually for your task."
        # The body is rendered as markup and labels can come from page titles: escape them, and end
        # option parsing so a title starting with "-" can't become a notify-send flag.
        cmd += ["--", html.escape(note.title, quote=False), html.escape(body, quote=False)]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            return
        # The id comes first and right away; the action name follows only once the card is clicked.
        first = proc.stdout.readline().strip()
        if first:
            with self._lock:
                self._last_id = first
        try:
            rest, _ = proc.communicate(timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            return
        if "default" in rest.split() and on_allow:
            on_allow()


def hypr_dispatch(lua: str) -> None:
    subprocess.run(["hyprctl", "dispatch", lua], capture_output=True, timeout=3)


def block(action: Block) -> bool:
    """Close the distracting tab (browsers) or window, but only if it is still the one in focus.

    The title must match too: the user may have switched to a work tab inside the same window
    since the verdict, and Ctrl+W would then close the wrong tab.
    """
    current = active_window()
    if not current or current.address != action.window.address or current.title != action.window.title:
        return False
    if action.browser:
        # Ctrl+W to the focused surface closes just the tab. Down and up are sent separately
        # (as Omarchy's own bindings do) so the synthetic key can't get stuck.
        hypr_dispatch('hl.dsp.send_key_state({ mods = "CTRL", key = "W", state = "down" })')
        hypr_dispatch('hl.dsp.send_key_state({ mods = "CTRL", key = "W", state = "up" })')
    elif _ADDRESS.match(action.window.address):
        hypr_dispatch(f'hl.dsp.window.close({{ window = "address:{action.window.address}" }})')
    else:
        return False
    return True
