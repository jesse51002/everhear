"""Stand-in actions and scripts for the vfg tests: the command, edit, stop and save_place
actions, smaller than the library's, and a few of the tests' own: beam (until stopped, live
values), ping and slow (once).

    standins.install(repo)    # writes them into a test's repo, with the real scripts/_lib

Each action is a class over the repo's own actions/_types.py, as the library's are; each script
uses the kit as a library script does: values(), result(), and instances.py for edit and stop.
Each sets mc.EXIT_GRACE to 0: it sets nothing off that needs a tick to go, and a copy that ends
then ends at once, not 0.4 s later.
"""
import os
import shutil

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS))
REAL_LIB = os.path.join(REPO, "scripts", "_lib")

ACTIONS = {
    "command": '''from actions._types import Action


class Command(Action):
    """Run server commands in order (a test's stand-in)."""

    script = "command"
    runs = "once"
    takes = ["commands"]

    commands: list[str] = []
    """the commands, without a slash"""
''',
    "edit": '''from actions._types import Action


class Edit(Action):
    """Change values on a running copy (a test's stand-in)."""

    script = "edit"
    runs = "once"
    takes = ["copy", "values"]

    copy: str = ""
    """the running copy"""
    values: list[str] = []
    """the values to change (dict[str, Any] in the real one; vfg sends the checked dict as it is)"""
''',
    "stop": '''from actions._types import Action


class Stop(Action):
    """Stop a running copy (a test's stand-in)."""

    script = "stop"
    runs = "once"
    takes = ["copy"]

    copy: str = ""
    """the running copy"""
''',
    "save_place": '''from actions._types import Action


class SavePlace(Action):
    """Save where a player stands, or x y z, under a name (a test's stand-in)."""

    script = "save_place"
    runs = "once"
    takes = ["name", "player", "at"]

    name: str = ""
    """the place's name"""
    player: str = ""
    """where this player stands"""
    at: str = ""
    """x y z, and a dimension"""
''',
    "beam": '''from pydantic import Field

from actions._types import Action, Live, Players


class Beam(Action):
    """A beam that logs its values every 0.1 s (a test's stand-in)."""

    script = "beam"
    runs = "until_stopped"
    takes = ["player", "cooldown"]
    fixed = {"colour": "red"}

    player: Players = "@a"
    """who has it"""
    length: int = Field(60, ge=5, le=200)
    """how far it reaches, in blocks"""
    cooldown: Live[float] = Field(10, ge=0, le=60)
    """seconds between shots"""
    colour: Live[str] = "red"
    """its colour"""
    report: bool = True
    """whether it says it is ready"""
''',
    "ping": '''from typing import Literal

from pydantic import Field

from actions._types import Action


class Ping(Action):
    """Ask who is online, once (a test's stand-in)."""

    script = "ping"
    runs = "once"
    takes = ["say"]

    say: str = "hello"
    """what it says first"""
    nap: float = Field(0, ge=0, le=30)
    """seconds it waits first"""
    fail: Literal["", "refuse", "crash"] = ""
    """how it fails: a result() and exit 1, or an error nothing catches; "" for not at all"""
    early: bool = False
    """whether it says it started before its nap"""
''',
    "slow": '''from actions.ping import Ping


class Slow(Ping):
    """Ask who is online, after a nap longer than its timeout (a test's stand-in)."""

    timeout_s = 1
    defaults = {"nap": 3}
''',
}

