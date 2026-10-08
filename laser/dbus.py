"""Just enough of the D-Bus wire protocol, in the standard library, to post desktop notifications.

Notifications used to go through `notify-send`, which put the summary and body (your task, the site you
drifted to) in a process's argv, where any local user can read it. Talking to the session bus directly
keeps that text inside an owner-only socket.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
from collections.abc import Callable

DEST = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
IFACE = "org.freedesktop.Notifications"

_METHOD_CALL, _METHOD_RETURN, _ERROR, _SIGNAL = 1, 2, 3, 4
_FIELD = {"path": (1, "o"), "interface": (2, "s"), "member": (3, "s"), "destination": (6, "s"), "signature": (8, "g")}


class DBusError(Exception):
    pass


# --- marshalling (little-endian) -------------------------------------------------------------

def _pad(buf: bytearray, n: int) -> None:
    buf.extend(b"\0" * (-len(buf) % n))


def _split(sig: str) -> list[str]:
    """Split a signature into complete types: "susa{sv}i" -> ["s", "u", "s", "a{sv}", "i"]."""
    out, i = [], 0
    while i < len(sig):
        j = i
        while sig[j] == "a":
            j += 1
        if sig[j] in "({":
            depth, k = 0, j
            while True:
                depth += sig[k] in "({"
                depth -= sig[k] in ")}"
                k += 1
                if depth == 0:
                    break
            j = k - 1
        out.append(sig[i:j + 1])
        i = j + 1
    return out


_ALIGN = {"y": 1, "b": 4, "i": 4, "u": 4, "s": 4, "o": 4, "g": 1, "a": 4, "v": 1, "(": 8, "{": 8}


def _put(buf: bytearray, t: str, v) -> None:
    c = t[0]
    _pad(buf, _ALIGN[c])
    if c == "y":
        buf.append(v)
    elif c == "b":
        buf += struct.pack("<I", 1 if v else 0)
    elif c == "i":
        buf += struct.pack("<i", v)
    elif c == "u":
        buf += struct.pack("<I", v)
    elif c in "so":
        raw = v.encode()
        buf += struct.pack("<I", len(raw)) + raw + b"\0"
    elif c == "g":
        raw = v.encode()
        buf += bytes([len(raw)]) + raw + b"\0"
    elif c == "v":
        sig, val = v
        _put(buf, "g", sig)
        _put(buf, sig, val)
    elif c == "a":
        inner = t[1:]
        length_at = len(buf)
        buf += b"\0\0\0\0"
        _pad(buf, _ALIGN[inner[0]])
        start = len(buf)
        items = v.items() if inner[0] == "{" else v
        for item in items:
            _put(buf, inner, item)
        struct.pack_into("<I", buf, length_at, len(buf) - start)
    elif c in "({":
        for sub, val in zip(_split(t[1:-1]), v):
            _put(buf, sub, val)
    else:
        raise DBusError(f"type {t} not supported")


def _get(data: bytes, pos: int, t: str):
    c = t[0]
    pos += -pos % _ALIGN[c]
    if c == "y":
        return data[pos], pos + 1
    if c in "bu":
        return struct.unpack_from("<I", data, pos)[0], pos + 4
    if c == "i":
        return struct.unpack_from("<i", data, pos)[0], pos + 4
    if c in "so":
        n = struct.unpack_from("<I", data, pos)[0]
        return data[pos + 4:pos + 4 + n].decode(errors="replace"), pos + 5 + n
    if c == "g":
        n = data[pos]
        return data[pos + 1:pos + 1 + n].decode(), pos + 2 + n
    if c == "v":
        sig, pos = _get(data, pos, "g")
        return _get(data, pos, sig)
    if c == "a":
        n = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        pos += -pos % _ALIGN[t[1]]
        end, items = pos + n, []
        while pos < end:
            item, pos = _get(data, pos, t[1:])
            items.append(item)
        return items, end
    if c in "({":
        vals = []
        for sub in _split(t[1:-1]):
            val, pos = _get(data, pos, sub)
            vals.append(val)
        return tuple(vals), pos
    raise DBusError(f"type {t} not supported")


def _message(kind: int, serial: int, fields: dict, sig: str = "", args: tuple = ()) -> bytes:
    body = bytearray()
    for t, v in zip(_split(sig), args):
        _put(body, t, v)
    header_fields = [(code, (vsig, fields[name])) for name, (code, vsig) in _FIELD.items() if name in fields]
    if sig:
        header_fields.append((8, ("g", sig)))
    head = bytearray(b"l" + bytes([kind, 0, 1]) + struct.pack("<II", len(body), serial))
    _put(head, "a(yv)", header_fields)
    _pad(head, 8)
    return bytes(head + body)


# --- connection -------------------------------------------------------------------------------

def _bus_path() -> str:
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    for part in addr.split(";"):
        if part.startswith("unix:"):
            opts = dict(kv.split("=", 1) for kv in part[5:].split(",") if "=" in kv)
            if "path" in opts:
                return opts["path"]
    return f"{os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}/bus"


class Notifications:
    """A session-bus connection for posting notifications and hearing which were clicked."""

    def __init__(self, on_action: Callable[[int, str], None] | None = None):
        self.on_action = on_action
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(_bus_path())
        self.sock.sendall(b"\0AUTH EXTERNAL " + str(os.getuid()).encode().hex().encode() + b"\r\n")
        if not self._readline().startswith(b"OK"):
            raise DBusError("session bus refused authentication")
        self.sock.sendall(b"BEGIN\r\n")
        self.serial = 0
        self.lock = threading.Lock()
        self.replies: dict[int, tuple] = {}
        self.cond = threading.Condition()
        self.buf = b""
        self.sock.settimeout(None)
        threading.Thread(target=self._reader, daemon=True).start()
        self._call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello")
        self._call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s",
                   (f"type='signal',interface='{IFACE}',member='ActionInvoked'",))

    def _readline(self) -> bytes:
        line = b""
        while not line.endswith(b"\r\n"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise DBusError("session bus closed")
            line += chunk
        return line

    def _recv(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise DBusError("session bus closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _reader(self) -> None:
        try:
            while True:
                fixed = self._recv(16)
                if fixed[0:1] != b"l":
                    raise DBusError("big-endian messages are not supported")
                kind = fixed[1]
                body_len, _serial, fields_len = struct.unpack_from("<III", fixed, 4)
                rest = self._recv(fields_len + (-(16 + fields_len) % 8) + body_len)
                msg = fixed + rest
                fields, pos = _get(msg, 12, "a(yv)")
                f = dict(fields)
                pos += -pos % 8
                sig = f.get(8, "")
                args, p = [], pos
                for t in _split(sig):
                    val, p = _get(msg, p, t)
                    args.append(val)
                if kind in (_METHOD_RETURN, _ERROR) and 5 in f:
                    with self.cond:
                        self.replies[f[5]] = (kind, args)
                        self.cond.notify_all()
                elif kind == _SIGNAL and f.get(3) == "ActionInvoked" and self.on_action and len(args) == 2:
                    try:
                        self.on_action(int(args[0]), str(args[1]))
                    except Exception:
                        pass
        except (OSError, DBusError, struct.error, IndexError):
            with self.cond:
                self.replies[-1] = (_ERROR, ["disconnected"])
                self.cond.notify_all()

    def _call(self, dest: str, path: str, iface: str, member: str, sig: str = "", args: tuple = ()) -> list:
        with self.lock:
            self.serial += 1
            serial = self.serial
            self.sock.sendall(_message(_METHOD_CALL, serial, {"path": path, "interface": iface, "member": member,
                                                              "destination": dest}, sig, args))
        with self.cond:
            if not self.cond.wait_for(lambda: serial in self.replies or -1 in self.replies, timeout=5):
                raise DBusError(f"{member}: no reply")
            if -1 in self.replies:
                raise DBusError("session bus closed")
            kind, out = self.replies.pop(serial)
        if kind == _ERROR:
            raise DBusError(f"{member}: {out[0] if out else 'error'}")
        return out

    def notify(self, summary: str, body: str, *, app: str = "Laser", icon: str = "", replaces: int = 0,
               actions: list[str] | None = None, urgency: int = 1, timeout_ms: int = -1) -> int:
        out = self._call(DEST, PATH, IFACE, "Notify", "susssasa{sv}i",
                         (app, replaces, icon, summary, body, actions or [], {"urgency": ("y", urgency)}, timeout_ms))
        return int(out[0])

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
