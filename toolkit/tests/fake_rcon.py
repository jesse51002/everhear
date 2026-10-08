#!/usr/bin/env python3
"""A fake vanilla RCON server for the tests.

    fake_rcon.py <server.properties> <commands.jsonl>

Port and password come from server.properties. Every command is appended to
commands.jsonl. It answers `list`, `No player was found` to a tellraw for
Nobody, `Unknown or incomplete command…` to bogus, and 9000 bytes in
4096-byte packets to big. Like vanilla, it closes the connection when one
read holds more than one packet. Prints `ready` once it listens.
"""
import json
import socket
import socketserver
import struct
import sys
import threading

PROPS, RECORD = sys.argv[1], sys.argv[2]
props = {}
with open(PROPS) as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            props[k] = v
PORT = int(props.get("rcon.port", 25575))
PASSWORD = props.get("rcon.password", "")
lock = threading.Lock()


def answer(cmd):
    if cmd == "list":
        return ["There are 1 of a max of 20 players online: Steve"]
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


def packet(rid, typ, body):
    b = body.encode("utf-8")
    return struct.pack("<iii", len(b) + 10, rid, typ) + b + b"\x00\x00"


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        authed = False
        while True:
            try:
                data = s.recv(4096)
            except OSError:
                return
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
                authed = body == PASSWORD
                s.sendall(packet(rid if authed else -1, 2, ""))
                continue
            if not authed:
                s.sendall(packet(-1, 2, ""))
                return
            with lock:
                with open(RECORD, "a") as f:
                    f.write(json.dumps(body) + "\n")
            for part in answer(body):
                s.sendall(packet(rid, 0, part))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


srv = Server(("127.0.0.1", PORT), Handler)
print("ready", flush=True)
srv.serve_forever()
