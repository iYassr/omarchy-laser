"""Paths and settings. Settings live in ~/.config/laser/settings.json, written by the daemon when
changed from the panel or CLI; the API key lives alone in ~/.config/laser/env (mode 600)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from laser.privacy import LEVELS, SENSITIVE_DEFAULT

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "laser"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "laser"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "laser"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "laser"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
ENV_FILE = CONFIG_DIR / "env"
STATE_FILE = RUNTIME_DIR / "state.json"
SOCKET_FILE = RUNTIME_DIR / "control.sock"
LOCK_FILE = RUNTIME_DIR / "daemon.lock"
SESSION_FILE = RUNTIME_DIR / "session.json"
HISTORY_FILE = DATA_DIR / "sessions.jsonl"
USAGE_FILE = DATA_DIR / "usage.json"
AUDIT_FILE = DATA_DIR / "sent.jsonl"
LOG_FILE = STATE_DIR / "laser.log"

# Seconds of accumulated distraction before each escalation step.
STRICTNESS = {  # red reticle, notification, red edge, fog the window, close it
    "gentle": (30, 60, 120, 180, 240),
    "strict": (10, 30, 60, 90, 120),
    "warn_only": (30, 60, 120, 180, 240),
}
FINAL_STEPS = ("close", "fog")  # at the limit: close the tab, or keep it fogged and never close


@dataclass
class Escalation:
    warn: float = 30
    nudge: float = 60
    tint: float = 120
    fog: float = 180
    block: float = 240
    renudge_every: float = 60
    decay_rate: float = 2.0  # focused seconds pay back distraction this many times faster
    block_enabled: bool = True


@dataclass
class Thresholds:
    focused: float = 0.55
    distracted: float = 0.35


@dataclass
class Rules:
    never_close: list[str] = field(default_factory=lambda: [
        "Alacritty", "kitty", "foot", "com.mitchellh.ghostty", "org.omarchy.terminal",
        "code", "Code", "code-oss", "cursor", "dev.zed.Zed", "jetbrains-idea", "nvim",
    ])
    browsers: list[str] = field(default_factory=lambda: [
        "chromium", "google-chrome", "brave-browser", "firefox", "zen", "vivaldi-stable",
        "microsoft-edge", "librewolf", "org.qutebrowser.qutebrowser",
    ])
    neutral_classes: list[str] = field(default_factory=lambda: [
        "TUI.float", "xdg-desktop-portal-gtk", "org.gnome.Nautilus", "org.pulseaudio.pavucontrol",
        "blueberry.py", "Impala",
    ])


@dataclass
class Config:
    privacy: str = "balanced"
    strictness: str = "gentle"
    look: str = "full"  # the "locked in" screen: full, subtle or off
    final_step: str = "close"
    sensitive: list[str] = field(default_factory=lambda: list(SENSITIVE_DEFAULT))
    poll_seconds: float = 2.0
    model: str = "jev-latest"
    escalation: Escalation = field(default_factory=Escalation)
    thresholds: Thresholds = field(default_factory=Thresholds)
    rules: Rules = field(default_factory=Rules)

    def apply_strictness(self) -> None:
        e = self.escalation
        e.warn, e.nudge, e.tint, e.fog, e.block = STRICTNESS[self.strictness]
        e.block_enabled = self.strictness != "warn_only" and self.final_step == "close"

    def public(self) -> dict:
        """What the panel shows and may change."""
        return {"privacy": self.privacy, "strictness": self.strictness, "look": self.look,
                "final_step": self.final_step, "sensitive": self.sensitive}


EDITABLE = {"privacy": LEVELS, "strictness": tuple(STRICTNESS), "look": ("full", "subtle", "off"),
            "final_step": FINAL_STEPS}


def load() -> Config:
    config = Config()
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    if data.get("privacy") in LEVELS:
        config.privacy = data["privacy"]
    if data.get("strictness") in STRICTNESS:
        config.strictness = data["strictness"]
    if data.get("look") in EDITABLE["look"]:
        config.look = data["look"]
    if data.get("final_step") in FINAL_STEPS:
        config.final_step = data["final_step"]
    if isinstance(data.get("sensitive"), list):
        config.sensitive = [str(s) for s in data["sensitive"]]
    for name in ("never_close", "browsers", "neutral_classes"):
        if isinstance(data.get(name), list):
            setattr(config.rules, name, [str(s) for s in data[name]])
    config.apply_strictness()
    return config


def save(config: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data.update(config.public())
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, SETTINGS_FILE)


def private_dirs() -> None:
    """Laser's files hold task names, sites and usage: readable by the user alone."""
    for d in (CONFIG_DIR, DATA_DIR, STATE_DIR, RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(d, 0o700)
        for f in d.iterdir():  # files written by older versions under a looser umask
            if f.is_file() and not f.is_symlink():
                os.chmod(f, 0o600)


_env_key = os.environ.pop("TYPESAFE_API_KEY", "")  # read once; never inherited by grim, hyprctl, ...


def api_key() -> str:
    """The user's own TypeSafe key: the environment wins, then ~/.config/laser/env."""
    if _env_key:
        return _env_key.strip()
    try:
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "TYPESAFE_API_KEY":
                return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def set_api_key(key: str) -> None:
    key = key.strip()
    if not key or any(c.isspace() for c in key) or not key.isascii():
        raise ValueError("that doesn't look like an API key")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"TYPESAFE_API_KEY={key}\n")
    os.chmod(ENV_FILE, 0o600)
