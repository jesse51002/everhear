#!/usr/bin/env python3
"""The game eval: each scenario of $VFG_IMPL_REPLAY/scenarios.json (impl_scenarios.py) played as a
game, on a pair of realmc servers of its own (a live one and its test server), from nothing:

1. both servers made fresh (impl_replay.clean: `up --fresh`, made/<server>/, the incantations,
   places, running copies and history set aside), then the scenario's setup;
2. each turn:
   (a) the player's line goes to the brain stand-in, the one-call brief writer (impl_briefs_write,
       Brain_Loop's hand-off and follow-up prompts), with the conversation so far: a hand-off on the
       first turn, then follow-ups to its task;
   (b) the implementer runs it: the real Implementers on the Claude Code adapter, on DeepSeek V4.1
       Flash through OpenRouter, VFG_TEST_BOX naming the test server;
   (c) the AI tells the player what came of it: the report's first sentence;
   (d) the player tries it on the live server, as a player would (try()): on a fresh stage of the
       judge's (builder B's test templates' geometry: a husk 10 blocks ahead, a stone wall 2 behind
       it, real ground under them, Alex 4 blocks to his left), he says the incantation (cast through the real
       bridge), else right-clicks what he holds, else walks; it is watched until it settles, at
       least as long as the checklist's times say; what he perceives is written down: how far he
       moved and rose, his health, his effects at their peak, what became of the husk and the wall,
       the block under him, Alex. Never the code or the log;
   (e) the simulated player (one structured() call: persona, goal, his wants revealed so far, what
       he perceived, the AI's line) says his next line: a complaint about what is wrong, the next
       hidden want once everything so far works, or that he is done;
3. it ends when he is done or at the scenario's most turns; then impl_judge.py marks the whole
   checklist on the live server.

    export REALMC_ROOT=…/.worktrees/script-library/integration/realmc/.server
    cd brain && uv run python ../integration/impl_game.py --name ih-d1 --only T207 [--stop-at 0.40] [--dry]

--dry spends nothing: the brain stand-in and the player are stubs (the brief is the player's line;
the player reveals each hidden want in turn, then is done), the implementer is a stub that binds
the phrase to a library `command` (a Speed effect), and no judge runs; everything else is real.
Writes OUT/<id>.json and .md (the game: every line, brief, report and perception; turns to done,
runs, requests, cost at list price and as billed, time, the judge's marks), OUT/brain.log, and
OUT/tests.jsonl (each vfg test run whole, with the test and script it ran: impl_game_tests.py);
OUT is $VFG_IMPL_REPLAY/game-<name>. Models: the brief writer and the player google/gemini-3.8-flash
at low reasoning (usage component brain, refs implgame-brief and implgame-player). --stop-at
dollars, all in: no new turn starts past it, and a run is stopped once it passes it plus 0.05.
OPEN_ROUTER_API is read from Internal/.env into this process's environment only."""
import argparse
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
import time

from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "brain"))
sys.path.insert(0, os.path.join(HERE, "realmc"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "lib", "py"))

import realmc  # noqa: E402
import impl_briefs_write as writer  # noqa: E402
import impl_judge as judge  # noqa: E402
from impl_data import ROOT  # noqa: E402
from impl_replay import PlayerKeeper, clean, watched  # noqa: E402
from impl_scenarios import SCENARIOS  # noqa: E402
from impl_wild import Tee, use_realmc  # noqa: E402
from incantations_e2e import Bridge  # noqa: E402

MODEL = "google/gemini-3.8-flash"
RUN_TIMEOUT_S = 1500.0   # one turn's implementer work, on the monotonic clock


# ------------------------------------------------------------------ the simulated player

class AiLine(BaseModel):
    line: str = Field(description="what the AI says to the player, one or two short sentences")


AI_SYSTEM = (
    "You are the AI on a Minecraft server where friends play and talk to you. Something you set up for a player has "
    "just finished, and you have its builder's report. Tell the player, in one or two short, warm, casual sentences, "
    "what he has now and how to use it, or plainly what did not work and what you will try. Speak as yourself, to "
    "him by name. Never mention tests, runs, checks, implementers, builders, the brain, code, files or limits, and "
    "never copy the report's wording.")


class PlayerTurn(BaseModel):
    line: str = Field(description="what he says next, one short spoken line, as a gamer says it")
    done: bool = Field(description="true when he is happy and stops, or gives up")
    revealed: bool = Field(description="true when this line asks for his next hidden want")
    why: str = Field(description="for the record, not said: one short reason for this line")


PLAYER_SYSTEM = (
    "You play %s, a Minecraft player on a server with friends, talking to the server's AI by voice. You asked "
    "the AI for something; it built it, told you what it did, and you just tried it in the game. You see only "
    "what happened in the game and what the AI said: never code, logs or reports. Say your next line, one short "
    "spoken line:\n"
    "- if something you asked for did not happen, or happened wrong, say what you saw, plainly, as a player "
    "would (\"it didn't launch me\", \"nothing happened\", \"it hurt me\", \"the blocks didn't break\");\n"
    "- if everything you asked for so far works and you have a hidden want left, ask for the NEXT one, in "
    "your own words close to its line (revealed true);\n"
    "- if everything works and no hidden want is left, say you are happy (done true);\n"
    "- if the same thing has failed three times, you may give up (done true), saying so.\n"
    "Never ask for a hidden want before its turn, never ask for two at once, never mention code or tests.")


