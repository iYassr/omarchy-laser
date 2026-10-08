"""The long-running watcher: senses, classifies, runs the engine, and serves the control socket.

Started and supervised by the bar plugin (Service.qml); a lock keeps it to one instance.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import logging
import math
import logging.handlers
import os
import signal
import socketserver
import sys
import threading
import time
from dataclasses import fields, replace

from laser import actions, config as cfg, privacy, sense
from laser.engine import Block, Ended, Engine, Notify, Session, site_label
from laser.jev import PRICE_PER_INPUT_TOKEN, AuthError, Jev, JevError, Verdict
from laser.look import Look

log = logging.getLogger("laser")

AWAY_CHECK_SECONDS = 10
API_BACKOFF_SECONDS = 30
KEY_RECHECK_SECONDS = 10
CACHE_TTL_SECONDS = 600
MIN_RECLASSIFY_SECONDS = 6
RECLASSIFY_UNSURE_SECONDS = 20
RECLASSIFY_CONFIDENT_SECONDS = 60  # a clear verdict on an unchanged window rarely flips; saves calls
AUDIT_KEEP = 200
ALREADY_RUNNING = 3  # exit code the bar plugin reads as "someone else is watching; check back later"


def _number(value, low: float, high: float) -> float | None:
    """A finite number within [low, high], or None: inf/nan would wedge the state file."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and low <= n <= high else None


