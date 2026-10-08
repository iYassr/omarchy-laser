"""Side effects on the desktop: notifications and closing distractions."""

from __future__ import annotations

import html
import re
import subprocess
import threading
from collections.abc import Callable

from laser.dbus import DBusError, Notifications
from laser.engine import Block, Notify
from laser.sense import active_window

APP = "Laser"
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]+$")
ICON = "dialog-warning"


class Notifier:
    """Desktop notifications over the session bus (laser/dbus.py), never through argv."""

    URGENCY = {"low": 0, "normal": 1, "critical": 2}

    def __init__(self):
        self._lock = threading.Lock()
        self._bus: Notifications | None = None
        self._last_id = 0
        self._callbacks: dict[int, Callable[[], None]] = {}

    def _on_action(self, nid: int, action: str) -> None:
        with self._lock:
            callback = self._callbacks.pop(nid, None)
        if action == "default" and callback:
            # Off the bus reader thread: the callback posts its own notification and needs the
            # reader free to deliver that reply.
            threading.Thread(target=callback, daemon=True).start()

    def send(self, note: Notify, on_allow: Callable[[], None] | None = None) -> None:
        body = note.body
        actions: list[str] = []
        if note.offer_allow and on_allow:
            # Omarchy's toasts have no buttons; clicking the card fires the "default" action.
            actions = ["default", "It's for my task"]
            body += "\nClick here if this is actually for your task."
        # The body is rendered as markup and labels can come from page titles: escape them.
        args = (html.escape(note.title, quote=False), html.escape(body, quote=False))
        timeout = -1 if note.urgency == "critical" else 12000
        for attempt in range(2):
            try:
                with self._lock:
                    if self._bus is None:
                        self._bus = Notifications(on_action=self._on_action)
                    bus, replaces = self._bus, self._last_id  # replace the previous nudge, don't stack
                nid = bus.notify(*args, replaces=replaces, actions=actions, urgency=self.URGENCY[note.urgency],
                                 timeout_ms=timeout)
                with self._lock:
                    self._last_id = nid
                    if actions:
                        self._callbacks[nid] = on_allow
                return
            except (OSError, DBusError):
                with self._lock:
                    if self._bus:
                        self._bus.close()
                    self._bus, self._last_id = None, 0


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
