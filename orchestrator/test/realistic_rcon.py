#!/usr/bin/env python3
"""A fake vanilla RCON server that answers like a 26.3 server would, so an implementer
can confirm what it did: "Set the time to 1000", "Gave 1 [Diamond] to Steve",
"Summoned new Primed TNT", "Test passed, count: 3", and so on.

    realistic_rcon.py <server.properties> <commands.jsonl>

Port and password come from server.properties; every command is appended to
commands.jsonl as {"at": <epoch>, "cmd": <command>}. One player, Steve, is online at
10.5 64 -3.5. It keeps a small world state: the time, the weather, and the tags of what
was summoned, so counts and kills add up. Prints `ready` once it listens.
"""
import json
import re
import socketserver
import struct
import sys
import threading
import time

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

PLAYERS = ["Steve"]
POS = (10.5, 64.0, -3.5)
state = {"time": 18000, "weather": "clear", "tags": {}, "objectives": set()}
TIMES = {"day": 1000, "noon": 6000, "night": 13000, "midnight": 18000}


def pretty(item):
    return " ".join(w.capitalize() for w in item.split(":")[-1].split("[")[0].split("_"))


def selector_tags(sel):
    return re.findall(r"tag=([A-Za-z0-9_.+-]+)", sel)


def matching(sel):
    """How many tracked entities a selector matches (by its tags), or players."""
    if sel in PLAYERS or sel.startswith("@a") or sel.startswith("@p") or sel.startswith("@s"):
        return 1
    tags = [t for t in selector_tags(sel)]
    if not tags:
        return sum(state["tags"].values()) or 0
    return min(state["tags"].get(t, 0) for t in tags)