def write_json(path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def today() -> str:
    return datetime.date.today().isoformat()


class Daemon:
    def __init__(self, config: cfg.Config):
        self.config = config
        self.engine = Engine(config)
        self.notifier = actions.Notifier()
        self.lock = threading.RLock()
        self.jev: Jev | None = None
        self.key_checked = -1e9
        self.cache: dict[tuple[str, str], tuple[Verdict, float]] = {}
        self.by_window: dict[str, tuple[Verdict, float, str]] = {}  # reused while a window's title churns
        self.seen_address: str | None = None
        self.error: str | None = None
        self.needs_key = False
        self.backoff_until = 0.0
        self.flagged: sense.Window | None = None  # the last window judged a distraction
        self.away = False
        self.away_checked = 0.0
        self.running = True
        self.persisted = 0.0
        self.last_sent: dict | None = None
        self.usage = self._load_usage()
        self.look = Look()

    # --- control socket -------------------------------------------------------------------

    def command(self, req: dict) -> dict:
        cmd = req.get("cmd")
        now = time.time()
        with self.lock:
            s = self.engine.session
            if cmd == "status":
                return self.snapshot(now)
            if cmd == "usage":
                return {"ok": True, "days": self.usage, "price_per_million": PRICE_PER_INPUT_TOKEN * 1e6}
            if cmd == "audit":
                return {"ok": True, "last_sent": self.last_sent}
            if cmd == "set":
                return self._set(req)
            if cmd == "set_key":
                return self._set_key(str(req.get("key") or "").strip())
            if cmd == "start":
                task = str(req.get("task") or "").strip()
                if len(task) < 4 or sum(c.isalpha() for c in task) < 3:
                    return {"error": "describe the task in a few words, e.g. \"write chapter 2 of my thesis\""}
                if s:
                    self._finish(now, "replaced")
                self.cache.clear()  # verdicts are relative to the task
                self.by_window.clear()
                self.flagged = None
                minutes = _number(req.get("minutes"), 1, 24 * 60) if req.get("minutes") else None
                if req.get("minutes") and minutes is None:
                    return {"error": "minutes must be between 1 and 1440"}
                self._notify_all(self.engine.start(task[:300], now, minutes))
                self._persist()
                return {"ok": True, "task": task}
            if not s:
                return {"error": "no focus session running"}
            if cmd == "stop":
                return {"ok": True, "summary": self._finish(now, "stopped")}
            if cmd == "pause":
                minutes = _number(req.get("minutes") or 5, 1, 240)
                if minutes is None:
                    return {"error": "a break is 1 to 240 minutes"}
                self.engine.pause(now, minutes)
                self.notifier.send(Notify("Break", f"{minutes:g} min. Laser will check back in."))
                return {"ok": True}
            if cmd == "resume":
                self.engine.resume()
                return {"ok": True}
            if cmd == "allow":
                window = self.flagged or s.window
                if not window:
                    return {"error": "nothing to allow yet"}
                if not window.url:  # the visit may have been saved since; allow by site when we can
                    url = sense.tab_url(window.cls, window.title, window.pid)
                    window = replace(window, url=url) if url else window
                label = self.engine.allow(window)
                self.flagged = None
                self.notifier.send(Notify("Got it", f"{label} counts as on-task for this session."))
                self._persist()
                return {"ok": True, "allowed": label}
        return {"error": f"unknown command: {cmd}"}

    def _set(self, req: dict) -> dict:
        changed = {}
        for key, allowed in cfg.EDITABLE.items():
            if key in req:
                if req[key] not in allowed:
                    return {"error": f"{key} must be one of: {', '.join(allowed)}"}
                setattr(self.config, key, req[key])
                changed[key] = req[key]
        if not changed:
            return {"error": "nothing to change"}
        self.config.apply_strictness()
        cfg.save(self.config)
        if "privacy" in changed:
            self.cache.clear()
            self.by_window.clear()
        log.info("settings: %s", changed)
        return {"ok": True, "settings": self.config.public()}

    def _set_key(self, key: str) -> dict:
        """Verify first, save second: a typo must never replace a working key."""
        if not key or any(c.isspace() for c in key) or not key.isascii():
            return {"error": "that doesn't look like an API key"}
        warning = None
        try:  # one tiny call (~$0.00002)
            Jev(key, self.config.model).classify({"declared_task": "verify key", "site": "laser"})
        except AuthError:
            return {"error": "TypeSafe rejected that key; your saved key is unchanged"}
        except JevError as exc:
            warning = f"saved, but couldn't verify it right now ({exc})"
        cfg.set_api_key(key)
        self._connect(force=True)
        return {"ok": True, "warning": warning} if warning else {"ok": True}

    def _serve(self) -> None:
        daemon = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    reply = daemon.command(json.loads(self.rfile.readline() or b"{}"))
                except Exception as exc:  # never let a bad request kill the server
                    log.exception("command failed")
                    reply = {"error": str(exc)}
                self.wfile.write(json.dumps(reply).encode() + b"\n")

        cfg.SOCKET_FILE.unlink(missing_ok=True)
        server = socketserver.ThreadingUnixStreamServer(str(cfg.SOCKET_FILE), Handler)
        server.daemon_threads = True
        os.chmod(cfg.SOCKET_FILE, 0o600)
        server.serve_forever()

    # --- loop -----------------------------------------------------------------------------

    def run(self) -> None:
        cfg.private_dirs()
        lock = open(cfg.LOCK_FILE, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("already running")
            sys.exit(ALREADY_RUNNING)
        self._restore()
        threading.Thread(target=self._serve, daemon=True).start()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        self._connect(force=True)

        poll = self.config.poll_seconds
        last = time.time()
        log.info("watching (poll %.1fs, privacy %s, strictness %s)", poll, self.config.privacy, self.config.strictness)
        while self.running:
            started = time.time()
            # Cap dt so a suspend/resume gap isn't counted as hours of distraction.
            dt, last = min(started - last, poll * 3), started
            try:
                self._step(started, dt)
            except Exception:
                log.exception("tick failed")
            time.sleep(max(0.2, poll - (time.time() - started)))
        with self.lock:
            self._persist()
        self.look.restore()  # never leave the screen tinted behind us
        cfg.SOCKET_FILE.unlink(missing_ok=True)

    def _connect(self, force: bool = False) -> None:
        """(Re)build the Jev client from the current key; cheap to call, rate-limited unless forced."""
        now = time.time()
        if not force and now - self.key_checked < KEY_RECHECK_SECONDS:
            return
        self.key_checked = now
        key = cfg.api_key()
        if self.jev and self.jev.api_key == key and not force:
            return
        if self.jev:
            self.jev.close()
            self.jev = None
        self.needs_key = not key
        if not key:
            self.error = "Add your TypeSafe API key in Laser's settings"
            return
        self.jev = Jev(key, self.config.model)
        self.error = None
        self.backoff_until = 0.0
        if self.jev.ipv4_only:
            log.info("IPv6 to the API is unreachable; using IPv4")

    def _step(self, now: float, dt: float) -> None:
        session = self.engine.session
        window = sense.active_window()
        if session and now - self.away_checked >= AWAY_CHECK_SECONDS:
            self.away, self.away_checked = sense.is_away(), now

        verdict = None
        if session and not session.paused_until and not self.away and window:
            verdict = self._verdict(session.task, window, now)

        with self.lock:
            if self.engine.session is not session:  # a command swapped the session mid-classification
                return
            todo = self.engine.tick(now, dt, window, verdict, self.away)
            if self.engine.session and self.engine.session.status == "distracted":
                self.flagged = window
            for action in todo:
                self._act(action)
            s = self.engine.session
            self.look.update(self.config.look, None if not s or s.paused_until else s.status)
            write_json(cfg.STATE_FILE, self.snapshot(now))
            if now - self.persisted >= 15:
                self._persist()

    def _verdict(self, task: str, window: sense.Window, now: float) -> Verdict | None:
        with self.lock:
            allowed = self.engine.is_allowed(window)
        if window.cls in self.config.rules.neutral_classes or allowed or sense.is_loading(window):
            return None
        key = window.key
        cached = self.cache.get(key)
        if cached and now - cached[1] < self._ttl(cached[0]):
            return cached[0]
        # A window's last verdict stands in while its title churns, but only for the same site:
        # after a tab switch to another site it would judge (and could close) the wrong page.
        site = site_label(window, self.config.rules.browsers)
        previous = self.by_window.get(window.address)
        if previous and previous[2] != site:
            previous = None
        fallback = cached[0] if cached else previous[0] if previous else None
        if window.address != self.seen_address:
            # Wait one poll before paying for OCR, so alt-tabbing past a window costs nothing.
            self.seen_address = window.address
            return fallback
        if previous and now - previous[1] < MIN_RECLASSIFY_SECONDS:
            return fallback  # the title changed again already; don't classify every tick
        self._connect()
        if not self.jev or now < self.backoff_until:
            return fallback

        level, sensitive = self.config.privacy, self.config.sensitive
        browsers = self.config.rules.browsers
        screen = (sense.screen_text(window)
                  if privacy.needs_screen(level, window.cls, window.url, sensitive, window.title, browsers) else "")
        state = privacy.build_state(level, task, window.cls, window.title, window.url, screen, sensitive, browsers)
        try:
            verdict = self.jev.classify(state)
        except AuthError as exc:
            self.error = f"Jev: {exc}. Check your key in Laser's settings"
            self.backoff_until = now + API_BACKOFF_SECONDS
            log.warning(self.error)
            return fallback
        except JevError as exc:
            self.error = f"Jev unreachable ({exc}); retrying"
            self.backoff_until = now + API_BACKOFF_SECONDS
            log.warning(self.error)
            self._connect(force=True)  # the network may have changed (e.g. IPv6 broke)
            return fallback
        self.error = None
        self._record_usage(verdict, state, window, now)
        self.cache[key] = (verdict, now)
        self.by_window[window.address] = (verdict, now, site)
        if len(self.cache) > 200:
            self.cache = {k: v for k, v in self.cache.items() if now - v[1] < CACHE_TTL_SECONDS}
            self.by_window = {k: v for k, v in self.by_window.items() if now - v[1] < CACHE_TTL_SECONDS}
        log.info("%.2f %-13s %-8s %s (%dms, %d tok)", verdict.p_on_task, verdict.category, level,
                 site_label(window, self.config.rules.browsers), verdict.latency_ms, verdict.input_tokens)
        return verdict

    @staticmethod
    def _ttl(verdict: Verdict) -> float:
        confident = verdict.p_on_task <= 0.15 or verdict.p_on_task >= 0.85
        return RECLASSIFY_CONFIDENT_SECONDS if confident else RECLASSIFY_UNSURE_SECONDS

    def _act(self, action) -> None:
        if isinstance(action, Notify):
            window = self.flagged
            allow = (lambda: self._allow_from_notification(window)) if action.offer_allow else None
            self.notifier.send(action, on_allow=allow)
        elif isinstance(action, Block):
            if not actions.block(action):
                self.engine.block_skipped()
            else:
                label = site_label(action.window, self.config.rules.browsers)
                what = "tab" if action.browser else "window"
                log.info("closed %s: %s", what, label)
                self.notifier.send(Notify(f"Closed {label}", f"That {what} is gone. Back to: {self.engine.session.task}",
                                          "critical", offer_allow=True),
                                   on_allow=lambda: self._allow_from_notification(action.window))
        elif isinstance(action, Ended):
            self._record(action.summary)

    def _allow_from_notification(self, window: sense.Window | None) -> None:
        with self.lock:
            if self.engine.session and window:
                self.flagged = window
                self.command({"cmd": "allow"})

    def _notify_all(self, notes: list[Notify]) -> None:
        for note in notes:
            self.notifier.send(note)

    # --- usage, audit, state --------------------------------------------------------------

    def _load_usage(self) -> dict:
        try:
            return json.loads(cfg.USAGE_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def _record_usage(self, verdict: Verdict, state: dict, window: sense.Window, now: float) -> None:
        with self.lock:
            day = self.usage.setdefault(today(), {"calls": 0, "tokens": 0})
            day["calls"] += 1
            day["tokens"] += verdict.input_tokens
            if self.engine.session:
                self.engine.session.api_calls += 1
                self.engine.session.input_tokens += verdict.input_tokens
            self.last_sent = {"at": now, "privacy": self.config.privacy, "state": state,
                              "tokens": verdict.input_tokens, "p_on_task": verdict.p_on_task}
        write_json(cfg.USAGE_FILE, self.usage)
        # A local, size-capped record of exactly what left the machine, for `laser audit`.
        try:
            lines = cfg.AUDIT_FILE.read_text().splitlines()[-(AUDIT_KEEP - 1):] if cfg.AUDIT_FILE.exists() else []
            lines.append(json.dumps(self.last_sent))
            cfg.AUDIT_FILE.write_text("\n".join(lines) + "\n")
            os.chmod(cfg.AUDIT_FILE, 0o600)
        except OSError:
            pass

    def _usage_summary(self) -> dict:
        month = today()[:7]
        def total(days):
            calls = sum(d["calls"] for d in days)
            tokens = sum(d["tokens"] for d in days)
            return {"calls": calls, "tokens": tokens, "cost_usd": round(tokens * PRICE_PER_INPUT_TOKEN, 5)}
        return {
            "today": total([self.usage.get(today(), {"calls": 0, "tokens": 0})]),
            "month": total([d for k, d in self.usage.items() if k.startswith(month)]),
            "all": total(self.usage.values()),
        }

    def snapshot(self, now: float) -> dict:
        snap = self.engine.snapshot(now, self.error)
        snap["needs_key"] = self.needs_key
        snap["settings"] = self.config.public()
        snap["locked_in"] = bool(snap.get("active")) and snap.get("status") == "focused" and self.config.look == "full"
        snap["usage"] = self._usage_summary()
        snap["last_sent"] = self.last_sent
        return snap

    def _finish(self, now: float, reason: str) -> dict:
        summary = self.engine.stop(now, reason)
        self._record(summary)
        self.notifier.send(Notify("Focus session ended", Engine.describe(summary)))
        return summary

    def _record(self, summary: dict) -> None:
        with cfg.HISTORY_FILE.open("a") as f:
            f.write(json.dumps(summary) + "\n")
        cfg.SESSION_FILE.unlink(missing_ok=True)

    def _persist(self) -> None:
        self.persisted = time.time()
        s = self.engine.session
        if not s:
            cfg.SESSION_FILE.unlink(missing_ok=True)
            return
        data = {f.name: getattr(s, f.name) for f in fields(s) if f.name not in ("window", "verdict")}
        data["allowed"] = sorted(s.allowed)
        write_json(cfg.SESSION_FILE, data)

    def _restore(self) -> None:
        """Pick a session back up after a restart (shell reload, crash, update)."""
        if not cfg.SESSION_FILE.exists():
            return
        try:
            data = json.loads(cfg.SESSION_FILE.read_text())
            data["allowed"] = set(data.get("allowed", []))
            self.engine.session = Session(**data)
            log.info("resumed session: %s", self.engine.session.task)
        except (ValueError, TypeError) as exc:
            log.warning("could not restore session: %s", exc)
            cfg.SESSION_FILE.unlink(missing_ok=True)


def run() -> None:
    os.umask(0o077)
    cfg.private_dirs()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if not sys.stderr.isatty():  # started by the bar plugin: keep a small rotating log instead
        cfg.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.handlers.RotatingFileHandler(cfg.LOG_FILE, maxBytes=512_000, backupCount=1)]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%m-%d %H:%M:%S",
                        handlers=handlers)
    Daemon(cfg.load()).run()
