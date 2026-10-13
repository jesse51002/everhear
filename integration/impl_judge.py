#!/usr/bin/env python3
"""The checks of Prototype_Implementer_Harness's Test 2, "scored on whether it works", on what one
realmc server's implementers made (made/<server>/), after a replay or from the night of 2026-10-11:

1. the task's action exists, and `vfg action check <name>` passes (or, for an incantation bound
   to a library action, the binding is there);
2. a test cast runs with no error in `vfg script logs`: the incantation cast through the real
   bridge, as integration/incantations_e2e.py casts (POST /v1/casts), else `vfg action run`, then
   the fake player's click or crouch if the judge asks for one;
3. a judge model reads the ask, the DONE WHEN checklist, the script, its log and what two casts did,
   and marks each item done, not done or can't tell for each cast. An item is done only when both
   casts show it, and "flaky" when one does. Its own probes win over the implementer's words: an
   item whose probe failed in a cast is not done in that cast, and an item that rests on the
   implementer's report alone is not done.

Each cast starts from fresh state on a stage of its own, the geometry of builder B's test
templates: Steve and Alex taken off and spawned again (Steve joining first for the first cast,
Alex for the second, so a spell bound to whoever is first in @a shows up), Alex 4 blocks to
Steve's left, a husk at full health with no armor and no AI 10 blocks ahead on his eye line, a
3 x 3 x 3 stone wall 2 behind it, and real ground under them (9 x 9 grass on dirt, short grass to
burn), as kit/test.py's templates set it. Only husks and zombies are targets, never a golem or anything else the kit spares. The
cast is watched until it settles (Steve on the ground and still, the cast's copy ended), at least
as long as the checklist's times say (a 5 s charge: 8 s) and at most 20 s or that plus 12; the
effects of Steve and Alex are sampled throughout and kept at their peak. A second cast waits out
the incantation's cooldown. A research task, or a scenario marked not buildable, is excluded from
the total: its items are scored apart, done only on evidence that is not the implementer's own.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    integration/realmc/up ih-d1 --fresh && python3 integration/realmc/players.py --name ih-d1 spawn Steve
    cd brain && uv run python ../integration/impl_judge.py --name ih-d1 [--night] [--only T207,T212]
                  [--reports RUNS.json] [--label L] [--data RECORD.json --scenario] [--plans JUDGE.json]

--night first loads what Haiku made that night into made/<server>/ (the four made actions and
their scripts, from integration/replay/impl/night/, KrownJM made Steve) and its five
incantations into the stand-in's incantations.json; "hot fireball" is bound as the night left
it, with the values its first run bound (explosion_power 2, spread 4, breaks false), which no
later edit reached. Without --night the server's made/<server>/ and incantations are a replay's,
each task's action its name in tasks.json, and --reports gives the replay's reports. A research
task (T215) has no action: it is excluded, scored apart on its reports and code. --scenario takes
each game record's checklist from scenarios.json now; --plans uses an earlier judging's plans again.

Each cast's stage is away from the others: a 33 x 33 grass floor on stone at y 100, cleared
above, Steve at its middle facing north. Judge calls: google/gemini-3.8-flash at low reasoning,
two a task (the plan, the marks), component brain, ref impljudge,
queued in OUT/usage (never sent to a box). Writes OUT/judge-<label>.json and .md, OUT being
$VFG_IMPL_REPLAY (~/.vfg/impl-replay), outside the repository."""
import argparse
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
import time
from typing import Literal

from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REALMC = os.path.join(HERE, "realmc")
sys.path.insert(0, REALMC)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "lib", "py"))

import players  # noqa: E402
import realmc  # noqa: E402
from impl_briefs_write import load_key  # noqa: E402
from impl_data import DATA, ROOT as OUT  # noqa: E402
from incantations_e2e import Bridge, vfg_env  # noqa: E402

# What Haiku made on 2026-10-11 and the incantations the box held after (KrownJM is Steve there).
NIGHT_MADE = os.path.join(HERE, "replay", "impl", "night")
NIGHT_INCANTATIONS = os.path.join(NIGHT_MADE, "incantations-2026-10-12.json")
MODEL = "google/gemini-3.8-flash"
# Stage centres, each over 150 blocks from the next, all inside the template's generated 224 blocks.
STAGES = [(-150, -150), (150, -150), (-150, 150), (150, 150), (0, -190), (0, 190), (-190, 0), (190, 0),
          (-100, 0), (100, 0), (0, -100), (0, 100)]
FLOOR_Y = 100
# Builder B's test templates' geometry (kit/test.py), so the judge and the tests see the same stage:
# a target 10 blocks ahead on his eye line, a 3 x 3 x 3 wall 2 behind it, real ground under them
# (t.ground: 9 x 9 grass on 3 of dirt centred 10 ahead, short grass on it to burn), and Alex beside
# him, 4 blocks to his left.
TARGET_AHEAD, WALL_AHEAD, ALEX_LEFT, PAD_SIZE, PAD_DEPTH = 10, 12, 4, 9, 3
TARGET_KINDS = ("husk", "zombie")  # what the abilities target; never a golem, a villager, anything the kit spares
WATCH_LEAST_S, WATCH_MOST_S, WATCH_CAP_S = 2.0, 20.0, 45.0
SCRATCH_Y = -63  # where clone counts are written: the world's bottom, under the stage
NOT_BUILDABLE = ("research an implementer in the game cannot do: it has no web and reads only scripts/, actions/ "
                 "and made/, so any measurement it reports is its own; scored apart, outside the total")
# What the judge's own commands may start with; a kill must name an entity type.
ALLOWED = {"summon", "setblock", "fill", "execute", "data", "effect", "tp", "teleport", "give", "kill", "time",
           "weather", "scoreboard", "attribute", "clear", "item", "particle", "playsound", "say", "tellraw"}


# ------------------------------------------------------------------ the judge's shapes

class Probe(BaseModel):
    item: int = Field(description="the checklist item it tests, by its number from 1")
    label: str = Field(description="what it shows, in a few words")
    blocks: str = Field(description="for fire, ice, a crater or anything built, which are blocks and never entities: "
                                    "the block or block tag to count (fire, #minecraft:ice, air, obsidian, "
                                    "packed_ice), counted by the harness in a box round right/up/forward; '' when "
                                    "`command` is the probe")
    right: int = Field(description="with blocks: the box's middle, blocks to Steve's right from where he starts "
                                   "(negative: left)")
    up: int = Field(description="with blocks: the box's bottom, blocks up from his feet (-1 the floor; -4 a crater's "
                                "depth); the box is 4 high")
    forward: int = Field(description="with blocks: the box's middle, blocks ahead of where he starts")
    radius: int = Field(description="with blocks: half the box's width, 1 to 15")
    command: str = Field(description="with no blocks: one server command with no slash, run over RCON before and "
                                     "after each cast; relative to Steve as `execute at Steve if …`, or to the "
                                     "target as `execute at @e[tag=judge_target,limit=1] if …`; '' with blocks")
    expect: Literal["passes after", "fails after", "count rises", "count falls", "changes", "unchanged"] = Field(
        description="what its answer does when the item holds: after the cast it passes ('Test passed') or fails, "
                    "its number rises or falls against before the cast, or the answer changes or stays the same")


class Plan(BaseModel):
    setup: list[str] = Field(description="at most 3 server commands, no slash, run after the stage is set: blocks "
                                         "(fill, setblock), or another husk or zombie (summon); [] when the stage "
                                         "will do, as it mostly will")
    probes: list[Probe] = Field(description="at most 10, one or more per item a block, an entity count or a "
                                            "block under Steve can show; none for health, motion or effects, "
                                            "which are sampled for you")
    values: list[str] = Field(description="when no incantation fires it, the key=value words `vfg action run` "
                                          "gets: whom it is for and where, such as player=Steve or at=Steve; [] for "
                                          "player=Steve alone")
    actor: Literal["Steve", "Alex"] = Field(description="who uses the thing: the player it was made for")
    player_action: Literal["none", "right_click", "hold_right_click", "sneak_three_times"] = Field(
        description="what the actor does after the cast starts, for a thing used with an item or a crouch; none "
                    "for an incantation, which the cast itself fires, or a thing that acts on its own")
    look_pitch: float = Field(description="Steve's pitch while casting, degrees: 0 level (the husk is on his eye "
                                          "line), positive down, -30 up")
    watch_at_least: float = Field(description="seconds to watch at the least: the latest moment the checklist or "
                                              "the code says something happens (a 5 s charge, a crater carved after "
                                              "the blast, a slow fall), plus 3; 0 when nothing is delayed")


class Mark(BaseModel):
    item: str
    first: Literal["done", "not done", "can't tell"] = Field(description="by the first cast's evidence")
    second: Literal["done", "not done", "can't tell"] = Field(description="by the second cast's evidence")
    evidence: Literal["observed", "probe", "log of a measurement", "code", "report only", "none"] = Field(
        description="what the marks rest on: observed (the samples and counts the harness took), probe (your "
                    "probes), log of a measurement (the copy's log of something its code really measured), code "
                    "(only for what nothing can observe: a sound, a particle, a title), report only (the "
                    "implementer's words), none")
    why: str = Field(description="the evidence, in one sentence: the O or P line, the trace or the log line")


