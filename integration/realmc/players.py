#!/usr/bin/env python3
"""Fake players on a realmc server, for tests: Carpet's /player, driven over RCON.

Carpet's fake players are real server players. They are in @a and `list`, have a position
and a rotation, hold items, sneak and walk, and a right-click with a carrot_on_a_stick counts
in minecraft.used:minecraft.carrot_on_a_stick, as a person's does. They need the server
started with Carpet (integration/realmc/realmc.py carpet, then up); without it every call
here raises NoCarpet. Test names only: Steve, Alex.

    import players
    p = players.on()               # the server `env` named ($REALMC_NAME), else main
    p = players.on("p1")           # a named one
    p.spawn("Steve")               # on the arena, facing south, in survival; returns (x, y, z)
    p.give("Steve", "carrot_on_a_stick[custom_name='Spirit Bomb']")
    p.look("Steve", 90, -10)       # yaw, pitch: what aim reads from the eyes
    p.use("Steve")                 # one right-click; hold (as a person holds it), use_interval, stop
    p.sneak("Steve")               # sneak("Steve", False) stands up
    p.move("Steve", "forward")     # forward, backward, left, right; move("Steve", None) stops
    p.pos("Steve"), p.rotation("Steve")
    p.kill("Steve"), p.kill_all()

    python3 integration/realmc/players.py [--name NAME] spawn Steve [X Y Z]
        look Steve YAW PITCH | look-at Steve X Y Z | use Steve [once|continuous|interval N]
        hold Steve | attack Steve [...] | drop Steve | sneak Steve on|off | sprint Steve on|off | jump Steve
        move Steve forward|backward|left|right|stop | stop Steve | hotbar Steve SLOT
        give Steve ITEM [COUNT] | tp Steve X Y Z [YAW PITCH] | pos Steve | kill Steve|all | list

Python 3.9+, standard library only.
"""
import argparse
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import realmc  # noqa: E402

NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
NUM = r"(-?\d+(?:\.\d+)?(?:[Ee]-?\d+)?)"


class NoCarpet(RuntimeError):
    """The server answers /player as an unknown command: it was started without Carpet."""


class Refused(RuntimeError):
    """Minecraft or Carpet refused a command; the message is its answer."""


def refused(out):
    return (out.startswith("Unknown or incomplete command") or out.startswith("Incorrect argument")
            or "<--[HERE]" in out)


