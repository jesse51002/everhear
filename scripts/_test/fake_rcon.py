"""A fake Minecraft RCON server for tests: it speaks the RCON protocol, records
every command, and answers a few the way the fake toolkit does.

    list                          There are 1 of a max of 20 players online: Steve
    data get entity <p> Pos       <p> has the following entity data: [10.5d, 64.0d, -3.5d]
    data get entity <p> Health    <p> has the following entity data: 20.0f
    execute ... if|unless ...     Test passed
    scoreboard players get ...    Steve has 0 [x]
    anything else                 (nothing)

Use it in-process (FakeRcon) or on its own:
    python3 fake_rcon.py --port 25575 --password secret
"""
import argparse
import re
import socket
import struct
import threading
import time
from typing import List, Optional

ONLINE = "There are 1 of a max of 20 players online: Steve"


def answer(cmd: str) -> str:
    c = cmd.strip()
    if c == "list":
        return ONLINE
    m = re.match(r"data get entity (\S+) Pos$", c)
    if m:
        return "%s has the following entity data: [10.5d, 64.0d, -3.5d]" % m.group(1)
    m = re.match(r"data get entity (\S+) Health$", c)
    if m:
        return "%s has the following entity data: 20.0f" % m.group(1)
    if c.startswith("execute ") and (" if " in c or " unless " in c):
        return "Test passed"
    if c.startswith("scoreboard players get "):
        return "Steve has 0 [x]"
    return ""


class FakeRcon:
    def __init__(self, password: str, port: int = 0, delay_on: Optional[str] = None,
                 delay_s: float = 0.0):
        self.password = password
        self.delay_on = delay_on  # a command starting with this is answered late
        self.delay_s = delay_s
        self.commands = []
        self.logins = 0
        self.failed_logins = 0
        self._lock = threading.Lock()
        self._clients = []  # type: List[socket.socket]
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        self._stopped = False
        self._thread = threading.Thread(target=self._accept, daemon=True)

    def start(self) -> "FakeRcon":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stopped = True
        try:
            self._srv.close()
        except Exception:
            pass
        self.drop_all()

    def drop_all(self) -> None:
        """Close every client connection, as a server restart would."""
        with self._lock:
            clients, self._clients = self._clients, []
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass

    def cmds(self, since: float = 0.0) -> List[str]:
        with self._lock:
            return [c for t, c in self.commands if t >= since]

    def clear(self) -> None:
        with self._lock:
            self.commands = []

    def _accept(self) -> None:
        while not self._stopped:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with self._lock:
                self._clients.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    @staticmethod
    def _recv(conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise OSError("closed")
            buf += chunk
        return buf

    @staticmethod
    def _send(conn: socket.socket, req_id: int, kind: int, body: str) -> None:
        data = struct.pack("<ii", req_id, kind) + body.encode("utf-8") + b"\x00\x00"
        conn.sendall(struct.pack("<i", len(data)) + data)

    def _serve(self, conn: socket.socket) -> None:
        authed = False
        try:
            while True:
                (length,) = struct.unpack("<i", self._recv(conn, 4))
                data = self._recv(conn, length)
                req_id, kind = struct.unpack("<ii", data[:8])
                body = data[8:-2].decode("utf-8", errors="replace")
                if kind == 3:
                    if body == self.password:
                        authed = True
                        self.logins += 1
                        self._send(conn, req_id, 2, "")
                    else:
                        self.failed_logins += 1
                        self._send(conn, -1, 2, "")
                    continue
                if not authed:
                    self._send(conn, -1, 2, "")
                    continue
                with self._lock:
                    self.commands.append((time.time(), body))
                if self.delay_on and body.startswith(self.delay_on):
                    time.sleep(self.delay_s)
                self._send(conn, req_id, 0, answer(body))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=25575)
    ap.add_argument("--password", required=True)
    args = ap.parse_args()
    srv = FakeRcon(args.password, args.port).start()
    print("fake rcon on 127.0.0.1:%d" % srv.port, flush=True)
    seen = 0
    try:
        while True:
            time.sleep(0.2)
            cmds = srv.cmds()
            for c in cmds[seen:]:
                print(c, flush=True)
            seen = len(cmds)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