class Verdict(BaseModel):
    items: list[Mark] = Field(description="one per checklist item, in its order, its words kept")
    summary: str = Field(description="one or two sentences: what the casts did")


class ApartMark(BaseModel):
    item: str
    mark: Literal["done", "not done", "can't tell"]
    evidence: Literal["measured by its code", "constants or guesses", "its own claim", "none"] = Field(
        description="measured by its code: a number its code really measured, shown in a log or its output; "
                    "constants or guesses: numbers written into the code or the report and presented as "
                    "measured; its own claim: the report says so with nothing behind it")
    why: str


class Apart(BaseModel):
    items: list[ApartMark] = Field(description="one per checklist item, in its order, its words kept")
    summary: str


JUDGE_SYSTEM = (
    "You judge whether a Minecraft ability an AI implementer built works, on a real vanilla Java 26.3 server "
    "(Fabric with Carpet, whose fake players Steve and Alex stand in for people). You read what the player asked "
    "for, its DONE WHEN checklist, the action and script that were made, and the evidence of two casts, each from "
    "fresh state. Mark each item for each cast: done only when that cast's evidence shows it (an O line the "
    "harness measured, a P line of your own probe, a log line of something the code really measured); not done "
    "when the evidence shows it did not happen, or the thing cannot run; can't tell when nothing observable "
    "settles it. Your observations outrank the implementer's words and its log's claims: a probe that failed, a "
    "count of 0 ice, a Steve who died, decide the item, whatever the report says. Never mark done on the "
    "implementer's report alone. A research or report item is done only on a measurement its code really took; "
    "numbers written into the code or the report and presented as measured are not done. The fake players hear "
    "and see nothing, so a sound, a particle or a title is judged from the script's code and log alone (evidence "
    "code). Effects were sampled every half second and are given at their peak, so a short Blindness shows.")

PLAN_SYSTEM = (
    "You plan two fair test casts of a Minecraft ability an AI implementer built, on a real vanilla Java 26.3 "
    "server, so that a judge can tell, item by item, whether its DONE WHEN checklist holds. The stage is fixed, "
    "as the test templates set it: Steve, a fake player in survival at full health, stands at the middle of a "
    "flat 33 x 33 grass stage on stone at y %d, cleared above, facing north (-z); Alex, another fake player, "
    "stands 4 blocks to his left (west); a husk at full health with no armor and no AI stands %d blocks ahead "
    "(tag judge_target), a 3 x 3 x 3 stone wall %d ahead (2 behind it), and under them real ground: 9 x 9 grass "
    "on 3 of dirt centred on the husk, short grass on it to burn. The cast is fired for you: the incantation said by Steve, or "
    "the action run for Steve. The harness samples Steve's and Alex's position, motion, health and effects (at "
    "their peak) through the cast, the husk's health, the fire on the stage, and counts the wall's and the pad's "
    "blocks after; you add probes for the rest, each tied to the item it tests and what its answer does when the "
    "item holds. Fire, ice, a crater and anything built are blocks, never entities: probe them with `blocks` (a block "
    "or tag counted in a box round a spot, as the test kit's t.fire_near counts fire), never with @e (type=item counts "
    "dropped items). No probe for what is measured for you (the players' health, motion and effects, the husk's "
    "health, the wall's and the pad's blocks, fire, ice and obsidian on the stage, the block under Steve at the "
    "end); never an exact NBT number. A marker tagged judge_stage stands where Steve starts, facing north: aim "
    "at places from it with local coordinates, ^left ^up ^forward, so `execute at @e[tag=judge_stage,limit=1] if "
    "block ^ ^-1 ^5 ice` tests the floor 5 ahead and ^2 is 2 to his left; `execute at Steve` follows him and "
    "fails once he is dead. With ~ the axes are the world's: ahead is ~ ~ ~-5, and ~ ~ ~5 is behind him. Commands "
    "run over RCON with no slash: `execute at Steve if block ~ ~-1 ~ obsidian` (answers 'Test passed' or 'Test "
    "failed'), `execute at Steve if entity @e[type=zombie,distance=..40]` ('Test passed, count: N'), `data get "
    "entity Alex SelectedItem`. Only husks and zombies are targets: never summon a golem, a villager or any "
    "other mob. Never op, stop, ban, kick or change game rules." % (FLOOR_Y, TARGET_AHEAD, WALL_AHEAD))

# ------------------------------------------------------------------ the server