class Players(object):
    def __init__(self, name=None):
        self.server = realmc.Server(name or realmc.default_name())
        self._r = None
        self._lock = threading.Lock()

    # -- RCON
    def cmd(self, command):
        """One command on this server; its answer. Reconnects once if the connection dropped."""
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._r is None:
                        if not self.server.pid():
                            raise RuntimeError("realmc %s is not running: integration/realmc/up %s"
                                               % (self.server.name, self.server.name))
                        self._r = self.server.rcon()
                    return self._r.command(command)
                except OSError:
                    if self._r is not None:
                        self._r.close()
                    self._r = None
                    if attempt:
                        raise

    def close(self):
        with self._lock:
            if self._r is not None:
                self._r.close()
                self._r = None

    def player(self, name, *words):
        """`player <name> <words…>`: Carpet's command."""
        if not NAME_RE.match(name):
            raise ValueError("%r is not a player name" % name)
        out = self.cmd("player %s %s" % (name, " ".join(str(w) for w in words)))
        if out.startswith("Unknown or incomplete command") and "player" in out:
            raise NoCarpet("realmc %s has no Carpet: integration/realmc/realmc.py carpet, then "
                           "integration/realmc/up %s --fresh" % (self.server.name, self.server.name))
        if refused(out):
            raise Refused(out)
        return out

    # -- where things are
    def arena(self):
        """The arena: {"x", "y", "z"} is where a player stands at its centre, "floor" its corners."""
        return realmc.read_json(self.server.info, {}).get("arena") or realmc.read_json(realmc.TEMPLATE_INFO, {})["arena"]

    def online(self):
        out = self.cmd("list")
        m = re.search(r"online:\s*(.*)$", out, re.S)
        return [n.strip() for n in m.group(1).split(",") if n.strip()] if m else []

    def _data(self, name, path):
        out = self.cmd("data get entity %s %s" % (name, path))
        if "No entity was found" in out or "<--[HERE]" in out:
            raise Refused(out)
        return [float(x) for x in re.findall(NUM + r"[dfDF]", out)]

    def pos(self, name):
        """(x, y, z) of the player's feet."""
        return tuple(self._data(name, "Pos")[:3])

    def rotation(self, name):
        """(yaw, pitch) in degrees, as Minecraft keeps them."""
        return tuple(self._data(name, "Rotation")[:2])

    # -- players
    def spawn(self, name, at=None, facing=(0, 0), gamemode="survival", timeout=10.0):
        """A fake player at `at` (default: the arena's centre), facing (yaw, pitch), in survival
        like the box's players (Carpet's own default from RCON is creative); waits until it is
        online and returns its position. A player already online is moved there instead."""
        if at is None:
            a = self.arena()
            at = (a["x"] + 0.5, a["y"], a["z"] + 0.5)
        if name in self.online():
            self.tp(name, at[0], at[1], at[2], facing[0], facing[1])
            if gamemode:
                self.cmd("gamemode %s %s" % (gamemode, name))
            return self.pos(name)
        words = ["spawn", "at", "%g %g %g" % tuple(at), "facing", "%g %g" % tuple(facing), "in", "minecraft:overworld"]
        if gamemode:
            words += ["in", gamemode]
        self.player(name, *words)
        deadline = time.monotonic() + timeout
        while name not in self.online():
            if time.monotonic() > deadline:
                raise Refused("%s did not come online within %g s" % (name, timeout))
            time.sleep(0.1)
        return self.pos(name)

    def kill(self, name, timeout=10.0):
        """Take a fake player off the server (Carpet's `kill`: it leaves, it does not die)."""
        self.player(name, "kill")
        deadline = time.monotonic() + timeout
        while name in self.online() and time.monotonic() < deadline:
            time.sleep(0.1)

    def kill_all(self):
        """Every fake player off; a real player (none, here) would be refused and stays."""
        for name in self.online():
            try:
                self.kill(name)
            except Refused:
                pass

    def look(self, name, yaw, pitch):
        """Face (yaw, pitch), as Rotation reads them. Through vanilla /rotate: Carpet 26.3's
        `look <yaw> <pitch>` sets them the wrong way round (`look 0 45` gives yaw 45, pitch 0)."""
        out = self.cmd("rotate %s %g %g" % (name, yaw, pitch))
        if not out.startswith("Rotated"):
            raise Refused(out)

    def look_at(self, name, x, y, z):
        self.player(name, "look", "at", "%g %g %g" % (x, y, z))

    def _action(self, name, action, mode):
        if mode in (None, "once"):
            return self.player(name, action, "once")
        if mode == "continuous":
            return self.player(name, action, "continuous")
        if isinstance(mode, int):
            return self.player(name, action, "interval", mode)
        raise ValueError("mode is once, continuous or a number of ticks")

    def use(self, name, mode="once"):
        """Right-click: once, continuous, or every N ticks (an int)."""
        return self._action(name, "use", mode)

    def use_continuous(self, name):
        return self._action(name, "use", "continuous")

    def use_interval(self, name, ticks):
        return self._action(name, "use", int(ticks))

    def hold(self, name):
        """Right-click held down, as a person holds it: one use every 4 ticks (5 a second).
        Carpet's `continuous` uses every tick, four times as fast as anyone can; stop() ends it."""
        return self._action(name, "use", 4)

    def drop(self, name, mode="once"):
        """Drop one of what the selected hotbar slot holds (Carpet's `drop`)."""
        return self._action(name, "drop", mode)

    def attack(self, name, mode="once"):
        return self._action(name, "attack", mode)

    def jump(self, name, mode="once"):
        return self._action(name, "jump", mode)

    def stop(self, name):
        """Every action and movement stopped."""
        self.player(name, "stop")

    def sneak(self, name, on=True):
        self.player(name, "sneak" if on else "unsneak")

    def sprint(self, name, on=True):
        self.player(name, "sprint" if on else "unsprint")

    def move(self, name, direction="forward"):
        """forward, backward, left or right until moved again; None or "stop" stops."""
        if direction in (None, "stop"):
            self.player(name, "move")
        elif direction in ("forward", "backward", "left", "right"):
            self.player(name, "move", direction)
        else:
            raise ValueError("direction is forward, backward, left, right or None")

    def hotbar(self, name, slot):
        self.player(name, "hotbar", int(slot))

    def give(self, name, item, count=1):
        """Vanilla /give; `item` may carry components: carrot_on_a_stick[custom_name='Spirit Bomb']."""
        out = self.cmd("give %s %s %d" % (name, item, count))
        if not out.startswith("Gave"):
            raise Refused(out)
        return out

    def clear(self, name):
        self.cmd("clear %s" % name)

    def tp(self, name, x, y, z, yaw=None, pitch=None):
        rot = "" if yaw is None else " %g %g" % (yaw, 0 if pitch is None else pitch)
        out = self.cmd("tp %s %g %g %g%s" % (name, x, y, z, rot))
        if not out.startswith("Teleported"):
            raise Refused(out)