def player_prompt(sc: dict, revealed: int, convo: list[str], perceived: str, chat: str) -> str:
    wants = sc["hidden_wants"]
    asked = ["his opening ask: " + sc["opening"]] + ["then: %s" % w["want"] for w in wants[:revealed]]
    nxt = ("YOUR NEXT HIDDEN WANT, only once everything above works: %s (say it like: %s)" % (
        wants[revealed]["want"], wants[revealed]["line"]) if revealed < len(wants) else
           "YOU HAVE NO HIDDEN WANT LEFT.")
    return ("WHO YOU ARE: %s\nYOUR GOAL, as a whole: %s\nHOW YOU USE IT: %s\nWHAT YOU HAVE ASKED FOR SO FAR:\n- %s\n%s\n\n"
            "THE CONVERSATION SO FAR:\n%s\n\nTHE AI JUST SAID: %s\n\nWHAT YOU SAW WHEN YOU TRIED IT:\n%s\n\nYour next line." % (
                sc["persona"], sc["goal"], sc["use"], "\n- ".join(asked), nxt, "\n".join(convo[-12:]), chat, perceived))


# ------------------------------------------------------------------ the player tries it

def phrase_of(sc: dict) -> str | None:
    m = re.search(r"['\"“]([^'\"”]{3,40})['\"”]", sc.get("use") or "")
    return m.group(1).strip().lower() if m else None


def kinds_at(s: judge.Server, cells: list[tuple[int, int, int]]) -> dict:
    """How many of the cells are air, stone, ice of any kind, or something else now."""
    out = {"air": 0, "stone": 0, "ice": 0, "other": 0}
    for x, y, z in cells:
        for k, test in (("air", "air"), ("stone", "stone"), ("ice", "#minecraft:ice")):
            if s.cmd("execute if block %d %d %d %s" % (x, y, z, test)).startswith("Test passed"):
                out[k] += 1
                break
        else:
            out["other"] += 1
    return out


def block_under(s: judge.Server, who: str) -> str:
    for name in ("obsidian", "packed_ice", "blue_ice", "ice", "grass_block", "stone", "air", "fire", "magma_block"):
        if s.cmd("execute at %s if block ~ ~-1 ~ %s" % (who, name)).startswith("Test passed"):
            return name
    return "something else"


def effects(s: judge.Server, who: str) -> list[str]:
    return sorted(set(re.findall(r'id: ?"minecraft:([a-z_]+)"', s.cmd("data get entity %s active_effects" % who))))


def health(s: judge.Server, sel: str) -> float | None:
    v = s.data(sel, "Health")
    return v[0] if v else None


def held(s: judge.Server, who: str) -> str:
    out = s.cmd("data get entity %s SelectedItem" % who)
    if "Found no elements" in out or "No entity" in out:
        return ""
    m = re.search(r'custom_name"?: ?(\'[^\']*\'|"[^"]*"|\{[^}]*\})', out)
    item = re.search(r'id: ?"minecraft:([a-z_]+)"', out)
    return ("%s%s" % (item.group(1) if item else "an item", " named %s" % m.group(1) if m else "")) if out else ""


# Block predicates the look-around counts, each by one `clone … filtered` (its answer counts what
# matched), and the mobs it counts near the player.
LOOK_BLOCKS = ["air", "#minecraft:planks", "#minecraft:logs", "#minecraft:leaves", "stone", "cobblestone",
               "#minecraft:stone_bricks", "#minecraft:ice", "snow_block", "glass", "#minecraft:wool", "obsidian",
               "fire", "lava", "water", "tnt", "#minecraft:doors", "#minecraft:stairs", "#minecraft:slabs",
               "#minecraft:dirt", "grass_block", "sand", "netherrack", "magma_block"]
LOOK_MOBS = ["zombie", "husk", "skeleton", "spider", "cave_spider", "creeper", "enderman", "witch", "wither",
             "wither_skeleton", "ender_dragon", "blaze", "ghast", "phantom", "slime", "magma_cube", "vex", "evoker",
             "ravager", "piglin", "warden", "iron_golem", "villager", "cow", "pig", "sheep", "chicken", "wolf", "cat",
             "horse", "tnt", "fireball", "falling_block", "lightning_bolt", "block_display", "armor_stand"]
LOOK_R, LOOK_DOWN, LOOK_UP = 24, 8, 28      # blocks around him: 49 x 49, 8 below his feet to 28 above
SCRATCH_Y = -63                              # where clone counts are written: the world's bottom, under him


count = judge.count