class Server:
    def __init__(self, name: str, made_server: str):
        self.name, self.made = name, made_server
        self.srv = realmc.Server(name)
        self.p = players.Players(name)
        self.env = vfg_env(name)
        self.env["VFG_SERVER"] = made_server

    def vfg(self, *words, timeout=120) -> tuple[int, str, str]:
        r = subprocess.run(["vfg", *words], env=self.env, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def cmd(self, command: str) -> str:
        try:
            return self.p.cmd(command).strip()
        except Exception as e:  # noqa: BLE001 — a probe's failure is evidence too
            return "(%s: %s)" % (type(e).__name__, e)

    def data(self, who: str, path: str) -> list[float]:
        out = self.cmd("data get entity %s %s" % (who, path))
        return [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?(?:E-?\d+)?)[dfDFb]?", out.split(":", 1)[-1])]

    def alive(self, who: str) -> bool:
        """In the world with health above 0: a fake player that just died can still be listed."""
        m = re.search(r"(-?\d+(?:\.\d+)?)f", self.cmd("data get entity %s Health" % who))
        return bool(m) and float(m.group(1)) > 0

    def ensure_players(self) -> None:
        """Steve and Alex online and alive: one listed but dead or gone is taken off, then spawned."""
        on = self.p.online()
        for who in ("Steve", "Alex"):
            if who in on and self.alive(who):
                continue
            if who in on:
                self.cmd("player %s kill" % who)
                end = time.time() + 5
                while who in self.p.online() and time.time() < end:
                    time.sleep(0.2)
            self.p.spawn(who)

    def fresh_players(self, order: tuple, at: dict) -> None:
        """Steve and Alex taken off and spawned again at `at`, facing north, in `order`: the first to
        join is the first in the online list and in @a. Each in survival at full health, fed, with
        nothing held and no effects."""
        for who in ("Steve", "Alex"):
            if who in self.p.online():
                self.p.kill(who)
        for who in order:
            self.p.spawn(who, at=at[who], facing=(180, 0))
        for who in order:
            for c in ("gamemode survival %s", "clear %s", "effect clear %s", "effect give %s instant_health 1 20 true",
                      "effect give %s saturation 1 20 true"):
                self.cmd(c % who)

    def steady(self, *who: str) -> None:
        """Wait until each player has stood on the ground for half a second: a player just spawned
        or teleported reads OnGround 0b for a tick or two, and an ability that throws only a player
        on the ground would find nobody (high step did, cast 0.1 s after a tp)."""
        end, since = time.monotonic() + 4.0, None
        while time.monotonic() < end:
            if all("1b" in self.cmd("data get entity %s OnGround" % w) for w in who):
                since = since or time.monotonic()
                if time.monotonic() - since >= 0.5:
                    return
            else:
                since = None
            time.sleep(0.1)

    def stage(self, i: int, order: tuple = ("Steve", "Alex"), respawn: bool = True) -> dict:
        """A fresh stage for cast i, builder B's templates' geometry: forceloaded, floored, cleared
        above; the ground pad, the wall and the husk placed; Steve and Alex spawned again in `order`
        (or, respawn False, only healed and moved, for a running copy whose item they hold)."""
        cx, cz = STAGES[i % len(STAGES)]
        x1, x2, z1, z2 = cx - 16, cx + 16, cz - 16, cz + 16
        y = FLOOR_Y + 1
        if not respawn:
            self.ensure_players()  # a test before may have killed one: a dead fake player leaves
        self.cmd("forceload add %d %d %d %d" % (x1, z1, x2, z2))
        time.sleep(1.0)
        self.cmd("execute positioned %d %d %d run kill @e[type=!player,distance=..80]" % (cx, FLOOR_Y, cz))
        self.cmd("fill %d %d %d %d %d %d stone" % (x1, FLOOR_Y - 8, z1, x2, FLOOR_Y - 1, z2))
        self.cmd("fill %d %d %d %d %d %d grass_block" % (x1, FLOOR_Y, z1, x2, FLOOR_Y, z2))
        for yy in range(y, y + 40, 20):
            self.cmd("fill %d %d %d %d %d %d air" % (x1, yy, z1, x2, yy + 19, z2))
        h = PAD_SIZE // 2
        pad = (cx - h, FLOOR_Y - PAD_DEPTH, cz - TARGET_AHEAD - h, cx + h, FLOOR_Y, cz - TARGET_AHEAD + h)
        self.cmd("fill %d %d %d %d %d %d dirt" % pad)
        self.cmd("fill %d %d %d %d %d %d grass_block" % (pad[0], FLOOR_Y, pad[2], pad[3], FLOOR_Y, pad[5]))
        wall = (cx - 1, y, cz - WALL_AHEAD - 1, cx + 1, y + 2, cz - WALL_AHEAD + 1)
        self.cmd("fill %d %d %d %d %d %d stone" % wall)
        self.cmd("fill %d %d %d %d %d %d short_grass replace air" % (pad[0], y, pad[2], pad[3], y, pad[5]))
        at = {"Steve": (cx + 0.5, y, cz + 0.5), "Alex": (cx + 0.5 - ALEX_LEFT, y, cz + 0.5)}
        if respawn:
            self.fresh_players(order, at)
        else:
            for who in ("Steve", "Alex"):
                for c in ("gamemode survival %s", "effect clear %s", "effect give %s instant_health 1 20 true"):
                    self.cmd(c % who)
                self.cmd("tp %s %g %d %g 180 0" % (who, at[who][0], y, at[who][2]))
        self.cmd('summon husk %g %d %g {NoAI:1b,PersistenceRequired:1b,Silent:1b,IsBaby:0b,CanPickUpLoot:0b,'
                 'Tags:["judge_target"]}' % (cx + 0.5, y, cz + 0.5 - TARGET_AHEAD))
        self.cmd('summon marker %g %d %g {Tags:["judge_stage"],Rotation:[180f,0f]}' % (cx + 0.5, y, cz + 0.5))
        bare_targets(self, cx, cz)
        self.steady("Steve", "Alex")
        self.cmd("execute positioned %d %d %d run kill @e[type=item,distance=..80]" % (cx, FLOOR_Y, cz))
        return {"cx": cx, "cz": cz, "y": y, "at": at, "pad": pad, "wall": wall, "order": list(order),
                "fire_box": (x1, FLOOR_Y, z1, x2, y + 5, z2), "pad_top": (pad[0], y, pad[2], pad[3], y, pad[5])}


def bare_targets(s: "Server", cx: int, cz: int) -> None:
    """Every husk and zombie on the stage armor-free and empty-handed, as the kit's targets are."""
    for kind in TARGET_KINDS:
        for slot in ("armor.head", "armor.chest", "armor.legs", "armor.feet", "weapon.mainhand", "weapon.offhand"):
            s.cmd("execute positioned %d %d %d as @e[type=%s,distance=..40] run item replace entity @s %s with air"
                  % (cx, FLOOR_Y, cz, kind, slot))


def safe(command: str, setup: bool = False) -> bool:
    """A command the judge's plan may run: an allowed verb, a kill that names a type, nothing that
    runs the server; in the setup, only blocks or a husk or zombie (never a golem or a villager)."""
    words = command.strip().lstrip("/").split()
    if not words or words[0] not in ALLOWED:
        return False
    if words[0] == "kill" and "type=" not in command:
        return False
    if setup:
        if words[0] not in ("fill", "setblock", "summon", "execute"):
            return False
        summoned = re.findall(r"\bsummon\s+(?:minecraft:)?([a-z_]+)", command)
        if any(k not in TARGET_KINDS for k in summoned):
            return False
        if words[0] == "execute" and not summoned and not re.search(r"\brun (fill|setblock)\b", command):
            return False
    return not re.search(r"\brun (op|deop|stop|ban|ban-ip|pardon|kick|whitelist|gamerule|save-off|reload)\b",
                         command)


# ------------------------------------------------------------------ watching a cast

def count(s: "Server", box: tuple, pred: str, dest_y: int = SCRATCH_Y) -> int:
    """How many blocks of `box` (x1 y1 z1 x2 y2 z2) match `pred`: cloned, filtered, to a scratch
    place under it, in bands of at most 32768 blocks; clone says how many it copied."""
    x1, y1, z1, x2, y2, z2 = box
    layer = (x2 - x1 + 1) * (z2 - z1 + 1)
    step = max(1, 32768 // layer)
    n = 0
    for y in range(y1, y2 + 1, step):
        top = min(y2, y + step - 1)
        out = s.cmd("clone %d %d %d %d %d %d %d %d %d filtered %s force" % (x1, y, z1, x2, top, z2, x1, dest_y, z1, pred))
        m = re.search(r"cloned (\d+)", out)
        n += int(m.group(1)) if m else 0
    return n


def kinds(s: "Server", box: tuple, preds: dict) -> dict:
    """{name: how many of box's blocks match its predicate}, and `other` for the rest."""
    x1, y1, z1, x2, y2, z2 = box
    out = {name: count(s, box, pred) for name, pred in preds.items()}
    out["other"] = (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1) - sum(out.values())
    return out


WALL_KINDS = {"gone": "air", "stone": "stone", "ice": "#minecraft:ice", "obsidian": "obsidian", "fire": "fire"}
PAD_KINDS = {"gone": "air", "grass": "grass_block", "dirt": "dirt", "ice": "#minecraft:ice", "obsidian": "obsidian"}


def top_level(text: str) -> list[str]:
    """The outermost {…} of a data answer, nested ones kept inside."""
    out, depth, start = [], 0, 0
    for n, ch in enumerate(text):
        if ch == "{":
            start = n if depth == 0 else start
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append(text[start:n + 1])
    return out


def effects_of(s: "Server", who: str) -> dict:
    """{effect: (level, seconds left)} that `who` has now; {} when he has none or is gone. An
    effect's compound can hold another (Blindness and Darkness keep their fog's factor data)."""
    out = {}
    for chunk in top_level(s.cmd("data get entity %s active_effects" % who)):
        m = re.search(r'(?<![a-z_])id: ?"minecraft:([a-z_]+)"', chunk)
        if not m:
            continue
        amp = re.search(r"(?<![a-z_])amplifier: ?(-?\d+)", chunk)
        dur = re.search(r"(?<![a-z_])duration: ?(-?\d+)", chunk)
        out[m.group(1)] = (int(amp.group(1)) + 1 if amp else 1, round(int(dur.group(1)) / 20.0, 1) if dur else 0.0)
    return out


def once_copies(s: "Server", name: str) -> int:
    """The task's copies that run once (a cast's) and are still running on the live stand-in: an
    item's listener, running until stopped, is not counted."""
    n = 0
    for meta_path in glob.glob(os.path.join(s.srv.vfg, "run", "*.json")):
        if meta_path.endswith(".result.json"):
            continue
        meta = read_json(meta_path) or {}
        if meta.get("runs") != "once" or not (meta.get("action") == name or meta.get("task") == name
                                               or str(meta.get("copy", "")).startswith(name)):
            continue
        if pid_alive(meta_path[:-5] + ".pid"):
            n += 1
    return n


def pid_alive(path: str) -> bool:
    try:
        with open(path) as f:
            os.kill(int(f.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def still_running(s: "Server", copy: str | None, name: str | None) -> bool:
    """The cast's copy still runs (its .pid alive), or, the copy not known, one of the task's once copies."""
    if copy:
        return pid_alive(os.path.join(s.srv.vfg, "run", copy + ".pid"))
    return bool(name) and once_copies(s, name) > 0


def times_in(texts: list[str], values: dict | None = None) -> float:
    """The latest moment the words or the bound values name, in seconds: "a 5-second fire buildup",
    "after 5 seconds", charge_seconds 5. 0 when none does."""
    found = [float(m.group(1)) for t in texts for m in re.finditer(
        r"(\d+(?:\.\d+)?)\s*-?\s*(?:seconds?|secs?|s)\b", t or "", re.I)]
    for k, v in (values or {}).items():
        if re.search(r"(seconds?|_s|delay|duration|fuse|charge)$", k) and isinstance(v, (int, float)) and 0 < v < 60:
            found.append(float(v))
    return max([x for x in found if x < 60] or [0.0])


def watch_window(texts: list[str], values: dict | None, plan_least: float = 0.0) -> tuple[float, float]:
    """(least, most) seconds to watch a cast: least the checklist's latest time plus 3 (or the plan's,
    if longer), most 20 s or least plus 12, up to 45."""
    t = times_in(texts, values)
    least = min(WATCH_CAP_S - 5, max(WATCH_LEAST_S, t + 3 if t else 0.0, plan_least or 0.0))
    return least, min(WATCH_CAP_S, max(WATCH_MOST_S, least + 12))


def watch(s: "Server", who: str, most: float, least: float = 0.0, copy: str | None = None, name: str | None = None,
          others: tuple = ("Alex",), target: str | None = None, fire_box: tuple | None = None) -> dict:
    """Watch a cast until it settles, at most `most` s: `who`'s position, motion and health about
    four times a second; every half second the effects of `who` and `others`, kept at their peak
    (the highest level and the longest time left seen, so a 2 s Blindness is not missed), their
    lowest health, and the target's; the fire in `fire_box` each second. It ends once `least` s
    have passed, the cast's copy no longer runs and `who` has stood on the ground, still, for 1.5 s;
    when `who` is gone (dead: a fake player leaves, on two reads running), the rest is still watched
    until the copy ends and `least` has passed: a blast after his death is still seen."""
    t0 = time.monotonic()
    trace, peak, low, gone = [], {w: {} for w in (who, *others)}, {}, {}
    fire, t_low, still_since, misses, next_slow, next_fire, ended = 0, None, None, 0, 0.0, 0.0, "at its limit"
    while True:
        t = time.monotonic() - t0
        if t >= most:
            break
        pos, mot, ground = [], [], False
        if who not in gone:
            raw = s.cmd("data get entity %s Pos" % who)
            if "No entity was found" in raw:
                misses += 1
                if misses >= 2:
                    gone[who] = round(t, 1)
                time.sleep(0.2)
                continue
            misses = 0
            pos = [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?(?:E-?\d+)?)d", raw.split(":", 1)[-1])][:3]
            mot = s.data(who, "Motion")[:3]
            hp = s.data(who, "Health")[:1]
            ground = "1b" in s.cmd("data get entity %s OnGround" % who)
            trace.append({"t": round(t, 2), "pos": [round(v, 2) for v in pos], "motion": [round(v, 3) for v in mot],
                          "health": hp[0] if hp else None})
        if t >= next_slow:
            next_slow = t + 0.5
            for w in (who, *others):
                for k, (level, left) in effects_of(s, w).items():
                    p = peak[w].setdefault(k, {"level": level, "seconds": left, "first": round(t, 1)})
                    p["level"], p["seconds"] = max(p["level"], level), max(p["seconds"], left)
            for w in others:
                h = s.data(w, "Health")[:1]
                if h:
                    low[w] = min(low.get(w, h[0]), h[0])
                elif w not in gone and "No entity was found" in s.cmd("data get entity %s Pos" % w):
                    gone[w] = round(t, 1)
            if target:
                h = s.data(target, "Health")[:1]
                t_low = 0.0 if not h else h[0] if t_low is None else min(t_low, h[0])
        if fire_box and t >= next_fire:
            next_fire = t + 1.0
            fire = max(fire, count(s, fire_box, "fire"))
        if who in gone:  # nothing more of him: the rest until the copy ends
            if t >= max(WATCH_LEAST_S, least) and not still_running(s, copy, name):
                ended = "%s gone (dead) at %.1f s; the rest watched until the copy ended" % (who, gone[who])
                break
            time.sleep(0.3)
            continue
        moving = len(mot) == 3 and (abs(mot[0]) > 0.02 or abs(mot[2]) > 0.02 or abs(mot[1] + 0.0784) > 0.02)
        if len(trace) >= 2 and trace[-2]["pos"] and pos:
            moving = moving or sum((a - b) ** 2 for a, b in zip(pos, trace[-2]["pos"])) > 0.01
        still_since = (still_since or time.monotonic()) if ground and not moving else None
        if (still_since and time.monotonic() - still_since >= 1.5 and t >= max(WATCH_LEAST_S, least)
                and not still_running(s, copy, name)):
            ended = "settled"
            break
        time.sleep(0.2)
    if who in gone:
        trace.append({"t": gone[who], "pos": [], "motion": [], "health": 0.0})
        ended = ended if ended != "at its limit" else "%s gone (dead) at %.1f s; the rest watched to the limit" % (
            who, gone[who])
    return {"trace": trace, "peak": peak, "lowest": low, "gone": gone, "fire": fire, "target_lowest": t_low,
            "seconds": round(time.monotonic() - t0, 1), "ended": ended}


def peak_words(peak: dict) -> str:
    return ", ".join("%s %d (up to %.0f s left, first at %.1f s)" % (k, v["level"], v["seconds"], v["first"])
                     for k, v in sorted(peak.items())) or "none"


# ------------------------------------------------------------------ the night's things

def load_night(s: Server) -> dict:
    """Haiku's made files into made/<server>/ (KrownJM made Steve), its bindings into the stand-in."""
    dest = os.path.join(REPO, "made", s.made)
    loaded = []
    for kind in ("actions", "scripts"):
        os.makedirs(os.path.join(dest, kind), exist_ok=True)
    for name in sorted(os.listdir(os.path.join(NIGHT_MADE, "actions"))):
        name = name[:-3]
        with open(os.path.join(NIGHT_MADE, "actions", name + ".py"), encoding="utf-8") as f:
            text = f.read().replace("KrownJM", "Steve")
        with open(os.path.join(dest, "actions", name + ".py"), "w", encoding="utf-8") as f:
            f.write(text)
        sd = os.path.join(dest, "scripts", name)
        os.makedirs(sd, exist_ok=True)
        with open(os.path.join(NIGHT_MADE, "scripts", name, "main.py"), encoding="utf-8") as f:
            text = f.read().replace("KrownJM", "Steve")
        with open(os.path.join(sd, "main.py"), "w", encoding="utf-8") as f:
            f.write(text)
        code, out, err = s.vfg("script", "deploy", name)
        loaded.append("%s (deploy %s)" % (name, "ok" if code == 0 else (err or out)[:120]))
    with open(NIGHT_INCANTATIONS, encoding="utf-8") as f:
        night = json.load(f)["incantations"]
    keep = []
    for e in night:
        e = json.loads(json.dumps(e).replace("KrownJM", "Steve").replace("krownjm.", "steve."))
        if e["phrase"] == "fire ball":  # bound the night after; the night's "hot fireball" is rebuilt below
            e.update(phrase="hot fireball", aliases=["hotfireball", "hot fire ball"], by="T207",
                     id="steve.hot_fireball", created="2026-10-11T05:55:25.077Z")
            e["values"].update(explosion_power=2.0, explosion_spread=4.0, explosion_breaks=False)
            e["values"].pop("fireball_damage", None)
            e.pop("asked", None)
        if e.get("server"):
            e["server"] = s.made
        keep.append(e)
    with open(os.path.join(s.srv.vfg, "incantations.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "incantations": keep}, f, indent=1)
    s.vfg("action", "list", "--json")
    return {"made": loaded, "incantations": [(e["phrase"], e["action"]) for e in keep]}


# ------------------------------------------------------------------ one task

def binding(s: Server, task: dict, action: str | None, night: bool) -> dict | None:
    try:
        with open(os.path.join(s.srv.vfg, "incantations.json"), encoding="utf-8") as f:
            entries = json.load(f).get("incantations") or []
    except (OSError, ValueError):
        return None
    mine = [e for e in entries if e.get("player") == "Steve"]
    want = task["night"].get("phrase") if night and task.get("night") else None
    by = task["id"] if night else task.get("name")
    for test in (lambda e: e.get("by") == by, lambda e: action and e.get("action") == action,
                 lambda e: want and e.get("phrase") == want):
        hit = [e for e in mine if test(e)]
        if hit:
            return hit[-1]
    return None


def read(path: str, most: int = 30000) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "(none)"
    return text if len(text) <= most else text[:most] + "\n… (%d more characters)" % (len(text) - most)


def read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def checklist(task: dict) -> list[str]:
    if task.get("final_checklist"):  # a scenario's whole goal (impl_game.py)
        return task["final_checklist"]
    for a in reversed(task["asks"]):
        b = a.get("brief") or {}
        if b.get("done_when_now"):
            return b["done_when_now"]
        if b.get("done_when"):
            return b["done_when"]
    return []


def asked(task: dict) -> str:
    out = []
    for a in task["asks"]:
        words = " / ".join(x["text"] for x in a["lines"]) or "(the brain's own, after a report)"
        b = (a.get("brief") or {}).get("text") or a.get("orchestrator") or ""
        out.append("%s %d: the player: %s\n   the brief: %s" % (a["kind"], a["n"], words, " ".join(b.split())[:900]))
    return "\n".join(out)


def said_lines(task: dict) -> str:
    """What the player said, ask by ask, without the briefs."""
    return "\n".join("%s %d: %s" % (a["kind"], a["n"], " / ".join(x["text"] for x in a["lines"]) or "(after a report)")
                     for a in task["asks"])


SEEN_BY_CODE = re.compile(r"title|tellraw|actionbar|bossbar|particle|playsound|sound\(|_say\b|say\(|countdown|"
                          r"effect give|blindness|darkness|\btp\b|teleport", re.I)


def excerpt(text: str, most: int = 7000) -> str:
    """A script for the marks: its first 30 lines, and every line (with its neighbours) that shows a
    player what no probe sees, a title, chat, a particle, a sound, an effect given, a teleport, by
    line number."""
    lines = text.splitlines()
    keep = set(range(min(30, len(lines))))
    for n, ln in enumerate(lines):
        if SEEN_BY_CODE.search(ln) and not ln.lstrip().startswith("#"):
            keep.update((n - 1, n, n + 1))
    out, last = [], -2
    for n in sorted(k for k in keep if 0 <= k < len(lines)):
        if n != last + 1:
            out.append("   …")
        out.append("%4d %s" % (n + 1, lines[n]))
        last = n
    joined = "\n".join(out)
    return joined if len(joined) <= most else joined[:most] + "\n… (cut)"


PLANS: dict = {}  # --plans: a task's plan from an earlier judging, used again with no plan call


async def call(prompt: str, output, system: str, spent: list):
    from vfg_llm import LlmError, structured
    try:
        ans = await structured(prompt, output, component="brain", call_id="impljudge", system=system, model=MODEL,
                               reasoning="low", timeout=120)
    except LlmError as e:
        spent.append(e.cost)
        raise
    spent.append(ans.cost)
    return ans.value


def trace_summary(trace: list[dict]) -> str:
    if not trace or not trace[0]["pos"]:
        return "no trace (Steve not found)"
    gone = not trace[-1]["pos"]
    hps = [t["health"] for t in trace if t["health"] is not None]
    trace = [t for t in trace if t["pos"]]
    p0 = trace[0]["pos"]
    ys = [t["pos"][1] for t in trace]
    far = max(((t["pos"][0] - p0[0]) ** 2 + (t["pos"][2] - p0[2]) ** 2) ** 0.5 for t in trace)
    fast = max((sum(v * v for v in t["motion"]) ** 0.5 for t in trace if len(t["motion"]) == 3), default=0)
    return ("start %s; highest y %.1f (%+.1f), lowest %.1f; farthest %.1f blocks across; fastest motion %.2f "
            "blocks a tick; health %s to %s, lowest %s; end %s" % (
                p0, max(ys), max(ys) - p0[1], min(ys), far, fast, hps[0] if hps else "?", hps[-1] if hps else "?",
                min(hps) if hps else "?", "gone (dead)" if gone else trace[-1]["pos"]))


RUN_RE = re.compile(r"vfg action run (\S+)(.*?)(?:;|&&|\||$)")


def commands(log_lines, name) -> list[str]:
    """The shell commands the task's implementer sent, from the replay's log."""
    if not log_lines or not name:
        return []
    out = []
    for ln in log_lines:
        m = re.match(r"^\S+ implementer %s Bash (\{.*)$" % re.escape(name), ln)
        if m:
            try:
                out.append(json.loads(m.group(1))["command"])
            except (ValueError, KeyError):
                out.append(m.group(1)[:400])
    return out


def last_copy(s: "Server", name) -> dict | None:
    """The task's last copy of a ready-made action, from the stand-in's run/<copy>.json (what the box
    started: its action, script, source and every value), as an incantation entry the bridge can cast
    again: a place given as coordinates becomes Steve, so it lands on the stage."""
    best = None
    for path in glob.glob(os.path.join(s.srv.vfg, "run", "*.json")):
        if path.endswith(".result.json"):
            continue
        meta = read_json(path)
        if not isinstance(meta, dict) or meta.get("task") != name or str(meta.get("action", "")).startswith(
                ("incantation", "edit", "stop")):
            continue
        if best is None or (meta.get("started") or "") > (best.get("started") or ""):
            best = meta
    if best is None:
        return None
    values = {k: ("Steve" if re.match(r"^(at|\w+_at)$", k) and isinstance(v, str)
                  and re.match(r"^-?[\d.]+ -?[\d.]+ -?[\d.]+$", v.strip()) else v)
              for k, v in (best.get("values") or {}).items()}
    return {"player": "Steve", "phrase": "judge rerun %s" % name.replace("_", " "), "aliases": [],
            "action": best.get("action"), "script": best.get("script") or best.get("action"),
            "source": best.get("source") or "library", "server": best.get("server") or "", "values": values,
            "live": best.get("live") or [], "runs": best.get("runs") or "once", "caster_fields": [],
            "cooldown_s": 0, "by": "judge", "id": "steve.judge_rerun", "copy": best.get("copy")}


def add_binding(s: "Server", entry: dict | None) -> None:
    """The judge's own entry in the stand-in's incantations.json (replacing an earlier one), or it removed."""
    path = os.path.join(s.srv.vfg, "incantations.json")
    doc = read_json(path) or {"version": 0, "incantations": []}
    keep = [e for e in doc.get("incantations") or [] if e.get("by") != "judge"]
    if entry:
        keep.append({k: v for k, v in entry.items() if k != "copy"})
    doc.update(version=int(doc.get("version") or 0) + 1, incantations=keep)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(path + ".tmp", path)


class OneMark(BaseModel):
    item: str
    mark: Literal["done", "not done", "can't tell"]
    evidence: Literal["observed", "probe", "log of a measurement", "code", "report only", "none"]
    why: str = Field(description="the evidence, in one sentence")


class OneVerdict(BaseModel):
    items: list[OneMark] = Field(description="one per checklist item, in its order, its words kept")
    summary: str


APART_SYSTEM = (
    "You score research an AI implementer did for a Minecraft voice-ability product, inside the game's toolkit: "
    "it has no web access and can read only its scripts, actions and made files, so anything it reports about "
    "models, latency, accuracy or price is either measured by code it ran or its own claim. Mark each checklist "
    "item done only when the item's numbers were measured by code it really ran (a timing its code took, shown in "
    "a log or its output); not done when they are constants written into the code or the report and presented as "
    "measured, guesses, or claims with nothing behind them; can't tell when you cannot tell which.")


async def judge_world(s: Server, task: dict, items: list, report: str, said: list, out: dict, spent: list) -> dict:
    """Nothing made and no ready-made action run: the commands the implementer ran, and the world now."""
    plan = await call(
        "THE ASKS, in order:\n%s\n\nTHE CHECKLIST:\n%s\n\nNOTHING WAS MADE AND NO ACTION RUN: the implementer ran "
        "these commands, and what they did is in the world now, wherever Steve and Alex are (not on a stage):\n%s\n\n"
        "Plan only probes of the world as it is now: no setup, nothing is cast (player_action none)." % (
            asked(task), numbered(items), "\n".join("$ " + c[:300] for c in said[-25:])),
        Plan, PLAN_SYSTEM, spent)
    at = [int(v // 1) for v in s.data("Steve", "Pos")[:3]] or [0, 85, 0]
    now = ["%s: %r" % (pr.label, answer(s, pr, tuple(at))[:400]) for pr in plan.probes[:10]
           if (pr.blocks or "").strip() or safe(pr.command)]
    fixed = [("Steve's position", "data get entity Steve Pos"), ("Alex's position", "data get entity Alex Pos"),
             ("Steve's items", "data get entity Steve Inventory"), ("Alex's items", "data get entity Alex Inventory")]
    now += ["%s: %r" % (label, ask(s, c)[:400]) for label, c in fixed]
    code, state, _ = s.vfg("state", "--json")
    v = await call(
        "THE ASKS, in order:\n%s\n\nTHE CHECKLIST:\n%s\n\nTHE IMPLEMENTER'S LAST REPORT:\n%s\n\nNOTHING WAS MADE: "
        "the commands it ran:\n%s\n\nTHE WORLD NOW, probed:\n%s\n\nTHE SERVER (vfg state --json):\n%s\n\n"
        "Mark each checklist item." % (
            asked(task), numbered(items), (report or "(none)")[:4000],
            "\n".join("$ " + c[:300] for c in said[-25:]), "\n".join(now), (state or "")[:3000]),
        OneVerdict, JUDGE_SYSTEM, spent)
    marks = []
    for m in v.items:
        d = m.model_dump()
        if d["mark"] == "done" and d["evidence"] in ("report only", "none"):
            d.update(mark="not done", why="only the implementer's word: " + d["why"])
        marks.append(d)
    out.update(exists=False, check=None, cast={"by": "commands only", "commands": len(said)}, items=marks,
               summary=v.summary, probes=now)
    return out


def numbered(items: list) -> str:
    return "\n".join("%d. %s" % (n, x) for n, x in enumerate(items, 1))


def answer_value(ans: str) -> float | None:
    """The number an answer gives: 0 for a test that failed or nothing found, a count, a block
    count, 1 for a bare pass, else the first number after its colon."""
    if re.search(r"Test failed|No entity was found|Found no elements", ans):
        return 0.0
    for pat in (r"[Cc]ount:? (\d+)", r"\b(?:cloned|filled|placed) (\d+)"):
        m = re.search(pat, ans)
        if m:
            return float(m.group(1))
    if "Test passed" in ans:
        return 1.0
    m = re.search(r"(-?\d+(?:\.\d+)?)", ans.split(":", 1)[1]) if ":" in ans else None
    return float(m.group(1)) if m else None


BROKEN = re.compile(r"Unknown or incomplete|Incorrect argument|<--\[HERE\]|Expected |Invalid |^\(\w+(Error|Exception)")


def answer(s: Server, p: "Probe", origin: tuple) -> str:
    """A probe's answer: a count of blocks in its box round `origin` (Steve's start, facing north),
    as the kit's t.fire_near counts fire, 'Test passed, count: N' or 'Test failed'; else its command's."""
    if not (p.blocks or "").strip():
        return ask(s, p.command)
    pred = p.blocks.strip().replace("minecraft:", "")
    pred = pred if pred.startswith("#") else "minecraft:" + pred if ":" not in pred else pred
    if pred == "#ice":
        pred = "#minecraft:ice"
    x, y, z = origin
    r = max(1, min(15, int(p.radius or 4)))
    box = (x + p.right - r, y + p.up, z - p.forward - r, x + p.right + r, y + p.up + 3, z - p.forward + r)
    n = count(s, box, pred)
    return ("Test passed, count: %d" % n) if n else "Test failed"


def ask(s: Server, command: str) -> str:
    """A probe's answer. A conditional execute with no `run` that fails answers nothing over RCON on
    26.3 for a block test (an entity test says 'Test failed'): that silence is 'Test failed'."""
    out = s.cmd(command)
    if not out and re.match(r"execute\b", command.strip()) and " run " not in command and re.search(
            r"\b(if|unless)\b", command):
        return "Test failed"
    return out


def probe_ok(expect: str, before: str, after: str) -> bool | None:
    """Whether a probe shows its item held in a cast: True, False, or None when its answer cannot
    say (a broken command, no number to compare, or a test already in its after state before the
    cast, which cannot tell anything)."""
    if BROKEN.search(after or "") or BROKEN.search(before or ""):
        return None
    passed, failed = "Test passed" in after, "Test failed" in after
    if (expect == "passes after" and "Test passed" in before) or (expect == "fails after" and "Test failed" in before):
        return None
    if expect == "passes after":
        return True if passed else False if failed else None
    if expect == "fails after":
        return True if failed else False if passed else None
    if expect == "changes":
        return before != after
    if expect == "unchanged":
        return before == after
    vb, va = answer_value(before), answer_value(after)
    if vb is None or va is None:
        return None
    return va > vb if expect == "count rises" else va < vb


TRIPLE = re.compile(r"(?<!\S)~(-?[\d.]+)?\s+~(-?[\d.]+)?\s+~(-?[\d.]+)?(?!\S)")


BLOCKY = re.compile(r"\b(fire|flames?|burn\w*|ice|icy|frozen|freez\w*|crater|hole|carv\w*|blocks?|ground|platform|"
                    r"obsidian|walls?|built|build\w*|tunnel|terrain)\b", re.I)
NOT_MOBS = ("item", "experience_orb", "block_display", "item_display", "text_display", "marker", "area_effect_cloud",
            "interaction", "armor_stand")


def cannot_say(command: str, label: str = "") -> str:
    """Why a probe cannot tell anything whatever it answers: entities counted for a thing that is
    blocks (fire, ice, a crater, something built: `@e[type=item]` counts dropped items, not fire),
    a place behind Steve by world axes (he faces north, -z, so `~ ~-1 ~5` is 5 behind him, where
    nothing was put: a plan that meant ahead), or an exact NBT number (TicksFrozen:100 matches 100
    alone). '' when it can."""
    kinds = re.findall(r"@e\[[^\]]*\btype=(?:minecraft:)?([a-z_]+)", command)
    if BLOCKY.search(label or "") and kinds and all(k in NOT_MOBS for k in kinds):
        return "counts %s entities for a thing that is blocks: fire, ice and craters are counted as blocks" % (
            "/".join(kinds))
    for m in TRIPLE.finditer(command):
        if m.group(3) and float(m.group(3)) > 0:
            return "aimed behind him (%s: he faces -z; ahead is ^ ^ ^N or ~ ~ ~-N)" % m.group(0)
    if re.search(r"nbt=\{[^}]*:\s*-?\d", command):
        return "an exact NBT number matches that one value alone"
    return ""


def mobs_near(s: Server, who: str, r: int = 40) -> int:
    m = re.search(r"[Cc]ount:? (\d+)", s.cmd("execute at %s if entity @e[type=!player,type=!item,distance=..%d]" % (who, r)))
    return int(m.group(1)) if m else 0


def under(s: Server, who: str) -> str:
    for name in ("obsidian", "packed_ice", "blue_ice", "ice", "grass_block", "dirt", "oak_planks", "stone", "air",
                 "fire", "magma_block", "barrier"):
        if s.cmd("execute at %s if block ~ ~-1 ~ %s" % (who, name)).startswith("Test passed"):
            return name
    return "something else"


async def cast_once(s: Server, bridge, phrase: str, retries: int = 2) -> tuple:
    """Steve says the phrase through the bridge; refused for its cooldown, the cooldown is waited out
    and the cast tried again."""
    for _ in range(retries + 1):
        now = time.time()
        code, ans = bridge.cast("Steve", phrase, now - 0.6, now - 0.1)
        left = re.search(r"([\d.]+) s left", str((ans or {}).get("error") or ""))
        if (ans or {}).get("status") == "cooldown" and left:
            await asyncio.sleep(min(60.0, float(left.group(1)) + 0.5))
            continue
        if (ans or {}).get("status") == "too_soon" or "gap" in str((ans or {}).get("error") or ""):
            await asyncio.sleep(1.2)
            continue
        break
    return code, ans


async def one_cast(s: Server, bridge, k: int, i: int, plan: Plan, bind: dict | None, running: str | None,
                   action: str, window: tuple) -> dict:
    """Cast k of a task (0 or 1) from fresh state: the stage, the plan's setup, the probes before,
    the cast, the watch, the probes and counts after. Returns the evidence."""
    order = ("Steve", "Alex") if k == 0 else ("Alex", "Steve")
    st = s.stage(2 * i + k, order=order, respawn=not running)
    cx, cz, y = st["cx"], st["cz"], st["y"]
    ran = [{"cmd": c, "said": s.cmd(c)[:200] if safe(c, setup=True) else "(refused: blocks, husks or zombies only)"}
           for c in plan.setup[:3]]
    if ran:
        bare_targets(s, cx, cz)
    if plan.look_pitch:
        s.cmd("tp Steve %g %d %g 180 %g" % (cx + 0.5, y, cz + 0.5, max(-60, min(60, plan.look_pitch))))
        s.steady("Steve")
    probes = [p for p in plan.probes[:10] if (p.blocks or "").strip() or safe(p.command)]
    origin = (cx, y, cz)
    before = {n: answer(s, p, origin) for n, p in enumerate(probes)}
    target = "@e[tag=judge_target,limit=1]"
    t0_hp = (s.data(target, "Health")[:1] or [None])[0]
    alex0 = (s.data("Alex", "Health")[:1] or [None])[0]
    alex_at = s.data("Alex", "Pos")[:3]
    mobs0 = mobs_near(s, "@e[tag=judge_stage,limit=1]")
    copy, cast = None, {}
    if bind:
        code, ans = await cast_once(s, bridge, bind["phrase"])
        cast = {"by": "incantation", "phrase": bind["phrase"], "http": code, "answer": ans}
        copy = (ans or {}).get("copy")
    elif running:
        copy, cast = running, {"by": "the running copy", "copy": running}
    else:
        words = [w for w in plan.values if re.match(r"^[a-z_][a-z0-9_.]*=\S", w) and not w.startswith("as=")] or [
            "player=Steve"]
        code, so, se = s.vfg("action", "run", action, *words, "as=judge_%s" % action[:20], "--task", "judge")
        cast = {"by": "vfg action run", "values": words, "exit": code, "said": (so or se)[-400:]}
        try:
            copy = json.loads(so.splitlines()[-1]).get("copy")
        except (ValueError, IndexError):
            copy = None
    if plan.player_action != "none":
        await asyncio.sleep(1.0)
        who = plan.actor
        if plan.player_action == "right_click":
            s.p.use(who)
        elif plan.player_action == "hold_right_click":
            s.p.hold(who)
        else:
            for _ in range(3):
                s.p.sneak(who)
                time.sleep(0.15)
                s.p.sneak(who, False)
                time.sleep(0.15)
    least, most = window
    w = await asyncio.to_thread(watch, s, "Steve", most, least, None if running else copy, action, ("Alex",), target,
                                st["fire_box"])
    if plan.player_action == "hold_right_click":
        s.p.stop(plan.actor)
    after = {n: answer(s, p, origin) for n, p in enumerate(probes)}
    t1_hp = (s.data(target, "Health")[:1] or [None])[0]
    alive = "Steve" not in w["gone"] and "No entity was found" not in s.cmd("data get entity Steve Pos")
    alex1 = (s.data("Alex", "Health")[:1] or [None])[0]
    alex_now = s.data("Alex", "Pos")[:3]
    wall = kinds(s, st["wall"], WALL_KINDS)
    pad = kinds(s, st["pad"], PAD_KINDS)
    pad_fire = count(s, st["pad_top"], "fire")
    stage_ice, stage_obsidian = count(s, st["fire_box"], "#minecraft:ice"), count(s, st["fire_box"], "obsidian")
    mobs1 = mobs_near(s, "@e[tag=judge_stage,limit=1]")
    gone_now = [who for who in ("Steve", "Alex") if who in w["gone"] or "No entity was found" in s.cmd(
        "data get entity %s Pos" % who)]
    results = []
    for n, p in enumerate(probes):
        what = ("count %s, %d right %d up %d ahead, radius %d" % (p.blocks, p.right, p.up, p.forward, p.radius)
                if (p.blocks or "").strip() else p.command)
        r = {"item": p.item, "label": p.label, "command": what, "expect": p.expect,
             "before": before[n][:160], "after": after[n][:160], "ok": probe_ok(p.expect, before[n], after[n])}
        who = [x for x in gone_now if re.search(r"\b%s\b" % x, p.command)] if not (p.blocks or "").strip() else []
        why = ("%s was gone (dead) when it was read" % who[0]) if who else (
            "" if (p.blocks or "").strip() else cannot_say(p.command, p.label))
        if why and r["ok"] is not None:
            r.update(ok=None, note=why)
        results.append(r)
    held = s.cmd("data get entity Steve SelectedItem") if alive else ""
    item = re.search(r'id: ?"minecraft:([a-z_]+)"', held)
    still = bool(copy) and not running and still_running(s, copy, None)
    logs = ""
    if copy:
        code, so, se = s.vfg("script", "logs", copy, "-n", "40")
        logs = so or se
    errors = [ln for ln in logs.splitlines() if re.search(r"Traceback|Error|error|Exception|failed", ln)]
    a_low = w["lowest"].get("Alex")
    alex_moved = (sum((p - q) ** 2 for p, q in zip(alex_at, alex_now)) ** 0.5) if len(alex_at) == 3 == len(
        alex_now) else None
    obs = [
        "O1 Steve: %s%s." % (trace_summary(w["trace"]), "; he died (a fake player leaves) at %.1f s" % w["gone"]["Steve"]
                             if "Steve" in w["gone"] else ""),
        "O2 Steve's effects, sampled every 0.5 s, at their peak: %s." % peak_words(w["peak"]["Steve"]),
        "O3 Alex, %d blocks to his left: health %s, lowest %s, end %s%s; moved %s blocks; effects at their peak: %s." % (
            ALEX_LEFT, alex0, a_low, alex1 if alex1 is not None else "-", "; he died at %.1f s" % w["gone"]["Alex"]
            if "Alex" in w["gone"] else "", "%.1f" % alex_moved if alex_moved is not None else "?",
            peak_words(w["peak"]["Alex"])),
        "O4 The husk %d ahead (full health, no armor, no AI): health %s, lowest %s, end %s." % (
            TARGET_AHEAD, t0_hp, w["target_lowest"], "killed or gone" if t1_hp is None else t1_hp),
        "O5 The 3 x 3 x 3 stone wall %d ahead (27 blocks), after: %s." % (
            WALL_AHEAD, ", ".join("%s %d" % kv for kv in wall.items())),
        "O6 The ground, %d x %d grass on %d of dirt centred %d ahead (%d blocks: %d grass, %d dirt), after: %s; "
        "fire on it now %d." % (PAD_SIZE, PAD_SIZE, PAD_DEPTH, TARGET_AHEAD, PAD_SIZE ** 2 * (PAD_DEPTH + 1),
                                 PAD_SIZE ** 2, PAD_SIZE ** 2 * PAD_DEPTH, ", ".join("%s %d" % kv for kv in pad.items()),
                                 pad_fire),
        "O7 The whole stage (33 x 33, floor to 5 up): fire blocks, counted each second, most %d; after, ice %d, "
        "obsidian %d." % (w["fire"], stage_ice, stage_obsidian),
        "O8 Mobs within 40 of where Steve stood: before %d, after %d." % (mobs0, mobs1),
        "O9 At the end Steve stands on %s and holds %s." % (under(s, "Steve") if alive else "- (gone)",
                                                           item.group(1) if item else "nothing"),
        "O10 The watch: %.1f s (at least %.0f, at most %.0f), ended %s; the cast's copy %s." % (
            w["seconds"], least, most, w["ended"], "still running" if still else "ended" if copy else "unknown"),
    ]
    if copy and not running:
        s.vfg("script", "stop", copy)
    s.cmd("execute positioned %d %d %d run kill @e[type=!player,distance=..80]" % (cx, FLOOR_Y, cz))
    return {"k": k + 1, "joined": list(order), "stage": [cx, cz], "setup": ran, "cast": cast, "copy": copy,
            "watch": {"seconds": w["seconds"], "ended": w["ended"], "least": least, "most": most},
            "obs": obs, "probes": results, "log": logs[-2500:], "log_errors": errors[:6]}


def cast_words(c: dict) -> str:
    probes = "\n".join("P%d [item %d] %s: `%s` before %r, after %r: %s%s" % (
        n, p["item"], p["label"], p["command"][:120], p["before"][:100], p["after"][:100],
        {True: "PASSED", False: "FAILED", None: "unclear"}[p["ok"]], " (%s)" % p["note"] if p.get("note") else "")
        for n, p in enumerate(c["probes"], 1))
    return ("CAST %d (joined: %s, so %s is first in @a; a fresh stage): %s\n%s\nYOUR PROBES (expect: %s):\n%s\n"
            "THE COPY'S LOG (vfg script logs %s -n 40):\n%s" % (
                c["k"], " then ".join(c["joined"]), c["joined"][0], json.dumps(c["cast"])[:600], "\n".join(c["obs"]),
                "what its answer does when the item holds", probes or "(none)", c["copy"], c["log"] or "(no log)"))


def final_marks(items: list, v: Verdict, casts: list) -> list:
    """Each item's mark from both casts: a cast whose probe for the item failed is not done in that
    cast, an item resting on the report alone is not done; done in both is done, in one is flaky."""
    out = []
    for n, x in enumerate(items, 1):
        m = v.items[n - 1] if n - 1 < len(v.items) else None
        marks = [m.first, m.second] if m else ["can't tell", "can't tell"]
        notes = []
        for k in range(2):
            c = casts[k] if k < len(casts) else None
            if c is None:
                marks[k] = "can't tell"
                continue
            failed = [p for p in c["probes"] if p["item"] == n and p["ok"] is False]
            if failed and marks[k] == "done":
                marks[k] = "not done"
                notes.append("cast %d: the probe '%s' failed (%s, then %s)" % (
                    k + 1, failed[0]["label"], failed[0]["before"][:60], failed[0]["after"][:60]))
        if m and m.evidence in ("report only", "none") and "done" in marks:
            marks = ["not done" if x2 == "done" else x2 for x2 in marks]
            notes.append("only the implementer's word")
        a, b = marks
        mark = a if a == b else "flaky" if "done" in marks else "not done" if "not done" in marks else "can't tell"
        why = (m.why if m else "no mark given") + ("; " + "; ".join(notes) if notes else "")
        out.append({"item": x, "mark": mark, "casts": marks, "evidence": m.evidence if m else "none", "why": why})
    return out


async def judge_apart(s: Server, task: dict, items: list, reports: dict, night: bool, out: dict, spent: list) -> dict:
    """A research task, or a scenario not buildable in the game: excluded from the total, its items
    scored apart on its reports and whatever code it ran, done only on what its code measured."""
    why = task.get("not_buildable") or NOT_BUILDABLE
    all_reports = reports.get(task["id"] + ":all") or "\n\n".join(
        "run %d: %s" % (r["n"], r["report"]) for r in (task.get("night") or {}).get("runs") or [])
    code = ""
    for path in sorted(glob.glob(os.path.join(REPO, "made", s.made, "scripts", "*", "main.py")))[:3]:
        code += "\n--- %s\n%s" % (os.path.relpath(path, REPO), read(path, 6000))
    v = await call("THE ASKS:\n%s\n\nTHE CHECKLIST:\n%s\n\nITS REPORTS:\n%s\n\nTHE CODE IT MADE:\n%s" % (
        asked(task), numbered(items), all_reports[-8000:], code[:12000] or "(none)"), Apart, APART_SYSTEM, spent)
    apart = []
    for m in v.items:
        d = m.model_dump()
        if d["mark"] == "done" and d["evidence"] != "measured by its code":
            d.update(mark="not done", why="%s: %s" % (d["evidence"], d["why"]))
        apart.append(d)
    out.update(exists=None, check=None, cast=None, excluded=why, apart=apart, summary=v.summary,
               items=[{"item": x, "mark": "excluded", "why": why} for x in items])
    return out


async def judge_task(s: Server, bridge, task: dict, i: int, night: bool, reports: dict, spent: list) -> dict:
    kind = task["kind"]
    items = checklist(task)
    out = {"id": task["id"], "name": task.get("name"), "kind": kind, "checklist": items}
    report = reports.get(task["id"]) or (task["night"]["runs"][-1]["report"] if night and task["night"].get("runs")
                                         else "")
    if kind == "research" or task.get("buildable") is False:
        return await judge_apart(s, task, items, reports, night, out, spent)
    action = (task["night"].get("action") if night else task.get("name"))
    made_action = os.path.join(REPO, "made", s.made, "actions", "%s.py" % action) if action else None
    exists = bool(made_action and os.path.exists(made_action))
    bind = binding(s, task, action if exists else None, night)
    if not exists and bind:
        action = bind["action"]  # an incantation on a library action (high step: command)
    running = None
    if not exists and not bind:  # a change to a running copy: the task's, or the copy its setup started
        copies = read_json(os.path.join(s.env["VFG_HOME"], "running.json")) or {}
        mine = {c[4].split("=", 1)[1] for c in task.get("setup") or [] if len(c) > 4 and c[4].startswith("as=")}
        hit = [n for n, c in copies.items() if isinstance(c, dict) and (c.get("task") == task.get("name") or n in mine)]
        if hit:
            running = hit[-1]
            action = copies[running].get("action") or copies[running].get("script")
    out["action"], out["binding"], out["copy"] = action, (bind or {}).get("phrase"), running
    out["exists"] = exists or bool(bind) or bool(running)
    check = None
    if exists:
        code, so, se = s.vfg("action", "check", action)
        check = {"ok": code == 0, "said": (so or se).splitlines()[-1][:300] if (so or se) else ""}
    out["check"] = check
    rerun = None
    if not out["exists"]:  # a ready-made action run once (an asteroid, a horde): that copy's run, again
        rerun = last_copy(s, task.get("name"))
        said = commands(reports.get(":log"), task.get("name"))
        if rerun:
            bind = rerun
            action, out["action"], out["exists"], out["rerun"] = rerun["action"], rerun["action"], True, rerun["copy"]
        elif said:  # commands only (vfg cmd): judged on what it ran and on the world as it is now
            return await judge_world(s, task, items, report, said, out, spent)
    if not out["exists"]:
        out.update(cast=None, items=[{"item": x, "mark": "not done", "why": "nothing was made"} for x in items],
                   summary="no action, no incantation, no running copy and no command for this task")
        return out
    script = os.path.join(REPO, "made", s.made, "scripts", action, "main.py")
    if not os.path.exists(script):
        script = os.path.join(REPO, "scripts", action, "main.py")
    code_text = read(made_action) if exists else read(os.path.join(REPO, "actions", "%s.py" % action), 6000)
    if rerun:
        add_binding(s, rerun)
        time.sleep(1.0)  # the bridge reads the store again when it changes
    if bind:  # a cast runs only what the box has: its script deployed first, a library one too
        code, so, se = s.vfg("script", "deploy", bind.get("script") or action)
        if code != 0:
            out["deploy"] = (se or so)[-300:]
    how = ("none of the player's: the judge runs the implementer's copy %s of %s again, with the values it ran with, "
           "through an incantation of the judge's own: %s" % (rerun["copy"], rerun["action"], json.dumps(rerun))
           if rerun else json.dumps(bind) if bind else "none: the copy %s already runs; nothing is cast, the actor uses it" % running
           if running else "none: the action is run with your values")
    script_text = read(script, 60000)
    if task["id"] in PLANS:
        plan = Plan(**PLANS[task["id"]])
    else:
        plan = await call(
            "THE ASKS, in order:\n%s\n\nTHE CHECKLIST:\n%s\n\nTHE INCANTATION: %s\n\nTHE ACTION:\n%s\n\nITS SCRIPT "
            "(its start):\n%s\n\nPlan the test casts." % (asked(task), numbered(items), how, code_text[:6000],
                                                       script_text[:8000]), Plan, PLAN_SYSTEM, spent)
    window = watch_window(items + [x["text"] for a in task.get("asks") or [] for x in a.get("lines") or []],
                          (bind or {}).get("values"), plan.watch_at_least)
    casts = []
    for k in range(2):
        try:
            casts.append(await one_cast(s, bridge, k, i, plan, bind, running, action, window))
        except Exception as e:  # noqa: BLE001 — a cast that could not be made is can't tell for it
            casts.append({"k": k + 1, "error": "%s: %s" % (type(e).__name__, e), "probes": [], "obs": [],
                          "joined": [], "cast": {}, "copy": None, "log": "", "log_errors": []})
    v = await call(
        "WHAT THE PLAYER SAID, in order:\n%s\n\nTHE CHECKLIST:\n%s\n\nTHE IMPLEMENTER'S LAST REPORT (its own words, "
        "never evidence alone):\n%s\n\nTHE INCANTATION: %s\n\nTHE ACTION (its start):\n%s\n\nITS SCRIPT, the lines "
        "that show what no probe sees:\n%s\n\nTHE PLAN: setup %s; Steve's player action %s; pitch %s\n\n%s\n\n"
        "Mark each checklist item for each cast." % (
            said_lines(task), numbered(items), (report or "(none)")[:1500], json.dumps(bind)[:1200] if bind else "none",
            code_text[:2000], excerpt(script_text), json.dumps(plan.setup), plan.player_action, plan.look_pitch,
            "\n\n".join(cast_words(c) if not c.get("error") else "CAST %d: could not be made: %s" % (c["k"], c["error"])
                        for c in casts)), Verdict, JUDGE_SYSTEM, spent)
    out.update(items=final_marks(items, v, casts), summary=v.summary, plan=plan.model_dump(), casts=casts,
               cast={"by": (casts[0].get("cast") or {}).get("by"), "answer": (casts[0].get("cast") or {}).get("answer"),
                     "log_errors": [e for c in casts for e in c.get("log_errors") or []][:8],
                     "watch": [c.get("watch") for c in casts]})
    if rerun:
        add_binding(s, None)
    return out


def markdown(label: str, rows: list[dict], cost: float) -> str:
    L = ["## Judge: %s" % label, "", "| Task | Action | Exists | Check | Cast | Done | Flaky | Not done | Can't tell |",
         "|---|---|---|---|---|---|---|---|---|"]
    tot = {"done": 0, "flaky": 0, "not done": 0, "can't tell": 0, "excluded": 0}
    for r in rows:
        marks = [m["mark"] for m in r.get("items") or []]
        for k in tot:
            tot[k] += marks.count(k)
        cast = r.get("cast")
        cast_s = ("excluded" if r.get("excluded") else "-" if cast is None else "error in log" if cast.get("log_errors")
                  else (cast.get("answer") or {}).get("status") or ("ok" if cast.get("by") else "failed"))
        L.append("| %s %s | %s | %s | %s | %s | %d | %d | %d | %d |" % (
            r["id"], r.get("name") or "", r.get("action") or "-", {True: "yes", False: "no", None: "-"}[r.get("exists")],
            "-" if not r.get("check") else ("pass" if r["check"]["ok"] else "fail"), cast_s, marks.count("done"),
            marks.count("flaky"), marks.count("not done"), marks.count("can't tell")))
    L += ["", "**%d done, %d flaky, %d not done, %d can't tell; %d excluded**; judge spend $%.4f." % (
        tot["done"], tot["flaky"], tot["not done"], tot["can't tell"], tot["excluded"], cost), ""]
    for r in rows:
        L.append("### %s %s" % (r["id"], r.get("name") or ""))
        L.append(r.get("summary", ""))
        if r.get("excluded"):
            L.append("_Excluded from the total: %s. Scored apart:_" % r["excluded"])
            for m in r.get("apart") or []:
                L.append("- %s: **%s** (%s), %s" % (m["item"], m["mark"], m["evidence"], m["why"]))
        else:
            for m in r.get("items") or []:
                L.append("- %s: **%s**%s, %s" % (m["item"], m["mark"], " (%s)" % " / ".join(m["casts"])
                                                  if m.get("casts") else "", m["why"]))
        L.append("")
    return "\n".join(L)


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", required=True, help="the realmc server")
    ap.add_argument("--made", help="made/<server>/ to score (default: --name)")
    ap.add_argument("--night", action="store_true", help="load and score what Haiku made on 2026-10-11")
    ap.add_argument("--only", default="", help="task ids, comma-separated (default: the six of 2026-10-11)")
    ap.add_argument("--reports", help="a replay's runs JSON: each task's last report, by id")
    ap.add_argument("--label", default=None)
    ap.add_argument("--data", default=DATA, help="the tasks file (default: tasks.json; a game's own record)")
    ap.add_argument("--plans", help="an earlier judge JSON: each task's plan there is used again, with no plan call")
    ap.add_argument("--scenario", action="store_true", help="each task's checklist and buildable mark from "
                                                            "scenarios.json now (a scenario fixed since its game)")
    a = ap.parse_args(argv)
    load_key()
    label = a.label or ("haiku-night" if a.night else a.name)
    os.makedirs(os.path.join(OUT, "usage"), exist_ok=True)
    os.environ["VFG_USAGE_QUEUE"] = os.path.join(OUT, "usage")
    with open(a.data, encoding="utf-8") as f:
        tasks = json.load(f)["tasks"]
    only = set(filter(None, a.only.split(","))) or {t["id"] for t in tasks if t.get("date") == "2026-10-11"}
    tasks = [t for t in tasks if t["id"] in only]
    for r in ((read_json(a.plans) or {}).get("rows") or []) if a.plans else []:
        if r.get("plan"):  # a plan from before block probes: its probes are commands
            for pr in r["plan"].get("probes") or []:
                for k, v in (("blocks", ""), ("right", 0), ("up", 0), ("forward", 0), ("radius", 4)):
                    pr.setdefault(k, v)
            PLANS[r["id"]] = r["plan"]
    if a.scenario:
        from impl_scenarios import SCENARIOS
        scs = {x["id"]: x for x in (read_json(SCENARIOS) or {}).get("scenarios") or []}
        for t in tasks:
            sc = scs.get(t["id"])
            if sc:
                t.update(final_checklist=sc["checklist"], buildable=sc.get("buildable", True),
                         not_buildable=sc.get("not_buildable"))
    reports = {}
    if a.reports:
        try:  # the replay's log beside its runs.json: what each implementer ran
            with open(os.path.join(os.path.dirname(a.reports), "brain.log"), encoding="utf-8") as f:
                reports[":log"] = f.read().splitlines()
        except OSError:
            pass
        with open(a.reports, encoding="utf-8") as f:
            for r in json.load(f).get("runs") or []:
                reports[r["id"]] = r["report"]
                reports[r["id"] + ":all"] = reports.get(r["id"] + ":all", "") + "\n\n" + r["report"]
    s = Server(a.name, a.made or a.name)
    s.ensure_players()
    loaded = load_night(s) if a.night else None
    if loaded:
        print("loaded the night's: %s; bindings %s" % (", ".join(loaded["made"]), loaded["incantations"]), flush=True)
    bridge = Bridge(s.srv, os.path.join(s.srv.dir, "judge-bridge.log"))
    rows, spent = [], []
    try:
        for i, t in enumerate(tasks):
            try:
                r = await judge_task(s, bridge, t, i, a.night, reports, spent)
            except Exception as e:  # noqa: BLE001 — one task's failure is its row
                r = {"id": t["id"], "name": t.get("name"), "kind": t["kind"], "error": "%s: %s" % (type(e).__name__, e),
                     "items": []}
            rows.append(r)
            marks = [m["mark"] for m in r.get("items") or []]
            print("%s: %d done, %d flaky, %d not done, %d can't tell, %d excluded%s" % (
                t["id"], marks.count("done"), marks.count("flaky"), marks.count("not done"), marks.count("can't tell"),
                marks.count("excluded"), "; " + r["error"] if r.get("error") else ""), flush=True)
    finally:
        bridge.stop()
        s.p.close()
    cost = sum(spent)
    doc = {"label": label, "server": a.name, "made": s.made, "night": a.night, "loaded": loaded, "rows": rows,
           "judge_cost": round(cost, 6)}
    with open(os.path.join(OUT, "judge-%s.json" % label), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
    md = markdown(label, rows, cost)
    with open(os.path.join(OUT, "judge-%s.md" % label), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
