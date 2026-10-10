#!/usr/bin/env python3
"""A fake vanilla RCON server for the tests.

    fake_rcon.py <server.properties> <commands.jsonl> [world.json]

Port and password come from server.properties. Every command is appended to
commands.jsonl. It answers `list`, `No player was found` to a tellraw for
Nobody, `Unknown or incomplete command…` to bogus, and 9000 bytes in
4096-byte packets to big. Like vanilla, it closes the connection when one
read holds more than one packet. Prints `ready` once it listens.

With FAKE_RCON_SHARED=1 it answers like vanilla's RconConsoleSource: one output buffer for
every connection, cleared when a command arrives, written while the command runs (one at a
time, as on the server thread) and read back after, so two clients with a command out at
once get each other's output glued into their replies. `echo <words>` answers its words.

world.json, read again for every command when it is given, holds what `vfg
state` asks about, answered in 26.3's words (`time query day` is the day timeline,
`time query time` the clock): {"players": {"Steve": {"pos": [x, y, z], "dim":
"minecraft:overworld", "shown": "[Owner] Steve"}}, "daytime": 18000, "day": 3,
"raining": false, "thundering": false}. Without it, Steve alone is online, at
0.5 64 0.5 in the overworld.
"""
import json
import os
import re
import socket
import socketserver
import struct
import sys
import threading
import time

PROPS, RECORD = sys.argv[1], sys.argv[2]
WORLD = sys.argv[3] if len(sys.argv) > 3 else None
props = {}
with open(PROPS) as f:
    for line in f:
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            props[k] = v
PORT = int(props.get("rcon.port", 25575))
PASSWORD = props.get("rcon.password", "")
lock = threading.Lock()
SHARED = os.environ.get("FAKE_RCON_SHARED") == "1"
shared_out = []                  # vanilla's one buffer for every RCON connection
server_thread = threading.Lock()  # commands run one at a time, as on the server thread


def world():
    try:
        with open(WORLD) as f:
            return json.load(f)
    except (TypeError, OSError, ValueError):
        return {"players": {"Steve": {}}}


def answer(cmd):
    if cmd.startswith("echo "):
        return [cmd[5:]]
    if cmd == "list":
        players = world().get("players") or {}
        shown = [p.get("shown") or name for name, p in players.items()]
        return ["There are %d of a max of 20 players online: %s" % (len(shown), ", ".join(shown))]
    m = re.match(r"^data get entity (\w+) (Pos|Dimension)$", cmd)
    if m:
        p = (world().get("players") or {}).get(m.group(1))
        if p is None:
            return ["No entity was found"]
        if m.group(2) == "Pos":
            x, y, z = p.get("pos") or [0.5, 64.0, 0.5]
            return ["%s has the following entity data: [%sd, %sd, %sd]" % (m.group(1), float(x), float(y), float(z))]
        return ['%s has the following entity data: "%s"' % (m.group(1), p.get("dim") or "minecraft:overworld")]
    # As 26.3 answers: the day timeline is the time of day, the clock every tick so far.
    if cmd == "time query day":
        return ["Timeline minecraft:day is at %d tick(s)" % world().get("daytime", 1000)]
    if cmd == "time query time":
        w = world()
        return ["Clock minecraft:overworld is at %d tick(s)" % (w.get("day", 0) * 24000 + w.get("daytime", 1000))]
    m = re.match(r'^execute if predicate \{"type":"minecraft:weather_check","(raining|thundering)":true\}$', cmd)
    if m:
        return ["Test passed" if world().get(m.group(1)) else "Test failed"]
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
            if SHARED:
                # prepareForCommand, then the command on the server thread, then the reply.
                del shared_out[:]
                time.sleep(0.002)
                with server_thread:
                    shared_out.append("".join(answer(body)))
                time.sleep(0.002)
                s.sendall(packet(rid, 0, "".join(shared_out)))
                continue
            for part in answer(body):
                s.sendall(packet(rid, 0, part))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


srv = Server(("127.0.0.1", PORT), Handler)
print("ready", flush=True)
srv.serve_forever()