def mobs_near(s: judge.Server, who: str, r: int = 48) -> dict:
    out, total = {}, 0
    m = re.search(r"Count: (\d+)", s.cmd("execute at %s if entity @e[type=!player,type=!item,distance=..%d]" % (who, r)))
    total = int(m.group(1)) if m else 0
    for k in LOOK_MOBS:
        m = re.search(r"Count: (\d+)", s.cmd("execute at %s if entity @e[type=%s,distance=..%d]" % (who, k, r)))
        if m and int(m.group(1)):
            out[k] = int(m.group(1))
    if total - sum(out.values()) > 0:
        out["other"] = total - sum(out.values())
    return out


class Surroundings:
    """What a player sees looking around: blocks within LOOK_R of where he stands, counted by type
    against how they were when the scenario began, the box the changes fill, and the mobs."""

    def __init__(self, s: judge.Server, who: str):
        self.s, self.who = s, who
        pos = s.data(who, "Pos")[:3]
        self.x, self.y, self.z = (int(pos[0] // 1), int(pos[1] // 1), int(pos[2] // 1)) if len(pos) == 3 else (0, 85, 0)
        self.box = (self.x - LOOK_R, self.y - LOOK_DOWN, self.z - LOOK_R, self.x + LOOK_R, self.y + LOOK_UP,
                    self.z + LOOK_R)
        self.snap_y = SCRATCH_Y + 14  # a copy of the region as it began, to find where it changed
        s.cmd("forceload add %d %d %d %d" % (self.box[0], self.box[2], self.box[3], self.box[5]))
        self.base = {p: count(s, self.box, p) for p in LOOK_BLOCKS}
        self.base_mobs = mobs_near(s, who)
        x1, y1, z1, x2, y2, z2 = self.box
        layer = (x2 - x1 + 1) * (z2 - z1 + 1)
        step = max(1, 32768 // layer)
        for y in range(y1, y2 + 1, step):
            s.cmd("clone %d %d %d %d %d %d %d %d %d replace force" % (x1, y, z1, x2, min(y2, y + step - 1), z2, x1,
                                                                       self.snap_y + (y - y1), z1))

    def slices(self, pred: str) -> tuple[list, list, list]:
        """The x, z and y slices of the region where `pred`'s count differs from the snapshot's."""
        x1, y1, z1, x2, y2, z2 = self.box
        sy = self.snap_y - y1  # the snapshot is the region moved down by this

        def differs(a):
            b = (a[0], a[1] + sy, a[2], a[3], a[4] + sy, a[5])
            return count(self.s, a, pred) != count(self.s, b, pred)
        xs = [x for x in range(x1, x2 + 1) if differs((x, y1, z1, x, y2, z2))]
        zs = [z for z in range(z1, z2 + 1) if differs((x1, y1, z, x2, y2, z))]
        ys = [y for y in range(y1, y2 + 1) if differs((x1, y, z1, x2, y, z2))]
        return xs, zs, ys

    def look(self) -> tuple[str, dict]:
        s = self.s
        now = {p: count(s, self.box, p) for p in LOOK_BLOCKS}
        delta = {p: now[p] - self.base[p] for p in LOOK_BLOCKS if now[p] != self.base[p]}
        names = {p: p.replace("#minecraft:", "").replace("minecraft:", "") for p in LOOK_BLOCKS}
        # where the big changes are: the slices of the region where the kinds that changed most differ
        big = [p for p, v in sorted(delta.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 20 and p != "air"][:2]
        xs, zs, ys = set(), set(), set()
        for p in big:
            a, b, c = self.slices(p)
            xs |= set(a)
            zs |= set(b)
            ys |= set(c)
        mobs = mobs_near(s, self.who)
        mob_delta = {k: mobs.get(k, 0) - self.base_mobs.get(k, 0) for k in set(mobs) | set(self.base_mobs)
                     if mobs.get(k, 0) != self.base_mobs.get(k, 0)}
        code, state, _ = s.vfg("state", "--json")
        try:
            st = json.loads(state)
            sky = "%s, %s" % ((st.get("time") or {}).get("phase") or "?", st.get("weather") or "?")
        except ValueError:
            sky = "unknown"
        words = []
        if delta:
            words.append("Within %d blocks of you, blocks now against before: %s." % (LOOK_R, ", ".join(
                "%s %+d" % (names[k], v) for k, v in sorted(delta.items(), key=lambda kv: -abs(kv[1])))))
        else:
            words.append("Nothing around you (within %d blocks) changed." % LOOK_R)
        if xs and zs and ys:
            words.append("The new %s fill a box %d wide (east-west) by %d deep by %d high, from %d to %d blocks above "
                         "your feet, its middle %d blocks east and %d south of you." % (
                             " and ".join(names[p] for p in big), max(xs) - min(xs) + 1, max(zs) - min(zs) + 1,
                             max(ys) - min(ys) + 1, min(ys) - self.y, max(ys) - self.y,
                             (max(xs) + min(xs)) // 2 - self.x, (max(zs) + min(zs)) // 2 - self.z))
        words.append("Mobs within 48 blocks, against before: %s." % (", ".join(
            "%s %+d" % (k, v) for k, v in sorted(mob_delta.items(), key=lambda kv: -abs(kv[1]))) or "no change"))
        words.append("Time and weather: %s." % sky)
        return " ".join(words), {"delta": {names[k]: v for k, v in delta.items()},
                                 "box": [len(xs), len(zs), len(ys)], "mobs": mob_delta, "sky": sky}


def settle(s: judge.Server, who: str, name: str, most: float, least: float = 0.0, fire_box: tuple | None = None):
    """Watch until things settle (judge.watch): (the trace, every effect he had at any point, the
    most fire blocks counted, the watch's record)."""
    w = judge.watch(s, who, most, least, None, name, others=(), fire_box=fire_box)
    return w["trace"], sorted(w["peak"][who]), w["fire"], w


def wall_cells(cx: int, y: int, cz: int) -> list:
    return [(x, yy, z) for x in range(cx + 12, cx + 15) for yy in range(y, y + 3) for z in range(cz - 1, cz + 2)]


async def use_it(s, bridge, actor: str, bind: dict | None, held0: str) -> str:
    if bind:
        now = time.time()
        code, ans = bridge.cast(actor, bind["phrase"], now - 0.6, now - 0.1)
        how = 'you said "%s"' % bind["phrase"]
        if (ans or {}).get("status") != "fired":
            how += " (the server answered %s: %s)" % (code, (ans or {}).get("status") or (ans or {}).get("error"))
        return how
    if held0:
        s.p.use(actor)
        return "you right-clicked with %s" % held0
    return ""


async def try_it(s: judge.Server, bridge, sc: dict, name: str, turn: int, around) -> tuple[str, dict]:
    """The player tries what was made, by the scenario's `try`; (what he perceived, in words; the numbers)."""
    actor = sc.get("player") or "Steve"
    other = "Alex" if actor == "Steve" else "Steve"
    kind = sc.get("try") or "cast"
    if kind == "research":
        return "There is nothing to try in the game: you read what the AI told you.", {}
    if kind in ("build", "event"):
        words, seen = around.look()
        hp = health(s, actor)
        eff = effects(s, actor)
        words += " Your health: %s. Your effects: %s." % (hp if hp is not None else "you are dead", ", ".join(eff) or "none")
        return "You looked around. " + words, {**seen, "health": hp, "effects": eff}
    bind = judge.binding(s, {"name": name}, None, False)
    if bind:
        s.vfg("script", "deploy", bind.get("script") or bind.get("action"))
    # the judge's stage (builder B's templates' geometry): the husk 10 ahead, the wall 2 behind it, the
    # ground, the other player 4 blocks to his left; both spawned again for a cast, kept (with what
    # they hold) for an item
    st = s.stage(turn, order=(actor, other), respawn=bool(bind) and kind == "cast")
    cx, cz, y = st["cx"], st["cz"], st["y"]
    s.cmd("tp %s %g %d %g 180 0" % (actor, cx + 0.5, y, cz + 0.5))
    s.cmd("tp %s %g %d %g 180 0" % (other, cx + 0.5 - judge.ALEX_LEFT, y, cz + 0.5))
    s.steady(actor, other)  # just moved: on the ground again before he casts
    cube = [(x, yy, z) for x in range(st["wall"][0], st["wall"][3] + 1) for yy in range(st["wall"][1], st["wall"][4] + 1)
            for z in range(st["wall"][2], st["wall"][5] + 1)]
    wall = wall_cells(cx, y, cz)
    s.cmd("fill %d %d %d %d %d %d stone" % (cx + 12, y, cz - 1, cx + 14, y + 2, cz + 1))
    time.sleep(0.5)
    target = "@e[tag=judge_target,limit=1]"
    husk0 = health(s, target)
    alex0 = health(s, other)
    held0 = held(s, actor) if kind == "item" or not bind else ""
    how = await use_it(s, bridge, actor, bind, held0) if (bind or held0) else ""
    if not how:
        s.p.move(actor, "forward")
        how = "you had no incantation for it and held nothing, so you walked forward"
    t_cast = time.time()
    least, most = judge.watch_window(sc.get("checklist") or [], (bind or {}).get("values"))
    w = judge.watch(s, actor, most, least, None, name, others=(other,), target=target, fire_box=st["fire_box"])
    trace, fire = w["trace"], w["fire"]
    if how.startswith("you had no"):
        s.p.move(actor, None)
    husk1 = health(s, target)
    alive = actor not in w["gone"] and "No entity was found" not in s.cmd("data get entity %s Pos" % actor)
    after = {"cube": kinds_at(s, cube), "under": block_under(s, actor) if alive else "-",
             "effects": sorted(w["peak"][actor]), "alex": health(s, other), "alex_lowest": w["lowest"].get(other),
             "alex_effects": sorted(w["peak"][other]), "held": held(s, actor), "fire": fire,
             "watch": {"seconds": w["seconds"], "ended": w["ended"], "least": least, "most": most}}
    # the wall probe: the same again, aimed at a bare wall 12 blocks east with nothing in front of it
    wall_how = ""
    if alive and (bind or held0):
        cool = float((bind or {}).get("cooldown_s") or 3.0)
        await asyncio.sleep(max(0.0, min(20.0, max(cool, 1.2) + 0.3 - (time.time() - t_cast))))
        s.cmd("tp %s %g %d %g -90 0" % (actor, cx + 0.5, y, cz + 0.5))
        s.steady(actor)
        wall_how = await use_it(s, bridge, actor, bind, held(s, actor) if not bind else "")
        judge.watch(s, actor, min(most, 20.0), least, None, name, others=())
        after["wall"] = kinds_at(s, wall)
    p0 = trace[0]["pos"] if trace and trace[0]["pos"] else None
    ys = [t["pos"][1] for t in trace if t["pos"]]
    far = max((((t["pos"][0] - p0[0]) ** 2 + (t["pos"][2] - p0[2]) ** 2) ** 0.5 for t in trace if t["pos"]),
              default=0) if p0 else 0
    hps = [t["health"] for t in trace if t["health"] is not None]
    words = ["You tried it: %s, facing a husk 10 blocks ahead with a stone wall right behind it, %s 4 blocks to "
             "your left." % (how, other)]
    if p0:
        words.append("You moved %.0f blocks across and rose %.0f blocks at most%s." % (
            far, max(ys) - p0[1] if ys else 0, ", then came down" if ys and max(ys) - ys[-1] > 2 else ""))
    if hps:
        words.append("Your health went %s to %s%s." % (hps[0], hps[-1], " (lowest %s)" % min(hps) if min(hps) < hps[-1] else ""))
    if not alive:
        words.append("You died.")
    words.append("The husk: %s." % ("killed" if husk0 and husk1 is None else "unhurt" if husk1 == husk0 else
                                     "hurt, %s to %s" % (husk0, husk1)))
    c = after["cube"]
    words.append("The 3x3x3 stone wall behind it (12 ahead): %d of 27 blocks gone, %d turned to ice, %d other." % (
        c["air"], c["ice"], c["other"]))
    if fire:
        words.append("At the most, %d fire blocks burned around you." % fire)
    if "wall" in after:
        w = after["wall"]
        words.append("Then you turned to a bare 3x3x3 stone wall 12 blocks away with nothing in front of it and %s: "
                     "%d of 27 blocks gone, %d turned to ice, %d other." % (wall_how or "tried again", w["air"], w["ice"],
                                                                           w["other"]))
    words.append("You stand on %s now. Effects you got: %s." % (after["under"], ", ".join(after["effects"]) or "none"))
    low = after["alex_lowest"]
    words.append("%s, 4 blocks to your left: %s%s." % (
        other, "gone or dead" if after["alex"] is None else "unhurt" if after["alex"] == alex0 and (low is None or low >= alex0)
        else "hurt, %s to %s (lowest %s)" % (alex0, after["alex"], low),
        "; his effects: %s" % ", ".join(after["alex_effects"]) if after["alex_effects"] else ""))
    if after["held"] and after["held"] != held0:
        words.append("You now hold %s." % after["held"])
    s.cmd("execute positioned %d %d %d run kill @e[type=!player,distance=..80]" % (cx, judge.FLOOR_Y, cz))
    s.cmd("tp %s 0.5 85 0.5" % actor)
    s.cmd("tp %s 12.5 85 0.5" % other)
    return " ".join(words), {"how": how, "husk": [husk0, husk1], "trace": judge.trace_summary(trace), **after}


# ------------------------------------------------------------------ one game

def first_sentence(report: str) -> str:
    text = " ".join(report.split())
    text = re.sub(r"^\W*(done|failed|cancelled)\W*", "", text, flags=re.I)
    m = re.match(r"(.{20,220}?[.!?])(\s|$)", text)
    return (m.group(1) if m else text[:200]).replace("**", "").replace("`", "")


async def play(sc: dict, a, out: str, tee, log, costs: dict) -> dict:
    from brain.implementers.config import ImplementerConfig
    from brain.implementers.runner import Implementers
    from vfg_llm import structured
    name_live, test = a.name, "%s-test" % a.name
    t0 = time.monotonic()
    keeper = PlayerKeeper(name_live)
    moved = [m for got in await asyncio.gather(asyncio.to_thread(clean, name_live),
                                                asyncio.to_thread(clean, test, False)) for m in got]
    log("game", "%s: fresh live and test servers; set aside %s" % (sc["id"], ", ".join(moved) or "nothing"))
    subprocess.run(["vfg", "action", "list", "--json"], capture_output=True, timeout=120)
    await asyncio.to_thread(keeper.ensure)
    for cmd in sc.get("setup") or []:
        if cmd.startswith("vfg "):
            r = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=120)
            said = (r.stdout or r.stderr).strip()[-200:]
        else:
            said = judge.Server(name_live, name_live).cmd(cmd)
        log("game", "%s setup: %s: %s" % (sc["id"], cmd, said))
    s = judge.Server(name_live, name_live)
    s.ensure_players()
    around = Surroundings(s, sc.get("player") or "Steve") if sc.get("try") in ("build", "event") else None
    bridge = Bridge(s.srv, os.path.join(out, "%s-bridge.log" % sc["id"]))
    keeper.start()
    reports: list[dict] = []
    got = asyncio.Event()

    async def on_report(name, status, report, description, made):
        reports.append({"task": name, "status": status, "report": report, "at": time.time()})
        got.set()

    imps = watched(Implementers)(ImplementerConfig.load(), on_report, log)
    task = {"id": sc["id"], "name": None}
    convo, history, turns, revealed, line = [], [], [], 0, sc["opening"]
    sections = writer.sections(writer.prompt_path())
    done = False
    try:
        for n in range(1, int(sc.get("max_turns") or 6) + 1):
            if spent(tee, costs) > a.stop_at:
                log("game", "%s: $%.4f spent, past --stop-at: no turn %d" % (sc["id"], spent(tee, costs), n))
                break
            convo.append("#%d %s: %s" % (n, sc.get("player", "Steve"), line))
            ask = {"n": n - 1, "kind": "start" if n == 1 else "follow_up", "source": "player",
                   "lines": [{"seq": n, "who": sc.get("player", "Steve"), "text": line}], "conversation": convo[:-1]}
            # (a) the brain stand-in's brief
            if a.dry:
                if n == 1:
                    task["name"] = "%s_steve" % sc["id"].lower()
                brief = {"text": line, "description": line}
            elif n == 1:
                ans = await structured(writer.handoff_prompt(task, ask), writer.HandOff, component="brain",
                                       call_id="implgame-brief", system=writer.handoff_system(sections), model=MODEL,
                                       reasoning="low", timeout=90)
                costs["brief"] += ans.cost
                h = ans.value
                task["name"] = re.sub(r"[^a-z0-9_]", "_", h.name.lower())[:32]
                brief = {"text": h.brief(), "description": h.description, "done_when": h.done_when}
            else:
                ans = await structured(writer.follow_up_prompt(task, ask, history), writer.FollowUp, component="brain",
                                       call_id="implgame-brief", system=writer.follow_up_system(sections), model=MODEL,
                                       reasoning="low", timeout=90)
                costs["brief"] += ans.cost
                brief = {"text": ans.value.brief.strip(), "description": ans.value.description}
            history.append(brief)
            ask["brief"] = brief
            # (b) the implementer
            got.clear()
            before = len(reports)
            if a.dry and sc.get("try") in ("build", "event"):  # something to see: a planks cube and three zombies
                s.cmd("fill 3 85 3 7 89 7 oak_planks")
                for k in range(3):
                    s.cmd("summon zombie %d 85 10 {NoAI:1b,PersistenceRequired:1b}" % k)
                reports.append({"task": task["name"], "status": "done", "at": time.time(),
                                "report": "Done: (dry) a planks cube 5 blocks out and three zombies."})
            elif a.dry:
                phrase = phrase_of(sc) or "try it"
                r = subprocess.run(["vfg", "action", "run", "incantation_create", "player=Steve", "phrase=" + phrase,
                                    "action=command", "values=" + json.dumps({"commands": ["effect give Steve speed 5 1"]}),
                                    "--task", task["name"]], capture_output=True, text=True, timeout=120)
                reports.append({"task": task["name"], "status": "done", "at": time.time(),
                                "report": "Done: (dry) bound %r to a Speed effect: %s" % (phrase, r.stdout.strip()[-120:])})
            else:
                if n == 1:
                    await imps.start(task["name"], brief["text"])
                else:
                    await imps.follow_up(task["name"], brief["text"])
                end = time.monotonic() + RUN_TIMEOUT_S
                while time.monotonic() < end and (len(reports) == before or task["name"] in imps.running()):
                    got.clear()
                    try:
                        await asyncio.wait_for(got.wait(), 5)
                    except TimeoutError:
                        pass
                    if spent(tee, costs) > a.stop_at + 0.15:  # only a runaway: a game is never cut for the budget
                        log("game", "%s: the run is stopped, $%.4f spent" % (sc["id"], spent(tee, costs)))
                        await imps.close(5)
                        break
            mine = reports[before:]
            report = mine[-1]["report"] if mine else "(no report)"
            # (c) the AI's line
            if a.dry:
                chat = first_sentence(report)
            else:
                try:
                    ans = await structured("THE PLAYER (%s) ASKED: %s\n\nTHE BUILDER'S REPORT:\n%s\n\nWhat you say to him." % (
                        sc.get("player", "Steve"), line, report[:4000]), AiLine, component="brain", call_id="implgame-chat",
                        system=AI_SYSTEM, model=MODEL, reasoning="low", timeout=60)
                    costs["chat"] = costs.get("chat", 0.0) + ans.cost
                    chat = " ".join(ans.value.line.split())
                except Exception as e:  # noqa: BLE001 — the report's first sentence will do
                    log("game", "%s: the AI's line was not written (%s): its report's first sentence" % (sc["id"], e))
                    chat = first_sentence(report)
            convo.append("AI: %s" % chat)
            # (d) the player tries it
            perceived, seen = await try_it(s, bridge, sc, task["name"], n, around)
            # (e) the player's next line
            if a.dry:
                if revealed < len(sc["hidden_wants"]):
                    pt = PlayerTurn(line=sc["hidden_wants"][revealed]["line"], done=False, revealed=True, why="dry")
                else:
                    pt = PlayerTurn(line="Nice, that's it.", done=True, revealed=False, why="dry")
            else:
                ans = await structured(player_prompt(sc, revealed, convo, perceived, chat), PlayerTurn, component="brain",
                                       call_id="implgame-player", system=PLAYER_SYSTEM % sc.get("player", "Steve"),
                                       model=MODEL, reasoning="low", timeout=60)
                costs["player"] += ans.cost
                pt = ans.value
            if pt.revealed and revealed < len(sc["hidden_wants"]):
                revealed += 1
            turns.append({"turn": n, "line": line, "brief": brief["text"], "reports": [x["report"] for x in mine],
                          "status": mine[-1]["status"] if mine else None, "ai": chat, "perceived": perceived,
                          "seen": seen, "next": pt.line, "done": pt.done, "why": pt.why, "revealed": revealed})
            log("game", "%s turn %d: %s | AI: %s | saw: %s | next: %s%s" % (
                sc["id"], n, line[:120], chat[:120], perceived[:300], pt.line[:120], " (done)" if pt.done else ""))
            line = pt.line
            if pt.done:
                done = True
                break
        if not a.dry:
            await imps.close(5)
    finally:
        keeper.stop()
        bridge.stop()
    return {"id": sc["id"], "name": task["name"], "turns": turns, "done": done, "revealed": revealed,
            "hidden_wants": len(sc["hidden_wants"]), "seconds": round(time.monotonic() - t0, 1), "reports": reports}


SHARED: str | None = None  # --budget-file: every process of one run adds its spend there


def mine(tee, costs: dict) -> float:
    """This process's spend: every implementer request at list price, and the models (the scenarios
    before this one in costs["before"], and this one's)."""
    reqs = sum(float(m.group(1)) for ln in tee.lines for m in [re.search(r" request \S+ .* cost=([\d.]+)", ln)] if m)
    return reqs + sum(costs.values())


def spent(tee, costs: dict) -> float:
    """The run's spend: this process's, written to SHARED.<pid>, plus every other process's of the
    same run, read from the files beside it; without --budget-file, this process's alone."""
    me = mine(tee, costs)
    if not SHARED:
        return me
    with open("%s.%d" % (SHARED, os.getpid()), "w") as f:
        f.write("%.6f\n" % me)
    total = 0.0
    for p in glob.glob(SHARED + ".*"):
        try:
            total += float(open(p).read().strip() or 0)
        except (OSError, ValueError):
            pass
    return total


def write(out: str, sc: dict, game: dict, tee, costs: dict, verdict: dict | None) -> dict:
    name = game["name"] or ""
    esc = re.escape(name)
    lines = tee.lines
    req = [ln for ln in lines if re.search(r" request %s " % esc, ln)]
    lst = sum(float(m.group(1)) for ln in req for m in [re.search(r" cost=([\d.]+)", ln)] if m)
    billed = sum(float(m.group(1)) for ln in lines
                 for m in [re.search(r" usage implementer .* cost=([\d.]+) \(priced by the provider", ln)] if m and
                 re.search(r" ref=%s\b" % esc, ln))
    runs = sum(1 for ln in lines if re.search(r" implementer %s (started|resumed)\b" % esc, ln))
    tests = sum(1 for ln in lines if re.search(r" testcall %s " % esc, ln))
    marks = [m["mark"] for m in (verdict or {}).get("items") or []]
    excluded = marks.count("excluded")  # a scenario not buildable in the game: scored apart, outside the total
    doc = {**game, "scenario": sc, "runs": runs, "requests": len(req), "cost_list": round(lst, 5),
           "cost_billed": round(billed, 5), "brief_cost": round(costs["brief"], 5), "player_cost": round(costs["player"], 5), "chat_cost": round(costs.get("chat", 0.0), 5),
           "judge_cost": round(costs["judge"], 5), "vfg_tests": tests, "verdict": verdict,
           "items_done": marks.count("done"), "items_flaky": marks.count("flaky"), "items_excluded": excluded,
           "items": len(sc["checklist"]) - excluded}
    with open(os.path.join(out, "%s.json" % sc["id"]), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, default=str)
    L = ["## %s, a game: %s" % (sc["id"], sc["goal"]), "",
         "%s in %d turns of %d (%d of %d hidden wants revealed); %d implementer runs, %d requests, %d vfg test runs; "
         "%.0f s. Cost: implementers $%.4f at list, $%.4f billed; brief writer $%.4f, player $%.4f, judge $%.4f. "
         "Judge: %d of %d items done%s." % (
             "Done" if game["done"] else "Not done", len(game["turns"]), int(sc.get("max_turns") or 6), game["revealed"],
             len(sc["hidden_wants"]), runs, len(req), tests, game["seconds"], lst, billed, costs["brief"],
             costs["player"], costs["judge"], marks.count("done"), len(sc["checklist"]) - excluded,
             (", %d flaky" % marks.count("flaky") if marks.count("flaky") else "") +
             ("; %d excluded: %s" % (excluded, (verdict or {}).get("excluded")) if excluded else "")), ""]
    for t in game["turns"]:
        L += ["**%d. %s:** %s" % (t["turn"], sc.get("player", "Steve"), t["line"]),
              "**AI:** %s" % t["ai"], "_He tried it:_ %s" % t["perceived"], ""]
    if game["turns"]:
        L += ["**%s (last):** %s" % (sc.get("player", "Steve"), game["turns"][-1]["next"]), ""]
    for m in (verdict or {}).get("items") or []:
        L.append("- %s: **%s**, %s" % (m["item"], m["mark"], m["why"]))
    with open(os.path.join(out, "%s.md" % sc["id"]), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return doc


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--name", required=True, help="the live realmc server; its test server is NAME-test")
    ap.add_argument("--only", required=True, help="scenario ids, comma-separated, played one after another")
    ap.add_argument("--stop-at", type=float, default=0.40, help="dollars, all in: the run's budget, checked before "
                                                                     "each turn and each scenario")
    ap.add_argument("--budget-file", default=None, help="a path every process of one run shares: --stop-at is then "
                                                        "the whole run's, summed over them")
    ap.add_argument("--dry", action="store_true", help="no model: stub brain, implementer and player; no judge")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    global SHARED
    SHARED = os.path.abspath(a.budget_file) if a.budget_file else None
    out = os.path.abspath(a.out or os.path.join(ROOT, "game-%s" % a.name))
    os.makedirs(out, exist_ok=True)
    use_realmc(a.name)
    writer.load_key()
    os.environ.update(VFG_IMPL_TESTS=os.path.join(out, "tests.jsonl"))  # every vfg test run, whole (impl_game_tests.py)
    os.environ.update(VFG_IMPL_PROVIDER="openrouter", VFG_USAGE_QUEUE=out, VFG_IMPL_HARNESS="claude-code",
                      VFG_TEST_BOX=os.path.join(realmc.ROOT, "%s-test" % a.name, "home", ".vfg", "config"))
    if a.dry:  # incantation_create names aliases with a model: off, so a dry run spends nothing
        os.environ["VFG_INCANT_ALIAS_MODEL"] = "off"
    with open(SCENARIOS, encoding="utf-8") as f:
        scs = {s["id"]: s for s in json.load(f)["scenarios"]}
    tee = Tee(os.path.join(out, "brain.log"))
    sys.stdout = tee
    from brain.implementers.config import log
    results = []
    before = 0.0
    try:
        for sid in a.only.split(","):
            sc = scs[sid]
            if results and spent(tee, {"before": before}) > a.stop_at:
                log("game", "%s not started: past --stop-at" % sid)
                continue
            costs = {"brief": 0.0, "player": 0.0, "judge": 0.0, "chat": 0.0, "before": before}
            game = await play(sc, a, out, tee, log, costs)
            verdict = None
            if not a.dry and game["name"]:
                record = {"tasks": [{"id": sc["id"], "name": game["name"], "kind": sc["kind"], "date": "game",
                                     "final_checklist": sc["checklist"], "night": {},
                                     "buildable": sc.get("buildable", True), "not_buildable": sc.get("not_buildable"),
                                     "asks": [{"n": t["turn"] - 1, "kind": "start" if t["turn"] == 1 else "follow_up",
                                               "lines": [{"who": sc.get("player", "Steve"), "text": t["line"]}],
                                               "brief": {"text": t["brief"]}} for t in game["turns"]]}]}
                with open(os.path.join(out, "%s.record.json" % sid), "w", encoding="utf-8") as f:
                    json.dump(record, f, indent=1)
                with open(os.path.join(out, "%s.runs.json" % sid), "w", encoding="utf-8") as f:
                    json.dump({"runs": [{"id": sid, "report": r["report"]} for r in game["reports"]]}, f, indent=1)
                label = "game-%s-%s" % (a.name, sid)
                r = await asyncio.to_thread(subprocess.run, [
                    sys.executable, os.path.join(HERE, "impl_judge.py"), "--name", a.name, "--only", sid, "--data",
                    os.path.join(out, "%s.record.json" % sid), "--reports", os.path.join(out, "%s.runs.json" % sid),
                    "--label", label], capture_output=True, text=True)
                j = judge.read_json(os.path.join(ROOT, "judge-%s.json" % label)) or {}
                costs["judge"] = j.get("judge_cost") or 0.0
                verdict = (j.get("rows") or [None])[0]
                log("game", "%s judged: %s" % (sid, (r.stdout.strip().splitlines() or ["(no answer)"])[0][:200]))
            results.append(write(out, sc, game, tee, costs, verdict))
            before += costs["brief"] + costs["player"] + costs["judge"] + costs.get("chat", 0.0)
    finally:
        sys.stdout = sys.__stdout__
        tee.flush()
    for d in results:
        print("%s: %s in %d turns; %d/%d items; implementers $%.4f list, $%.4f billed; models $%.4f; %d s" % (
            d["id"], "done" if d["done"] else "not done", len(d["turns"]), d["items_done"], d["items"], d["cost_list"],
            d["cost_billed"], d["brief_cost"] + d["player_cost"] + d["chat_cost"] + d["judge_cost"], d["seconds"]))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