_default = {}


def on(name=None):
    """The Players of a server: `env`'s ($REALMC_NAME), else main, or the one named."""
    name = name or realmc.default_name()
    if name not in _default:
        _default[name] = Players(name)
    return _default[name]


def main(argv=None):
    p = argparse.ArgumentParser(prog="players.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--name", metavar="NAME", help="the realmc server (default $REALMC_NAME, else main)")
    p.add_argument("action")
    p.add_argument("args", nargs="*")
    a = p.parse_args(argv)
    pl = on(a.name)
    x = a.args
    try:
        if a.action == "list":
            print(" ".join(pl.online()))
        elif a.action == "spawn":
            at = tuple(float(v) for v in x[1:4]) if len(x) >= 4 else None
            print("%s at %.2f %.2f %.2f" % ((x[0],) + pl.spawn(x[0], at)))
        elif a.action == "look":
            pl.look(x[0], float(x[1]), float(x[2]))
        elif a.action == "look-at":
            pl.look_at(x[0], float(x[1]), float(x[2]), float(x[3]))
        elif a.action in ("use", "attack"):
            mode = "once"
            if len(x) > 1:
                mode = int(x[2]) if x[1] == "interval" else x[1]
            print(pl._action(x[0], a.action, mode))
        elif a.action == "jump":
            pl.jump(x[0])
        elif a.action == "hold":
            pl.hold(x[0])
        elif a.action == "drop":
            pl.drop(x[0])
        elif a.action in ("sneak", "sprint"):
            getattr(pl, a.action)(x[0], (x[1] if len(x) > 1 else "on") == "on")
        elif a.action == "move":
            pl.move(x[0], x[1] if len(x) > 1 else "forward")
        elif a.action == "stop":
            pl.stop(x[0])
        elif a.action == "hotbar":
            pl.hotbar(x[0], x[1])
        elif a.action == "give":
            print(pl.give(x[0], x[1], int(x[2]) if len(x) > 2 else 1))
        elif a.action == "tp":
            pl.tp(x[0], *[float(v) for v in x[1:6]])
        elif a.action == "pos":
            print("%.2f %.2f %.2f  yaw %.1f pitch %.1f" % (pl.pos(x[0]) + pl.rotation(x[0])))
        elif a.action == "kill":
            pl.kill_all() if x[0] == "all" else pl.kill(x[0])
        else:
            p.error("unknown action %s" % a.action)
    except (IndexError, ValueError) as e:
        sys.stderr.write("players.py: bad arguments for %s (%s)\n" % (a.action, e))
        return 2
    except (NoCarpet, Refused, RuntimeError, OSError) as e:
        sys.stderr.write("players.py: %s\n" % e)
        return 1
    finally:
        pl.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
