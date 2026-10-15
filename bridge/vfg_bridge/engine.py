"""The game engine, as the bridge sees it (the game engine spec, "Incantations"): the incantations
bound to its abilities, read from its export without asking, and a cast, sent to its control socket.

The engine owns every incantation: a phrase bound, per player, to an ability (or to a scenario
with its args). It writes them to /opt/vfg/engine/incantations.json on every change, whole and
atomically: ``{"version": n, "incantations": [{"id", "player", "phrase", "aliases",
"everyday_aliases", "ability" | "scenario" and "args", "cooldown_s"}, ...]}``. A cast is one control
request on /opt/vfg/engine/engine.sock: one JSON line in, ``{"op": "cast", "args": {"player",
"phrase", "cast_id"}, "timeout_s"}``, and one JSON line out, the engine's reply (engine/README.md,
"The control socket"). Standard library only, as the whole bridge is: this is its own small client.

"""

import json
import logging
import math
import os
import socket

log = logging.getLogger("vfg_bridge")

MAX_REPLY = 1 << 20
# The socket's own read timeout runs this much past the engine's, so the engine's answer that it
# did not answer in time (reason "timeout") is what comes back, not a silent cut.
MARGIN_S = 0.25
EMPTY = {"version": 0, "incantations": []}


class Down(Exception):
    """The engine is not there: no socket, nobody listening on it, or it closed without a reply."""


class Late(Exception):
    """The engine did not answer in time."""


def _one_line(exc):
    return " ".join(str(exc).split()) or type(exc).__name__


def _strings(value):
    """A list of strings from an export field, or None when it is not one."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return list(value)


def _entry(raw):
    """An export entry as casts.py uses it, or None (with why) when it is not one."""
    if not isinstance(raw, dict):
        return None, "not an object"
    for key in ("player", "phrase"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            return None, "%s is not a name" % key
    what = [k for k in ("ability", "scenario") if isinstance(raw.get(k), str) and raw[k].strip()]
    if len(what) != 1:
        return None, "it binds neither an ability nor a scenario (or both)"
    if raw.get("args") is not None and not isinstance(raw.get("args"), dict):
        return None, "args is not an object"
    aliases, everyday = _strings(raw.get("aliases")), _strings(raw.get("everyday_aliases"))
    if aliases is None or everyday is None:
        return None, "aliases and everyday_aliases are lists of phrases"
    cooldown = raw.get("cooldown_s")
    if cooldown is not None and (isinstance(cooldown, bool) or not isinstance(cooldown, (int, float))
                                 or not math.isfinite(cooldown) or cooldown < 0):
        return None, "cooldown_s is not a number of seconds"
    entry = dict(raw, aliases=aliases, everyday_aliases=everyday)
    entry["id"] = str(raw.get("id") or "%s.%s" % (raw["player"].lower(),
                                                   "_".join(raw["phrase"].lower().split())))
    entry["cooldown_s"] = 0.0 if cooldown is None else float(cooldown)
    return entry, None


class Engine:
    """The engine's export and its socket. Thread-safe: the export is parsed again only when its
    file changes (the engine replaces it whole), and each request is its own connection."""

    def __init__(self, socket_path, export_path, logger=None):
        self.socket_path = socket_path
        self.export_path = export_path
        self.log = logger or log
        self._read = (None, EMPTY)   # the export's file key, and its parse
        self._warned = None          # the file key last warned about, so one bad file warns once

    # -- the export ----------------------------------------------------------------

    def file_key(self):
        try:
            st = os.stat(self.export_path)
        except OSError:
            return None
        return st.st_ino, st.st_size, st.st_mtime_ns

    def bindings(self):
        """The export: {"version": n, "incantations": [entry, ...]}; version 0 and none when there
        is no export (no engine on this box yet), or it cannot be read."""
        key = self.file_key()
        seen, doc = self._read
        if key is not None and key == seen:
            return doc
        doc = self._parse(key)
        self._read = (key, doc)
        return doc

    def _parse(self, key):
        if key is None:
            return EMPTY
        try:
            with open(self.export_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            self._warn(key, "the engine's incantations %s cannot be read (%s): its bindings are"
                            " left out until it is written again" % (self.export_path, _one_line(exc)))
            return EMPTY
        items = data.get("incantations") if isinstance(data, dict) else None
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(items, list) or isinstance(version, bool) or not isinstance(version, int):
            self._warn(key, 'the engine\'s incantations %s is not {"version": n, "incantations": [...]}:'
                            " its bindings are left out" % self.export_path)
            return EMPTY
        out, bad = [], []
        for raw in items:
            entry, why = _entry(raw)
            if entry is None:
                bad.append(why)
            else:
                out.append(entry)
        if bad:
            self._warn(key, "the engine's incantations %s has %d entr%s the bridge cannot use (%s):"
                            " they are left out" % (self.export_path, len(bad),
                                                    "y" if len(bad) == 1 else "ies", bad[0]))
        return {"version": version, "incantations": out}

    def _warn(self, key, text):
        if key != self._warned:
            self._warned = key
            self.log.warning(text)

    def for_player(self, name):
        """The engine's bindings for ``name`` (case-insensitive), in the export's order."""
        key = str(name or "").lower()
        return [e for e in self.bindings()["incantations"] if e["player"].lower() == key]

    # -- the socket ----------------------------------------------------------------

    def request(self, op, args, timeout):
        """One control request, and the engine's reply (a dict with ``ok``). ``timeout``: how long
        the engine may take, which it is told, so it takes a request it has not started off its
        queue then. Down when the engine is not there; Late when no reply came in time."""
        line = (json.dumps({"op": op, "args": args, "timeout_s": timeout}) + "\n").encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout + MARGIN_S)
            try:
                sock.connect(self.socket_path)
            except FileNotFoundError:
                raise Down("no engine socket at %s" % self.socket_path) from None
            except ConnectionRefusedError:
                raise Down("nothing answers on %s" % self.socket_path) from None
            except socket.timeout:
                raise Late("the engine's socket did not take the connection in %.1f s" % timeout) from None
            except OSError as exc:
                raise Down("cannot reach %s: %s" % (self.socket_path, _one_line(exc))) from None
            try:
                sock.sendall(line)
                reply = self._read_line(sock)
            except socket.timeout:
                raise Late("the engine did not answer %s in %.1f s" % (op, timeout)) from None
            except OSError as exc:
                raise Down("the engine's socket failed: %s" % _one_line(exc)) from None
        finally:
            sock.close()
        if not reply:
            raise Down("the engine closed the connection without a reply")
        try:
            doc = json.loads(reply.decode("utf-8"))
        except ValueError:
            raise Down("the engine's reply is not JSON") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("ok"), bool):
            raise Down("the engine's reply has no ok")
        return doc

    @staticmethod
    def _read_line(sock):
        chunks, size = [], 0
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if b"\n" in chunk or size > MAX_REPLY:
                break
        return b"".join(chunks).split(b"\n", 1)[0]

    def cast(self, player, phrase, cast_id, timeout):
        """The engine's answer to a cast of ``phrase`` by ``player`` (THE CONTROL OPS, ``cast``)."""
        return self.request("cast", {"player": player, "phrase": phrase, "cast_id": cast_id}, timeout)
