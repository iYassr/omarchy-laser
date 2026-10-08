"""The focus state machine. Pure: it is fed observations and returns actions, and never touches the desktop.
Times are wall-clock seconds (time.time()) so summaries can be logged as-is.

Distraction is tracked as a leaky bucket. Every distracted second adds a second; every focused
second drains `decay_rate` seconds. The bucket level, not a single verdict, picks the escalation
level, so a glance at Reddit does nothing and a quick alt-tab to the editor doesn't reset it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from laser.config import Config
from laser.jev import PRICE_PER_INPUT_TOKEN, Verdict
from laser.sense import Window

LEVELS = ("ok", "warn", "nudge", "tint", "block")
WEBAPP_PREFIXES = ("chrome-", "brave-", "msedge-", "vivaldi-", "chromium-")


@dataclass(frozen=True)
class Notify:
    title: str
    body: str
    urgency: str = "normal"
    offer_allow: bool = False


@dataclass(frozen=True)
class Ended:
    summary: dict


@dataclass(frozen=True)
class Block:
    window: Window
    browser: bool


@dataclass
class Session:
    task: str
    started: float
    ends_at: float | None = None
    paused_until: float | None = None
    allowed: set[str] = field(default_factory=set)
    status: str = "checking"
    bucket: float = 0.0
    level: int = 0
    focused_s: float = 0.0
    distracted_s: float = 0.0
    other_s: float = 0.0
    away_s: float = 0.0
    distractions: dict[str, float] = field(default_factory=dict)
    nudges: int = 0
    blocks: int = 0
    api_calls: int = 0
    input_tokens: int = 0
    last_nudge: float = -1e9
    last_block: float = -1e9
    window: Window | None = None
    verdict: Verdict | None = None


_SEPARATORS = re.compile(r"\s+[-–—|·]\s+")


def title_site(window: Window, browsers: list[str]) -> str:
    """The site as the window title names it ("YouTube"), or the app for non-browsers."""
    if window.cls not in browsers:
        return window.cls
    parts = [p for p in _SEPARATORS.split(window.title) if p]
    if len(parts) >= 2:
        parts = parts[:-1]  # drop the trailing browser name
    return parts[-1].strip() if parts else window.cls


def site_label(window: Window, browsers: list[str]) -> str:
    """A short name for what's on screen: the domain when the tab's URL is known, else the title's site."""
    return window.domain or title_site(window, browsers)


