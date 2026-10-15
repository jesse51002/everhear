"""Teleport: move a player, or everyone, to a biome, a structure, a saved place, coordinates, far
away, their spawn point or where they last died, landing on safe ground. To another player it is
one `tp`, which the command scenario does: not this. It gives Resistance V and Slow Falling first
(the kit's `land_safely`, the old land_safely preset's rings of 8, 16, 32 and 64 blocks). With @a,
the first player online lands and everyone else is teleported onto them. A move far enough sets the
spawn point there. Port of the old teleport action/script: the destination kinds, the words
Minecraft answers with (locate, and the two bits of a player's own data it reads) and the distance
that sets a spawn point are kept; the structure is not — there is no long-lived RCON connection, no
`while` loop, and a locate (which can take seconds) runs off the tick through `self.ask`, its reply
landing back in `on_located` a tick later."""

import math
import random
import re
from typing import Literal

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, Spot, players
from vfg_engine.kit import land_safely

DIMS = {"overworld": "minecraft:overworld", "the_nether": "minecraft:the_nether", "the_end": "minecraft:the_end"}
HEIGHTS = {"minecraft:overworld": (-64, 319), "minecraft:the_nether": (0, 255), "minecraft:the_end": (0, 255)}
BORDER = 29_999_000          # the old land_safely preset's own border, a little inside the real one
ID_RE = re.compile(r"^[a-z0-9_]+$")
LOCATED = re.compile(r"^The nearest (\S+)(?: \((\S+)\))? is at \[(-?\d+), (-?\d+|~), (-?\d+)\]")
DATA = " has the following entity data: "
POS_RE = re.compile(r"\[I;\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\]")
DIM_RE = re.compile(r'dimension:\s*"?([a-z0-9_.:]+)"?')
# A structure id that puts the kind first (village_plains is a plains village): said the other way.
KIND_FIRST = ("village", "ruined_portal", "ocean_ruin", "shipwreck", "trial_chambers")


def clamp(v: float) -> float:
    return max(-BORDER, min(BORDER, v))


def describe(ident: str) -> str:
    """minecraft:snowy_taiga -> "a snowy taiga"; minecraft:village_plains -> "a plains village"."""
    name = ident.lstrip("#").split(":")[-1]
    if name.startswith("is_"):
        name = name[3:]
    for k in KIND_FIRST:
        if name.startswith(k + "_"):
            name = name[len(k) + 1:] + "_" + k
            break
    words = name.replace("_", " ")
    return ("an " if words[:1] in "aeiou" else "a ") + words


