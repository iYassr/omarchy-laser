"""The "locked in" look: while a session runs, the focused window's border glows laser green
(red when you drift) and, in the full style, other windows dim. Runtime-only Hyprland changes:
the original values are saved first and restored when the session ends; nothing is written to
the user's config files, and `hyprctl reload` is never used (it would also wipe other plugins'
runtime changes, such as a screen shader).
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from laser.config import RUNTIME_DIR

STYLES = ("full", "subtle", "off")  # full: border + dimming + beam, subtle: border only
SAVED_FILE = RUNTIME_DIR / "look.json"

BORDERS = {
    "focused": (("rgba(39ff88ee)", "rgba(00d4ffee)"), 45),
    "unsure": (("rgba(e0a83aee)", "rgba(ffd166ee)"), 45),
    "distracted": (("rgba(ff3b3bee)", "rgba(ff8a3bee)"), 45),
}
DIM_STRENGTH = 0.35

_GRADIENT = re.compile(r"gradient data:\s*((?:[0-9a-fA-F]{8}\s+)+)(-?\d+)deg")
_HEX8 = re.compile(r"^[0-9a-fA-F]{8}$")
_RGBA = re.compile(r"^rgba\([0-9a-fA-F]{8}\)$")


def _hyprctl(*args: str) -> str:
    try:
        return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=3).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def _lua_gradient(colors: tuple[str, ...] | list[str], angle: int) -> str:
    # Only values we generated or parsed as hex ever reach the Lua string.
    if not all(_RGBA.match(c) for c in colors):
        raise ValueError("bad colour")
    return "{ colors = { " + ", ".join(f'"{c}"' for c in colors) + f" }}, angle = {int(angle)} }}"


def read_current() -> dict | None:
    """The live values, as `hyprctl getoption` reports them (gradient colours come back as AARRGGBB)."""
    border = _GRADIENT.search(_hyprctl("getoption", "general:col.active_border"))
    dim = re.search(r"bool:\s*(true|false)", _hyprctl("getoption", "decoration:dim_inactive"))
    strength = re.search(r"float:\s*([0-9.]+)", _hyprctl("getoption", "decoration:dim_strength"))
    if not (border and dim and strength):
        return None
    colors = [f"rgba({c[2:]}{c[:2]})" for c in border.group(1).split() if _HEX8.match(c)]
    return {"colors": colors, "angle": int(border.group(2)), "dim": dim.group(1) == "true",
            "strength": float(strength.group(1))}


def _apply(colors, angle, dim: bool, strength: float) -> None:
    lua = ("hl.config({ general = { col = { active_border = " + _lua_gradient(colors, angle) + " } }, "
           f"decoration = {{ dim_inactive = {'true' if dim else 'false'}, dim_strength = {float(strength):.2f} }} }})")
    _hyprctl("eval", lua)


class Look:
    def __init__(self):
        self.applied: tuple | None = None
        self.saved: dict | None = self._load_saved()

    @staticmethod
    def _load_saved() -> dict | None:
        try:
            saved = json.loads(SAVED_FILE.read_text())
            float(saved["strength"]), int(saved["angle"]), list(saved["colors"])
            return saved
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def update(self, style: str, status: str | None) -> None:
        """Called every tick; touches Hyprland only when the wanted look changes."""
        mode = status if status in BORDERS else ("focused" if status in ("checking", "neutral", "away") else None)
        if style == "off" or mode is None:  # no session, on a break, or switched off
            self.restore()
            return
        want = (style, mode)
        if want == self.applied:
            return
        if self.saved is None:
            self.saved = read_current()
            if self.saved is None:
                return  # can't read the originals: don't change what we couldn't put back
            SAVED_FILE.write_text(json.dumps(self.saved))
            os.chmod(SAVED_FILE, 0o600)
        colors, angle = BORDERS[mode]
        _apply(colors, angle, style == "full" or self.saved["dim"],
               DIM_STRENGTH if style == "full" else self.saved["strength"])
        self.applied = want

    def restore(self) -> None:
        if self.saved is None:
            self.applied = None
            return
        s = self.saved
        try:
            _apply(s["colors"], s["angle"], s["dim"], s["strength"])
        except (KeyError, ValueError, TypeError):
            pass
        self.saved = self.applied = None
        SAVED_FILE.unlink(missing_ok=True)