SCRIPTS = {
    "command": '''"""Command: runs its commands in order and stops at the first Minecraft refuses."""
import mc
mc.EXIT_GRACE = 0  # a stand-in sets nothing off that needs a tick to go
import sys

import mc
from kit import result, values

v = values()
replies, failed = [], None
for c in v["commands"]:
    out = mc.rcon(c)
    replies.append(out)
    if out.startswith("Unknown or incomplete command"):
        failed = c
        break
result("; ".join(r or "(no reply)" for r in replies), replies=replies, failed=failed)
sys.exit(1 if failed else 0)
''',
    "edit": '''"""Edit: changes values on a running copy; live ones land in place, any other restarts it."""
import mc
mc.EXIT_GRACE = 0  # a stand-in sets nothing off that needs a tick to go
import sys

import instances
import mc
from kit import result, values

v = values()
try:
    got = instances.edit(v["copy"], v["values"], v["changed"], v["live_only"])
except instances.CopyError as e:
    result(str(e))
    sys.exit(1)
shown = " ".join("%s=%s" % (k, got["values"][k]) for k in got["changed"])
if got["live"]:
    result("%s: %s, live" % (v["copy"], shown), **got)
else:
    result("%s restarted with %s, pid %d" % (v["copy"], shown, got["restarted"]), **got)
''',
    "stop": '''"""Stop: stops a running copy; everything it made goes."""
import mc
mc.EXIT_GRACE = 0  # a stand-in sets nothing off that needs a tick to go
import sys

import instances
from kit import result, values

v = values()
try:
    was = instances.stop(v["copy"])
except instances.CopyError as e:
    result(str(e), copy=v["copy"])
    sys.exit(1)
if was:
    result("stopped %s" % v["copy"], copy=v["copy"], was_running=True)
else:
    result("%s was not running" % v["copy"], copy=v["copy"], was_running=False)
''',
    "save_place": '''"""Save place: saves where a player stands, or x y z, under a name."""
import mc
mc.EXIT_GRACE = 0  # a stand-in sets nothing off that needs a tick to go
import sys

import mc
import places
from kit import result, values, world

v = values()
at = world.place(v["player"] or v["at"])
if at is None:
    result("%s is not online" % v["player"] if v["player"] else "nowhere to save")
    sys.exit(1)
p = places.save(mc.rcon, v["name"], at, at.dim or "overworld", mc.TASK)
result("saved %s at %d %d %d in %s" % (v["name"], p["pos"][0], p["pos"][1], p["pos"][2], p["dim"].split(":")[-1]),
       name=v["name"], pos=p["pos"], dim=p["dim"], by=p["by"])
''',
    "beam": '''"""Beam: logs its values every 0.1 s, so a test can see them change."""
import mc
mc.EXIT_GRACE = 0  # a stand-in sets nothing off that needs a tick to go
import json
import time

import mc
from kit import result, values

v = values()
if v["report"]:
    result("%s has a beam" % v["player"], player=v["player"])
while True:
    print(json.dumps(dict((k, v[k]) for k in v), sort_keys=True), flush=True)
    time.sleep(0.1)
''',
    "ping": '''"""Ping: asks who is online, then ends."""
import mc
mc.EXIT_GRACE = 0  # a stand-in sets nothing off that needs a tick to go
import sys
import time

import mc
from kit import result, values

v = values()
if v["early"]:
    result("%s: started" % v["say"])
time.sleep(v["nap"])
if v["fail"] == "refuse":
    result("%s: refused" % v["say"], refused=True)
    sys.exit(1)
if v["fail"] == "crash":
    mc.log("about to crash")
    no_such_name_here
online = mc.players()
result("%s: %d online" % (v["say"], len(online)), online=online, task=mc.TASK, copy=mc.INSTANCE)
''',
}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def install_types(repo):
    """What an action needs to load: actions/__init__.py, the real actions/_types.py, and what
    it reads: the one list of refused commands, scripts/_lib/commands.py, and kit/world.py's GONE."""
    write(os.path.join(repo, "actions", "__init__.py"), "")
    shutil.copy(os.path.join(REPO, "actions", "_types.py"), os.path.join(repo, "actions", "_types.py"))
    os.makedirs(os.path.join(repo, "scripts", "_lib", "kit"), exist_ok=True)
    for rel in ("commands.py", os.path.join("kit", "world.py")):
        shutil.copy(os.path.join(REAL_LIB, rel), os.path.join(repo, "scripts", "_lib", rel))


def install(repo, names=None):
    """The real scripts/_lib (mc, the kit, instances, places, commands), the real
    actions/_types.py, and the stand-ins."""
    lib = os.path.join(repo, "scripts", "_lib")
    shutil.rmtree(lib, ignore_errors=True)
    shutil.copytree(REAL_LIB, lib, ignore=shutil.ignore_patterns("__pycache__"))
    write(os.path.join(repo, "actions", "__init__.py"), "")
    shutil.copy(os.path.join(REPO, "actions", "_types.py"), os.path.join(repo, "actions", "_types.py"))
    for name, text in ACTIONS.items():
        if names is None or name in names:
            write(os.path.join(repo, "actions", name + ".py"), text)
    for name, text in SCRIPTS.items():
        if names is None or name in names:
            write(os.path.join(repo, "scripts", name, "main.py"), text)