def answer(cmd):
    c = cmd.strip()
    w = c.split()
    if not w:
        return ""
    head = w[0]
    if head == "execute":
        if " run " in c:
            return answer(c.split(" run ", 1)[1])
        m = re.search(r"\b(if|unless) entity (\S+)", c)
        if m:
            n = matching(m.group(2))
            ok = (n > 0) == (m.group(1) == "if")
            if not ok:
                return "Test failed"
            return "Test passed, count: %d" % n if m.group(1) == "if" and not m.group(2).startswith("@a") else "Test passed"
        return "Test passed"
    if c == "list":
        return "There are %d of a max of 20 players online: %s" % (len(PLAYERS), ", ".join(PLAYERS))
    if head == "time":
        if len(w) >= 3 and w[1] in ("set", "add"):
            v = TIMES.get(w[2]) if w[2] in TIMES else (int(w[2]) if w[2].isdigit() else None)
            if v is None:
                return "Unknown or incomplete command, see below for error"
            state["time"] = v if w[1] == "set" else (state["time"] + v) % 24000
            return "Set the time to %d" % state["time"]
        if len(w) >= 3 and w[1] == "query":
            return "The time is %d" % (state["time"] % 24000 if w[2] == "daytime" else state["time"])
    if head == "weather" and len(w) >= 2:
        state["weather"] = w[1]
        return {"clear": "Changing to clear weather", "rain": "Changing to rain",
                "thunder": "Changing to rain and thunder"}.get(w[1], "Changing to clear weather")
    if head == "give" and len(w) >= 3:
        n = w[3] if len(w) >= 4 and w[3].isdigit() else "1"
        return "Gave %s [%s] to %s" % (n, pretty(w[2]), w[1])
    if head == "data" and len(w) >= 4 and w[1] == "get" and w[2] == "entity":
        who = w[3]
        if who not in PLAYERS:
            return "No entity was found"
        path = w[4] if len(w) > 4 else ""
        if path == "Pos":
            return "%s has the following entity data: [%.1fd, %.1fd, %.1fd]" % ((who,) + POS)
        if path == "Health":
            return "%s has the following entity data: 20.0f" % who
        if path == "Dimension":
            return '%s has the following entity data: "minecraft:overworld"' % who
        if path == "SelectedItem":
            return "Found no elements matching SelectedItem"
        return "%s has the following entity data: {}" % who
    if head == "summon" and len(w) >= 2:
        for t in re.findall(r'Tags:\[([^\]]*)\]', c):
            for tag in re.findall(r'"([^"]+)"', t):
                state["tags"][tag] = state["tags"].get(tag, 0) + 1
        return "Summoned new %s" % pretty(w[1])
    if head == "setblock" and len(w) >= 5:
        return "Changed the block at %s, %s, %s" % (w[1], w[2], w[3])
    if head == "fill" and len(w) >= 8:
        return "Successfully filled 27 block(s)"
    if head == "kill":
        sel = w[1] if len(w) > 1 else "@s"
        n = matching(sel)
        for t in selector_tags(sel):
            state["tags"].pop(t, None)
        return "Killed %d entities" % n if n else "No entity was found"
    if head == "tag" and len(w) >= 4:
        return "Added tag '%s' to %d entities" % (w[3], max(1, matching(w[1]))) if w[2] == "add" else \
            "Removed tag '%s' from %d entities" % (w[3] if len(w) > 3 else "", max(1, matching(w[1])))
    if head == "effect" and len(w) >= 4 and w[1] == "give":
        return "Applied effect %s to %s" % (pretty(w[3]), w[2])
    if head == "playsound":
        return "Played sound %s to %s" % (w[1] if len(w) > 1 else "", w[3] if len(w) > 3 else "Steve")
    if head == "particle":
        return "Displaying particle %s" % (w[1] if len(w) > 1 else "")
    if head == "title":
        return "Showing new title for %s" % (w[1] if len(w) > 1 else "Steve")
    if head == "tellraw" or head == "say":
        return ""
    if head == "scoreboard":
        if w[1:3] == ["objectives", "add"] and len(w) >= 4:
            if w[3] in state["objectives"]:
                return "An objective already exists by that name"
            state["objectives"].add(w[3])
            return "Created new objective [%s]" % w[3]
        if w[1:3] == ["objectives", "remove"] and len(w) >= 4:
            state["objectives"].discard(w[3])
            return "Removed objective [%s]" % w[3]
        if w[1:3] == ["players", "get"] and len(w) >= 5:
            return "%s has 0 [%s]" % (w[3], w[4])
        return ""
    if head == "gamerule" and len(w) >= 3:
        return "Gamerule %s is now set to: %s" % (w[1], w[2])
    if head == "worldborder":
        return "Set the world border to %s block(s) wide" % (w[2] if len(w) > 2 else "?")
    if head == "spawnpoint":
        return "Set spawn point to %.0f, %.0f, %.0f [0.0] in minecraft:overworld for %s" % (POS + ((w[1] if len(w) > 1 else "Steve"),))
    if head == "tp" or head == "teleport":
        return "Teleported %s to %.1f, %.1f, %.1f" % (((w[1] if len(w) > 1 else "Steve"),) + POS)
    if head == "clear" and len(w) >= 2:
        return "Removed 0 item(s) from player %s" % w[1]
    if head == "item":
        return "Replaced a slot"
    if head == "forceload":
        return "Marked chunk [0, 0] in minecraft:overworld to be force loaded"
    if head == "attribute":
        return "Base value for attribute is reset"
    if head == "gamemode":
        return "Set own game mode to Survival Mode"
    if head == "difficulty":
        return "The difficulty has been set to %s" % (w[1] if len(w) > 1 else "normal")
    if head in ("op", "deop", "kick", "ban", "ban-ip", "pardon", "pardon-ip", "whitelist", "stop", "save-off"):
        return "Made %s a server operator" % (w[1] if len(w) > 1 else "") if head == "op" else ""
    if head == "bossbar":
        return "Created custom bossbar [%s]" % (w[3] if len(w) > 3 else "") if w[1:2] == ["add"] else ""
    return ""  # vanilla prints something for most commands; silence is safer than a false error


def packet(rid, typ, body):
    b = body.encode("utf-8")
    return struct.pack("<iii", len(b) + 10, rid, typ) + b + b"\x00\x00"


def recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise OSError("closed")
        buf += chunk
    return buf


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        authed = False
        try:
            while True:
                (length,) = struct.unpack("<i", recv_exact(s, 4))
                data = recv_exact(s, length)
                rid, typ = struct.unpack("<ii", data[:8])
                body = data[8:-2].decode("utf-8", "replace")
                if typ == 3:
                    authed = body == PASSWORD
                    s.sendall(packet(rid if authed else -1, 2, ""))
                    continue
                if not authed:
                    s.sendall(packet(-1, 2, ""))
                    return
                with lock:
                    with open(RECORD, "a") as f:
                        f.write(json.dumps({"at": time.time(), "cmd": body}) + "\n")
                    out = answer(body)
                s.sendall(packet(rid, 0, out))
        except OSError:
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    srv = Server(("127.0.0.1", PORT), Handler)
    print("ready", flush=True)
    srv.serve_forever()