def _fmt(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m" if m else f"{int(seconds)}s"


class Engine:
    def __init__(self, config: Config):
        self.config = config
        self.session: Session | None = None

    # --- commands -------------------------------------------------------------------------

    def start(self, task: str, now: float, minutes: float | None = None) -> list[Notify]:
        self.session = Session(task=task, started=now, ends_at=now + minutes * 60 if minutes else None)
        until = f" for {_fmt(minutes * 60)}" if minutes else ""
        return [Notify("Focus session started", f"{task}{until}")]

    def stop(self, now: float, reason: str = "stopped") -> dict | None:
        if not self.session:
            return None
        summary = self.summary(now) | {"ended_by": reason}
        self.session = None
        return summary

    def pause(self, now: float, minutes: float) -> None:
        if self.session:
            self.session.paused_until = now + minutes * 60

    def resume(self) -> None:
        if self.session:
            self.session.paused_until = None

    def allow(self, window: Window) -> str:
        """Mark what's on screen as on-task for the rest of the session and forgive the backlog."""
        assert self.session
        label = site_label(window, self.config.rules.browsers)
        self.session.allowed.update(self._allow_keys(window))
        self.session.bucket = 0.0
        self.session.level = 0
        self.session.status = "focused"
        return label

    def is_allowed(self, window: Window) -> bool:
        return bool(self.session) and not self.session.allowed.isdisjoint(self._allow_keys(window))

    def _allow_keys(self, window: Window) -> set[str]:
        # Both the domain and the title's site name: the URL isn't always resolvable yet, and an
        # allow made while it was known must still match while it isn't (and vice versa).
        keys = {f"{window.cls}:{title_site(window, self.config.rules.browsers)}"}
        if window.domain:
            keys.add(f"{window.cls}:{window.domain}")
        return keys

    def block_skipped(self) -> None:
        """The close didn't happen (focus moved first): undo its bookkeeping so the next
        distracted tick, on whatever is on screen now, can close that instead."""
        s = self.session
        if s and s.blocks:
            s.blocks -= 1
            s.bucket = max(s.bucket, self.config.escalation.block)
            s.level = 4
            s.last_block = -1e9

    # --- observation ----------------------------------------------------------------------

    def tick(self, now: float, dt: float, window: Window | None, verdict: Verdict | None, away: bool) -> list:
        s = self.session
        if not s:
            return []
        if s.ends_at and now >= s.ends_at:
            summary = self.stop(now, "timer")
            return [Notify("Focus session complete", self.describe(summary)), Ended(summary)]
        if s.paused_until:
            if now < s.paused_until:
                s.status = "break"
                s.other_s += dt
                return []
            s.paused_until = None
            return [Notify("Break's over", f"Back to: {s.task}")]
        if away:
            s.status = "away"
            s.away_s += dt
            return []

        same_window = s.window is not None and window is not None and s.window.key == window.key
        s.window, s.verdict = window, verdict
        s.status = self._status(window, verdict, s.status if same_window else "checking")
        esc = self.config.escalation
        if s.status == "focused":
            s.focused_s += dt
            s.bucket = max(0.0, s.bucket - dt * esc.decay_rate)
        elif s.status == "distracted":
            s.distracted_s += dt
            s.bucket += dt
            label = site_label(window, self.config.rules.browsers)
            s.distractions[label] = s.distractions.get(label, 0.0) + dt
        else:
            s.other_s += dt
        s.level = sum(s.bucket >= t for t in (esc.warn, esc.nudge, esc.tint, esc.block))

        actions: list = []
        if s.status != "distracted":
            return actions
        if s.level >= 2 and now - s.last_nudge >= esc.renudge_every:
            s.last_nudge, s.nudges = now, s.nudges + 1
            label = site_label(window, self.config.rules.browsers)
            urgency = "critical" if s.level >= 3 else "normal"
            actions.append(Notify(f"You drifted to {label}", f"{_fmt(s.bucket)} off task. Back to: {s.task}",
                                  urgency, offer_allow=True))
        # Only tabs and web apps are closed: a native app window may hold unsaved work.
        closable = window.cls in self.config.rules.browsers or window.cls.startswith(WEBAPP_PREFIXES)
        protected = not closable or window.cls in self.config.rules.never_close
        if s.level >= 4 and esc.block_enabled and not protected and now - s.last_block >= 10:
            s.last_block, s.blocks = now, s.blocks + 1
            s.bucket = esc.tint  # stay hot: drifting straight back escalates quickly
            s.level = 3
            actions.append(Block(window, window.cls in self.config.rules.browsers))
        return actions

    def _status(self, window: Window | None, verdict: Verdict | None, previous: str) -> str:
        if window is None or window.cls in self.config.rules.neutral_classes:
            return "neutral"
        if self.is_allowed(window):
            return "focused"
        if verdict is None:
            return previous if previous in ("focused", "distracted") else "checking"
        t = self.config.thresholds
        if verdict.p_on_task >= t.focused:
            return "focused"
        if verdict.p_on_task <= t.distracted:
            return "distracted"
        return "unsure"

    # --- reporting ------------------------------------------------------------------------

    def summary(self, now: float) -> dict:
        s = self.session
        assert s
        judged = s.focused_s + s.distracted_s
        top = sorted(s.distractions.items(), key=lambda kv: -kv[1])[:5]
        return {
            "task": s.task,
            "started": s.started,
            "ended": now,
            "duration_s": round(now - s.started),
            "focused_s": round(s.focused_s),
            "distracted_s": round(s.distracted_s),
            "away_s": round(s.away_s),
            "focus_ratio": round(s.focused_s / judged, 3) if judged else None,
            "top_distractions": [[k, round(v)] for k, v in top],
            "nudges": s.nudges,
            "blocks": s.blocks,
            "api_calls": s.api_calls,
            "cost_usd": round(s.input_tokens * PRICE_PER_INPUT_TOKEN, 5),
        }

    @staticmethod
    def describe(summary: dict) -> str:
        ratio = summary["focus_ratio"]
        lines = [f"{_fmt(summary['duration_s'])} on “{summary['task']}”",
                 f"Focused {_fmt(summary['focused_s'])}" + (f" ({ratio:.0%})" if ratio is not None else "")
                 + f", distracted {_fmt(summary['distracted_s'])}"]
        if summary["top_distractions"]:
            lines.append("Top drains: " + ", ".join(f"{k} {_fmt(v)}" for k, v in summary["top_distractions"][:3]))
        return "\n".join(lines)

    def snapshot(self, now: float, error: str | None = None) -> dict:
        s = self.session
        if not s:
            return {"active": False, "status": "idle", "updated": now, "error": error}
        judged = s.focused_s + s.distracted_s
        return {
            "active": True,
            "task": s.task,
            "status": s.status,
            "level": LEVELS[s.level],
            "level_index": s.level,
            "bucket": round(s.bucket, 1),
            "tint": s.status == "distracted" and s.level >= 3,
            "label": site_label(s.window, self.config.rules.browsers) if s.window else "",
            "p_on_task": round(s.verdict.p_on_task, 3) if s.verdict else None,
            "category": s.verdict.category if s.verdict else None,
            "elapsed_s": round(now - s.started),
            "remaining_s": round(s.ends_at - now) if s.ends_at else None,
            "break_remaining_s": round(s.paused_until - now) if s.paused_until else None,
            "focused_s": round(s.focused_s),
            "distracted_s": round(s.distracted_s),
            "focus_ratio": round(s.focused_s / judged, 3) if judged else None,
            "nudges": s.nudges,
            "blocks": s.blocks,
            "api_calls": s.api_calls,
            "cost_usd": round(s.input_tokens * PRICE_PER_INPUT_TOKEN, 5),
            "error": error,
            "updated": now,
        }
