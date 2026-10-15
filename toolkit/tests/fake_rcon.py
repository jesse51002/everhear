"""A fake vanilla RCON server for the tests, in the test's own process, on 127.0.0.1.

    server = FakeRcon(password).start()   # server.port; server.stop()
    server.commands                       # every command, in order
    server.world = {...}                  # what `vfg state` asks about, below
    server.reset()                        # between tests: no commands, the default world

It answers `list`, `No player was found` to a tellraw for Nobody, `Unknown or incomplete
command…` to bogus, and 9000 characters in 4096-byte packets to big. Like vanilla, it closes
the connection when one read holds more than one packet. A `responder` (a callable taking the
command and returning its reply, or None to fall back to these answers) makes it any server a
test needs (test_vfg's state test, test_engine's migrate test).

With shared=True it answers like vanilla's RconConsoleSource: one output buffer for every
connection, cleared when a command arrives, written while the command runs (one at a time, as
on the server thread) and read back after, so two clients with a command out at once get each
other's output glued into their replies. `echo <words>` answers its words.

`world` is answered in 26.3's words (`time query day` is the day timeline, `time query time`
the clock): {"players": {"Steve": {"pos": [x, y, z], "dim": "minecraft:overworld", "shown":
"[Owner] Steve", "items": {"Inventory": "<snbt>", "equipment": "<snbt>", "SelectedItemSlot":
4}}}, "daytime": 18000, "day": 3, "raining": false, "thundering": false}. By default Steve
alone is online, at 0.5 64 0.5 in the overworld, carrying nothing.
"""
import re
import socket
import struct
import threading
import time

DEFAULT_WORLD = {"players": {"Steve": {}}}


class FakeRcon(object):
    def __init__(self, password, shared=False, responder=None):
        self.password = password
        self.shared = shared
        self.responder = responder
        self.commands = []
        self.world = dict(DEFAULT_WORLD)
        self._lock = threading.Lock()
        self._out = []                      # vanilla's one buffer, shared=True
        self._server_thread = threading.Lock()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(64)
        self.port = self._srv.getsockname()[1]
        self._clients = []
        self._stopped = False

    def start(self):
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self):
        self._stopped = True
        try:
            self._srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._srv.close()
        with self._lock:
            clients, self._clients = self._clients, []
        for c in clients:
            try:
                c.close()
            except OSError:
                pass

    def reset(self):
        with self._lock:
            del self.commands[:]
        self.world = dict(DEFAULT_WORLD)
        self.responder = None

    # ---------------------------------------------------------------- answers

    def player(self, name):
        return (self.world.get("players") or {}).get(name)

    def answer(self, cmd):
        """The reply's packets."""
        if self.responder is not None:
            got = self.responder(cmd)
            if got is not None:
                return [got]
        w = self.world
        if cmd.startswith("echo "):
            return [cmd[5:]]
        if cmd == "list":
            shown = [p.get("shown") or name for name, p in (w.get("players") or {}).items()]
            return ["There are %d of a max of 20 players online: %s" % (len(shown), ", ".join(shown))]
        m = re.match(r"^data get entity (\w+) (Pos|Dimension|Inventory|SelectedItemSlot)$", cmd)
        if m:
            p = self.player(m.group(1))
            if p is None:
                return ["No entity was found"]
            if m.group(2) == "Pos":
                x, y, z = p.get("pos") or [0.5, 64.0, 0.5]
                return ["%s has the following entity data: [%sd, %sd, %sd]" % (m.group(1), float(x), float(y), float(z))]
            if m.group(2) == "Dimension":
                return ['%s has the following entity data: "%s"' % (m.group(1), p.get("dim") or "minecraft:overworld")]
            items = p.get("items") or {}
            value = items.get(m.group(2), "[]" if m.group(2) == "Inventory" else 0)
            return ["%s has the following entity data: %s" % (m.group(1), value)]
        m = re.match(r"^execute if data entity (\w+) equipment run data get entity \1 equipment$", cmd)
        if m:
            got = ((self.player(m.group(1)) or {}).get("items") or {}).get("equipment")
            return ["%s has the following entity data: %s" % (m.group(1), got) if got else ""]
        if cmd == "time query day":
            return ["Timeline minecraft:day is at %d tick(s)" % w.get("daytime", 1000)]
        if cmd == "time query time":
            return ["Clock minecraft:overworld is at %d tick(s)" % (w.get("day", 0) * 24000 + w.get("daytime", 1000))]
        m = re.match(r'^execute if predicate \{"type":"minecraft:weather_check","(raining|thundering)":true\}$', cmd)
        if m:
            return ["Test passed" if w.get(m.group(1)) else "Test failed"]
        if cmd.startswith("tellraw Nobody "):
            return ["No player was found"]
        if cmd.split(" ")[0] == "bogus":
            return ["Unknown or incomplete command, see below for error\nbogus<--[HERE]"]
        if cmd == "big":
            body = "".join("%04d " % (i % 10000) for i in range(1800))  # 9000 characters
            return [body[i:i + 4096] for i in range(0, len(body), 4096)]
        if cmd.startswith("time set"):
            return ["Set the time to 1000"]
        return [""]

    # ---------------------------------------------------------------- the wire

    def _accept(self):
        while not self._stopped:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with self._lock:
                self._clients.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _packet(rid, typ, body):
        b = body.encode("utf-8")
        return struct.pack("<iii", len(b) + 10, rid, typ) + b + b"\x00\x00"

    def _serve(self, s):
        authed = False
        try:
            while True:
                data = s.recv(4096)
                if len(data) < 4:
                    return
                (length,) = struct.unpack("<i", data[:4])
                while len(data) < 4 + length:
                    more = s.recv(4096)
                    if not more:
                        return
                    data += more
                if len(data) > 4 + length:
                    return  # more than one packet in one read: vanilla drops it
                rid, typ = struct.unpack("<ii", data[4:12])
                body = data[12:4 + length - 2].decode("utf-8", "replace")
                if typ == 3:
                    authed = body == self.password
                    s.sendall(self._packet(rid if authed else -1, 2, ""))
                    continue
                if not authed:
                    s.sendall(self._packet(-1, 2, ""))
                    return
                with self._lock:
                    self.commands.append(body)
                if self.shared:
                    # prepareForCommand, then the command on the server thread, then the reply.
                    del self._out[:]
                    time.sleep(0.001)
                    with self._server_thread:
                        self._out.append("".join(self.answer(body)))
                    time.sleep(0.001)
                    s.sendall(self._packet(rid, 0, "".join(self._out)))
                    continue
                for part in self.answer(body):
                    s.sendall(self._packet(rid, 0, part))
        except OSError:
            return
        finally:
            try:
                s.close()
            except OSError:
                pass
