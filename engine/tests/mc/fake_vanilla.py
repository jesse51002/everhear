"""A fake vanilla RCON server for the client's tests, as 26.3's RconClient behaves (read from its
bytecode, engine/MINECRAFT.md): one read of up to 1460 bytes per packet, the connection dropped when
a read is not exactly one packet; a reply cut every 4096 UTF-16 units, one packet each; any packet
type but 2 and 3 answered "Unknown request <hex>"; and one shared reply buffer for every connection.

    srv = FakeVanilla(handler=lambda cmd: "reply")    # handler(command) -> reply text
    srv.port, srv.password
    srv.close()

`handler` may sleep, to stand for a slow command.
"""

from __future__ import annotations

import socket
import struct
import threading


class FakeVanilla:
    def __init__(self, handler=None, password="pw"):
        self.handler = handler or (lambda cmd: "")
        self.password = password
        self.commands: list[str] = []
        self.dropped = 0          # connections dropped for a read that was not one packet
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._closed = False
        self._run_lock = threading.Lock()     # the server thread: one command at a time
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)     # wakes the accept, so nothing more is taken
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def _accept(self):
        while not self._closed:
            try:
                c, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _send(self, c, rid, kind, text):
        data = text.encode("utf-8")
        c.sendall(struct.pack("<iii", len(data) + 10, rid, kind) + data + b"\x00\x00")

    def _client(self, c):
        authed = False
        try:
            while True:
                buf = c.recv(1460)
                if len(buf) < 10:
                    return
                (length,) = struct.unpack("<i", buf[:4])
                if length != len(buf) - 4:
                    self.dropped += 1          # vanilla: "not one packet" closes the connection
                    return
                rid, kind = struct.unpack("<ii", buf[4:12])
                body = buf[12:-2].decode("utf-8", "replace")
                if kind == 3:
                    authed = body == self.password
                    self._send(c, rid if authed else -1, 2, "")
                elif kind == 2 and authed:
                    with self._run_lock:
                        self.commands.append(body)
                        reply = self.handler(body)
                    units = reply.encode("utf-16-le")
                    pieces = [units[i:i + 8192].decode("utf-16-le", "replace") for i in range(0, len(units), 8192)] or [""]
                    for p in pieces:
                        self._send(c, rid, 0, p)
                elif kind == 2:
                    self._send(c, -1, 2, "")
                else:
                    self._send(c, rid, 0, "Unknown request %x" % kind)
        except OSError:
            pass
        finally:
            c.close()


def properties(tmp_path, srv: FakeVanilla) -> str:
    """A server.properties naming the fake server, for Rcon(properties=…)."""
    p = tmp_path / "server.properties"
    p.write_text("rcon.port=%d\nrcon.password=%s\n" % (srv.port, srv.password))
    return str(p)