class Teleport(Scenario):
    class Config(ScenarioConfig):
        summary = ("Moves a player, or everyone, to a biome, a structure, a saved place, coordinates, far away, "
                   "their spawn point or where they last died, landing on safe ground.")
        duration_s = 60
        cushion_s: float = Field(5, ge=0, le=30)             # Resistance V / Slow Falling before landing
        spawn_after: float = Field(1000, ge=0, le=1_000_000)  # a move at least this far sets the spawn point

    class Args(Args):
        players: Players = Field("@a", description="who moves: @a, @r or names; the first lands and everyone "
                                                   "else is teleported onto them")
        to: Literal["biome", "structure", "place", "coords", "far", "spawn_point", "last_death"] = Field(
            "place", description="the kind of destination: biome (the nearest one), structure (the nearest one, "
                                 "such as a village), place (a saved place by name), coords (given x and z), far "
                                 "(a random spot that many blocks away), spawn_point (where the player respawns: "
                                 "their bed, else the world spawn), last_death (where the player last died)")
        target: str = Field("", description="a biome or structure id, a #tag allowed, such as snowy_taiga or "
                                            "#village; a saved place's name; 'x z' (or 'x y z') for coords; a "
                                            "distance in blocks for far, 1 to 1000000 (empty: 1000); empty for "
                                            "spawn_point and last_death")
        dimension: Literal["overworld", "the_nether", "the_end"] = Field(
            "overworld", description="the dimension to look in, for biome, structure, coords and far; a saved "
                                     "place, a spawn point and a death already know theirs")

    # ---- start: everything but biome and structure lands within the call -------------------

    def start(self, ctx):
        a = self.args
        names = players(ctx, a.players)
        if not names:
            self._nobody(a)
            return
        if a.to in ("biome", "structure"):
            full = self._full_id((a.target or "").strip())
            if full is None:
                self.result("%r is not %s id" % (a.target, "a biome" if a.to == "biome" else "a structure"),
                           players=[], failed=True)
                self.end()
                return
            self._locate(ctx, names[0], a.to, full, DIMS[a.dimension], "on_located")
            return
        try:
            at, dim, exact, label, phrase, show_xz = self._resolve(ctx, a, names[0])
        except ValueError as exc:
            self.result(str(exc), players=[], failed=True)
            self.end()
            return
        self._land(ctx, names, at, dim, exact, label, phrase, show_xz)

    def _nobody(self, a):
        self.result("nobody is online" if a.players in ("@a", "@r") else "%s is not online" % a.players, players=[], failed=True)
        self.end()

    # ---- biome and structure: a slow command, off the tick ---------------------------------

    def _full_id(self, target: str) -> str | None:
        tag = target.startswith("#")
        ident = (target[1:] if tag else target).lower()
        if ident.startswith("minecraft:"):
            ident = ident[len("minecraft:"):]
        if not ID_RE.match(ident):
            return None
        return ("#" if tag else "") + "minecraft:" + ident

    def _anchor(self, ctx, player: str, dim: str):
        """Where to look from: the mover's position, scaled into `dim` (the Nether is 8 times
        smaller), its y clamped inside that dimension."""
        p = ctx.world.player(player)
        if p is None:
            return 0, 64, 0
        frm = p.dim
        x, z = p.pos[0], p.pos[2]
        if frm != dim:
            if dim == "minecraft:the_nether" and frm == "minecraft:overworld":
                x, z = x / 8.0, z / 8.0
            elif dim == "minecraft:overworld" and frm == "minecraft:the_nether":
                x, z = x * 8.0, z * 8.0
            else:
                x, z = 0.0, 0.0
        lo, hi = HEIGHTS.get(dim, (-64, 319))
        return int(math.floor(x)), max(lo, min(hi, int(math.floor(p.pos[1])))), int(math.floor(z))

    def _locate(self, ctx, player: str, kind: str, full: str, dim: str, method: str):
        cx, cy, cz = self._anchor(ctx, player, dim)
        self.ask("execute in %s positioned %d %d %d run locate %s %s" % (dim, cx, cy, cz, kind, full), method)

    def on_located(self, ctx, reply):
        self._after_locate(ctx, reply, retry=True)

    def on_located_again(self, ctx, reply):
        self._after_locate(ctx, reply, retry=False)

    def _after_locate(self, ctx, reply, retry: bool):
        a = self.args
        names = players(ctx, a.players)
        if not names:
            self._nobody(a)
            return
        dim = DIMS[a.dimension]
        full = self._full_id((a.target or "").strip()) or ""
        m = LOCATED.match(reply)
        if m:
            label = describe(m.group(2) or m.group(1))
            x, z = float(m.group(3)), float(m.group(5))
            self._land(ctx, names, (x, 0.0, z), dim, False, label, "to %s at" % label, True)
            return
        if reply.startswith("Could not find"):
            self.result("no %s within reach of %s" % (describe(full), names[0]), players=[], failed=True)
            self.end()
            return
        if a.to == "structure" and retry and reply.startswith("There is no structure") and not full.startswith("#"):
            self._locate(ctx, names[0], "structure", "#" + full, dim, "on_located_again")
            return
        if reply.startswith("There is no") or reply.startswith("Can't find") or "<--[HERE]" in reply:
            self.result("Minecraft has no %s called %s" % (a.to, full), players=[], failed=True)
            self.end()
            return
        self.result("locate %s %s failed: %s" % (a.to, full, reply or "no answer from Minecraft"), players=[], failed=True)
        self.end()

    # ---- place, coords, far, spawn_point, last_death: resolved within the call -------------

    def _resolve(self, ctx, a, first: str):
        """(at, dim, exact, label, phrase, show_xz), or ValueError in words."""
        if a.to == "place":
            name = (a.target or "").strip()
            doc = ctx.places.get(name) if name else None
            if doc is None:
                raise ValueError("there is no saved place called %s" % name if name else
                                 "which place? give its saved name")
            return tuple(doc["pos"]), doc["dim"], True, name, "to %s at" % name, True
        if a.to == "coords":
            return self._coords(a)
        if a.to == "far":
            return self._far(ctx, a, first)
        if a.to == "spawn_point":
            return self._spawn_point(ctx, first)
        if a.to == "last_death":
            return self._last_death(ctx, first)
        raise ValueError("unknown destination %r" % a.to)

    def _coords(self, a):
        parts = re.split(r"[,\s]+", (a.target or "").strip())
        parts = [p for p in parts if p]
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            nums = []
        if len(nums) not in (2, 3):
            raise ValueError("coordinates are 'x z' or 'x y z', not %r" % a.target)
        x, z = clamp(nums[0]), clamp(nums[-1])
        dim = DIMS[a.dimension]
        if len(nums) == 3:
            return (x, nums[1], z), dim, True, " ".join(str(n) for n in nums), "to", True
        return (x, 0.0, z), dim, False, "%g %g" % (x, z), "to", False

    def _far(self, ctx, a, first):
        target = (a.target or "").strip()
        try:
            d = float(target) if target else 1000.0
        except ValueError:
            raise ValueError("far takes a distance in blocks, not %r" % target)
        if not 1 <= d <= 1_000_000:
            raise ValueError("far takes a distance from 1 to 1000000 blocks")
        dim = DIMS[a.dimension]
        cx, _, cz = self._anchor(ctx, first, dim)
        angle = random.uniform(0, 2 * math.pi)
        x, z = clamp(cx + d * math.cos(angle)), clamp(cz + d * math.sin(angle))
        return (x, 0.0, z), dim, False, "a spot %d blocks away" % int(d), "%d blocks away, to" % int(d), True

    def _nbt(self, ctx, player: str, path: str):
        """(pos, dim) read from `path` on `player`'s own data (respawn, LastDeathLocation: both
        `{pos: [I; x, y, z], dimension: "..."}`), or None when it has none. Not a general SNBT
        reader (the engine's own is internal); ValueError in words when the small shape it expects
        is not there."""
        reply = ctx.query("data get entity %s %s" % (player, path))
        i = reply.find(DATA)
        if i < 0:
            if reply.startswith("Found no elements matching"):
                return None
            raise ValueError("Minecraft did not answer about %s: %s" % (player, reply or "no reply"))
        body = reply[i + len(DATA):]
        pm = POS_RE.search(body)
        if pm is None:
            raise ValueError("cannot read %s's %s: %r" % (player, path, body[:120]))
        x, y, z = (float(g) for g in pm.groups())
        dm = DIM_RE.search(body)
        dim = dm.group(1) if dm else "minecraft:overworld"
        dim = dim if ":" in dim else "minecraft:" + dim
        return (x, y, z), dim

    def _spawn_point(self, ctx, first):
        got = self._nbt(ctx, first, "respawn")
        if got is None:
            # NEEDS: the world spawn's own position (kept in the world's level.dat; the engine
            # reads no such thing today) - approximated at 0, 0, the default spawn chunk.
            y = ctx.world.ground_y(0, 0, dim="minecraft:overworld")
            return ((0.5, float(y) if y is not None else 64.0, 0.5), "minecraft:overworld", False,
                    "the world spawn", "to the world spawn at", False)
        pos, dim = got
        return pos, dim, True, "%s's spawn point" % first, "to %s's spawn point at" % first, True

    def _last_death(self, ctx, first):
        got = self._nbt(ctx, first, "LastDeathLocation")
        if got is None:
            raise ValueError("%s has no death on record" % first)
        pos, dim = got
        return pos, dim, True, "where %s last died" % first, "to where %s last died, at" % first, True

    # ---- landing: common to every destination ----------------------------------------------

    def _land(self, ctx, names, at, dim, exact, label, phrase, show_xz):
        c = self.config
        first = names[0]
        before = ctx.world.player(first)
        start_pos, start_dim = (before.pos, before.dim) if before is not None else (None, None)
        spot = land_safely(self, ctx, first, at, exact=exact, cushion_s=c.cushion_s, dim=dim)
        if spot is None:
            at_txt = " near %d %d" % (int(at[0]), int(at[2])) if show_xz else ""
            self.result("no safe ground was found %s%s; nobody moved" % (label, at_txt), players=[], failed=True)
            self.end()
            return
        moved = [first]
        for other in names[1:]:
            self.teleport(other, spot, dim=dim)
            moved.append(other)
        spawn_set = []
        if start_pos is not None and start_dim == dim and math.hypot(
                spot[0] - start_pos[0], spot[2] - start_pos[2]) >= c.spawn_after:
            for p in moved:
                self.spawnpoint(p, Spot(dim=dim, pos=spot))
            spawn_set = list(moved)
        who_text = "everyone (%s)" % ", ".join(moved) if self.args.players == "@a" else " and ".join(moved)
        dim_suffix = "" if dim == "minecraft:overworld" else " in the " + dim.split(":")[-1].removeprefix(
            "the_").replace("_", " ")
        x, y, z = (int(math.floor(v)) for v in spot)
        summary = "teleported %s %s %d %d %d%s" % (who_text, phrase, x, y, z, dim_suffix)
        if spawn_set:
            summary += "; spawn point set there"
        self.result(summary, players=moved, pos=[x, y, z], dim=dim, to=self.args.to, target=label)
        self.end()
