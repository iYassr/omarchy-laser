"""A minimal, dependency-free client for TypeSafe's System One API (the Jev model).

Stdlib only, so the plugin needs nothing beyond python3. It keeps one connection alive and avoids
IPv6 when it's broken on the network: Python's HTTP stack has no happy-eyeballs fallback, so a dead
IPv6 route would otherwise cost the full timeout on every new connection.
"""

from __future__ import annotations

import http.client
import json
import math
import socket
import ssl
import time
from dataclasses import dataclass

HOST = "api.typesafe.ai"
PATH = "/v1/systemone"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000  # list price; output tokens are free

CATEGORIES = ["work", "research", "communication", "social_media", "entertainment", "shopping", "news", "system"]

QUESTIONS = {
    "on_task": {
        "type": "noul",
        "instructions": (
            "The user declared a task they want to focus on. Judge only what they are looking at now, "
            "from whatever is provided (site or app, title words, screen keywords or text): is it "
            "plausibly part of accomplishing that task? Tools, documentation, research, and "
            "conversations about the task count. Leisure, feeds, unrelated chats, or content on an "
            "unrelated topic do not, even inside an app that is sometimes used for work."
        ),
        "criteria": {
            "true": "What is on screen directly serves the declared task.",
            "false": "What is on screen is unrelated to the declared task or is a distraction.",
        },
    },
    # Bare labels: descriptions cost ~145 tokens per call and changed no verdict in testing.
    "category": {"type": "choice", "instructions": "Activity on screen", "criteria": {c: None for c in CATEGORIES}},
}


MAX_RESPONSE = 64 * 1024


def _printable(raw: bytes) -> str:
    """Server text shown to the user: no terminal escapes or control characters."""
    return "".join(c for c in raw.decode(errors="replace") if c.isprintable())


class JevError(Exception):
    pass


class AuthError(JevError):
    pass


@dataclass(frozen=True)
class Verdict:
    p_on_task: float
    category: str
    category_confidence: float
    latency_ms: int
    input_tokens: int


def _ipv6_works(host: str, timeout: float = 1.5) -> bool:
    try:
        addrs = socket.getaddrinfo(host, 443, socket.AF_INET6, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        with socket.create_connection(addrs[0][4][:2], timeout=timeout):
            return True
    except OSError:
        return False


class _Connection(http.client.HTTPSConnection):
    """HTTPS connection pinned to one address family."""

    def __init__(self, host: str, family: int, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.family = family

    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, self.family, socket.SOCK_STREAM)
        last: OSError | None = None
        for *_, addr in infos:
            try:
                raw = socket.create_connection(addr[:2], timeout=self.timeout)
                break
            except OSError as exc:
                last = exc
        else:
            raise last or OSError(f"cannot reach {self.host}")
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class Jev:
    def __init__(self, api_key: str, model: str = "jev-latest", timeout: float = 6.0, host: str = HOST):
        if not api_key:
            raise AuthError("no API key")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.host = host
        self.ipv4_only = not _ipv6_works(host)
        self._conn: _Connection | None = None

    def _connection(self) -> _Connection:
        if self._conn is None:
            family = socket.AF_INET if self.ipv4_only else socket.AF_UNSPEC
            self._conn = _Connection(self.host, family, self.timeout)
        return self._conn

    def _post(self, body: dict) -> dict:
        payload = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "User-Agent": "laser/1"}
        for attempt in range(2):  # one retry: a kept-alive connection may have been dropped by the server
            conn = self._connection()
            try:
                conn.request("POST", PATH, body=payload, headers=headers)
                resp = conn.getresponse()
                data = resp.read(MAX_RESPONSE + 1)
            except (OSError, http.client.HTTPException) as exc:
                self.close()
                if attempt:
                    raise JevError(f"network: {exc}") from exc
                continue
            if resp.status in (401, 403):
                raise AuthError("the API key was rejected")
            if resp.status == 429 or resp.status >= 500:
                if attempt:
                    raise JevError(f"HTTP {resp.status}")
                time.sleep(0.5)
                continue
            if resp.status != 200:
                raise JevError(f"HTTP {resp.status}: {_printable(data[:160])}")
            if len(data) > MAX_RESPONSE:
                self.close()
                raise JevError("response too large")
            try:
                return json.loads(data)
            except ValueError as exc:
                raise JevError("response is not JSON") from exc
        raise JevError("unreachable")

    def classify(self, state: dict) -> Verdict:
        started = time.monotonic()
        data = self._post({"model": self.model, "state": state, "questions": QUESTIONS})
        try:
            on_task, category = data["answers"]["on_task"], data["answers"]["category"]
            p = float(on_task["noul"])
            if not (math.isfinite(p) and 0 <= p <= 1):
                raise ValueError(p)
            return Verdict(
                p_on_task=p,
                category=str(category["choice"]) if category.get("choice") in CATEGORIES else "other",
                category_confidence=float(category.get("confidence", 0)),
                latency_ms=int((time.monotonic() - started) * 1000),
                input_tokens=int((data.get("usage") or {}).get("input_tokens") or 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JevError("unexpected response") from exc

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
