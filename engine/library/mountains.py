"""Mountains: a natural mountain range beside a player (or at a place), width by depth blocks:
ridged snowy peaks, rock and grass by height, two lakes, two carved caves, up to `trees` spruce on
its grass slopes, and jagged_peaks/snowy_slopes/grove biomes by height. It forceloads its area and
queues its fills a row at a time, then its caves, its trees and its biomes, each over many calls;
the blocks stay.

mountain_trees (the old separate script, the same area's spruce) is folded in as the `trees` arg:
mountains already finds candidate spots while it builds the terrain (main.py:209-214 of the old
script), so planting them is this scenario's own last phase, not a second pass over the ground."""

import math
import random

from vfg_engine import Args, Field, Place, Players, Scenario, ScenarioConfig, State, players


def _height(x, z, base, peaks, ph):
    """The ridged, peaked terrain height at x, z (DESIGN2: the old mountains/main.py:91-103)."""
    h = 0.0
    for px, pz, a, r in peaks:
        d = math.hypot(x - px, z - pz)
        ang = math.atan2(z - pz, x - px)
        rr = r * (1 + 0.25 * math.sin(3 * ang + px) + 0.15 * math.sin(5 * ang + pz))
        v = a * math.exp(-(d / rr) ** 2 * 1.3)
        if v > 0:
            h = max(h, v)
    h += 5 * math.sin(x * 0.11 + ph[0]) * math.sin(z * 0.13 + ph[1]) + 3 * math.sin(x * 0.27 + z * 0.19 + ph[2])
    h += 4 * abs(math.sin(x * 0.05 + ph[3]) * math.cos(z * 0.06 + ph[4])) * min(1, h / 40)
    return base + h


def _lake_of(x, z, lakes):
    for lx, lz, r in lakes:
        d = math.hypot(x - lx, z - lz)
        if d < r + 7:
            return lx, lz, r, d
    return None


def _pick_rock(ix, iz):
    """andesite, tuff or stone, the same for the same column every time (main.py:120-122)."""
    r = (ix * 73856093 ^ iz * 19349663) % 100
    return "minecraft:andesite" if r < 35 else ("minecraft:tuff" if r < 60 else "minecraft:stone")


def _chance(seed, x, z, tag):
    """A number from 0 to 1, the same for the same cell and tag every time: in place of a shared,
    stateful random stream the engine's State (plain data only) cannot hold across calls."""
    return random.Random("%d %d %d %s" % (seed, x, z, tag)).random()


class Mountains(Scenario):
    every = 0.1                       # tick(): one row of terrain, one cave, one tree or one biome run a call

    class Config(ScenarioConfig):
        summary = ("Builds a natural mountain range beside a player (or at a place): ridged snowy peaks, rock "
                   "and grass by height, two lakes, two caves, spruce on the slopes, biomes by height.")
        duration_s = 900
        width: int = Field(200, ge=40, le=400)           # the range's width, east-west, in blocks
        depth: int = Field(100, ge=40, le=400)           # the range's depth, north-south, in blocks
        cell: int = Field(2, ge=1, le=8)                 # terrain resolution: one column every this many blocks
        batch: int = Field(6, ge=1, le=64)               # terrain columns queued a call, so it stays quick
        peaks: list = [[25, 45, 100, 36], [70, 62, 112, 40], [118, 48, 104, 38], [158, 66, 92, 34],
                       [183, 38, 70, 28], [95, 85, 60, 26], [140, 20, 55, 26], [45, 15, 50, 24]]
        lakes: list = [[92, 22, 11], [170, 86, 10]]      # [x, z, radius] off the corner, each a lake
        snow_rel: int = 56                                # rel height, from its base: snow_block above this
        rock_rel: int = 42                                # rel height: bare rock with a snow dusting above this
        rocky_rel: int = 28                               # rel height: andesite/tuff/stone, no grass, above this
        flat_rel: int = 6                                 # rel height: grass with occasional trees above this
        biomes: dict = {"2": "minecraft:jagged_peaks", "1": "minecraft:snowy_slopes", "0": "minecraft:grove"}
        mspt_max: float = Field(45, ge=1, le=60)         # skip a tick's row, tree or biome work at and above this

    class Args(Args):
        players: Players = Field("@a", description="whom it is built beside, the first one named who is "
                                                   "online: @a, @r or names; not used with corner")
        corner: Place = Field("", description="its north-west corner instead: a saved place, x y z (its y not "
                                              "used), or a player; always in the overworld")
        away: int = Field(60, ge=0, le=1000, description="how far east of the player its west edge is, in "
                                                         "blocks; the range spans their z (not used with corner)")
        seed: int = Field(167, ge=0, le=1000000, description="picks the range's shape, its peaks, caves, lakes "
                                                             "and trees: the same seed builds the same range")
        trees: int = Field(260, ge=0, le=2000, description="how many spruce, at most, planted on its grass "
                                                           "slopes below y 130 (the old mountain_trees)")

    class State(State):
        phase: str = "rows"              # rows -> caves -> trees -> biomes -> done
        x0: int = 0
        z0: int = 0
        base: int = 0
        ph: list = []                     # the terrain's 6 random phases, drawn once from args.seed
        cell_i: int = 0                    # the next terrain column to queue (row-major: ix * nz + iz)
        cave: int = 0                     # the next cave to carve (0, 1)
        tree: int = 0                     # the next candidate tree to place
        biome: int = 0                     # the next biome run to ask for
        peak_max: int = 0
        zone: list = []                    # flat, row-major, width//cell by depth//cell: -1, 0, 1 or 2
        trees_list: list = []              # [x, y, z] candidates, capped and ordered once the rows are done
        placed: int = 0                    # spruce place feature actually placed
        biome_runs: list = []              # [zone, bx, a, b] precomputed once the rows are done

    def start(self, ctx):
        a, c = self.args, self.config
        if a.corner is not None:
            x0, z0 = math.floor(a.corner.pos[0]), math.floor(a.corner.pos[2])
        else:
            names = players(ctx, a.players)
            at = next((p for p in (ctx.world.player(n) for n in names) if p is not None), None)
            if at is None:
                self.result("no mountains: %s" % ("nobody is online" if a.players in ("@a", "@r")
                                                  else a.players + " is not online"), players=[])
                self.end()
                return
            x0, z0 = math.floor(at.pos[0]) + a.away, math.floor(at.pos[2]) - c.depth // 2
        self.state.x0, self.state.z0 = x0, z0
        self.blocks.forceload((x0, 0, z0 - 16), (x0 + c.width, 0, z0 + c.depth + 16))
        points = [(x0 + dx, z0 + dz) for dx, dz in ((0, 0), (c.width, 0), (0, c.depth), (c.width, c.depth),
                                                    (c.width // 2, c.depth // 2), (c.width // 4, c.depth // 2),
                                                    (3 * c.width // 4, c.depth // 2))]
        samples = sorted(y for y in (ctx.world.ground_y(x, z) for x, z in points) if y is not None)
        self.state.base = max(58, min(samples[len(samples) // 2], 80)) if samples else 64
        self.state.ph = [random.Random(a.seed).uniform(0, 2 * math.pi) for _ in range(6)]

    def tick(self, ctx):
        if ctx.world.mspt >= self.config.mspt_max:        # the server is busy: wait a tick
            return
        phase = self.state.phase
        if phase == "rows":
            self.build_row(ctx)
        elif phase == "caves":
            self.build_cave(ctx)
        elif phase == "trees":
            self.place_tree(ctx)
        elif phase == "biomes":
            self.place_biome(ctx)

    # ---- the terrain, a batch of columns a call (so one call stays well under the cut) -----

    def build_row(self, ctx):
        a, c = self.args, self.config
        x0, z0, base, ph = self.state.x0, self.state.z0, self.state.base, self.state.ph
        nx, nz = c.width // c.cell, c.depth // c.cell
        k = self.state.cell_i
        if k >= nx * nz:
            self.state.phase = "caves"
            return
        peaks = [[x0 + px, z0 + pz, pa, pr] for px, pz, pa, pr in c.peaks]
        lakes = [[x0 + lx, z0 + lz, lr] for lx, lz, lr in c.lakes]
        batch_zone, batch_trees, peak_max = [], [], self.state.peak_max
        for ix, iz in (divmod(j, nz) for j in range(k, min(k + c.batch, nx * nz))):
            x, z = x0 + c.cell * ix, z0 + c.cell * iz
            h = _height(x + 1, z + 1, base, peaks, ph)
            lake = _lake_of(x + 1, z + 1, lakes)
            if lake is not None:
                lx, lz, r, d = lake
                if d <= r:
                    batch_zone.append(-1)
                    self.blocks.fill((x, base - 8, z), (x + 1, base - 5, z + 1), "minecraft:stone")
                    self.blocks.fill((x, base - 4, z), (x + 1, base - 4, z + 1), "minecraft:gravel")
                    self.blocks.fill((x, base - 3, z), (x + 1, base + 14, z + 1), "minecraft:air")
                    self.blocks.fill((x, base - 3, z), (x + 1, base - 1, z + 1), "minecraft:water")
                    continue
                t = (d - r) / 7.0
                h = (base - 4) * (1 - t) + max(h, base + 8) * t
            if h < base + 2:
                batch_zone.append(-1)
                continue
            big_h = int(h)
            rel = big_h - base
            peak_max = max(peak_max, big_h)
            zone = 2 if rel >= c.snow_rel else (1 if rel >= c.rocky_rel else (0 if rel >= c.flat_rel else -1))
            batch_zone.append(zone)
            self.blocks.fill((x, base - 8, z), (x + 1, big_h - 4, z + 1), "minecraft:stone")
            if rel < c.rocky_rel:
                self.blocks.fill((x, big_h - 3, z), (x + 1, big_h - 1, z + 1), "minecraft:dirt")
                self.blocks.fill((x, big_h, z), (x + 1, big_h, z + 1), "minecraft:grass_block")
                if rel > 4 and _chance(a.seed, x, z, "tree_flat") < 0.07:
                    batch_trees.append([x, big_h + 1, z])
                continue
            m = _pick_rock(ix, iz)
            self.blocks.fill((x, big_h - 3, z), (x + 1, big_h - 1, z + 1), m)
            if rel >= c.snow_rel:
                self.blocks.fill((x, big_h - 1, z), (x + 1, big_h, z + 1), "minecraft:snow_block")
            elif rel >= c.rock_rel:
                self.blocks.fill((x, big_h, z), (x + 1, big_h, z + 1), m)
                layers = 2 + int(_chance(a.seed, x, z, "snow_layers") * 4)
                self.blocks.fill((x, big_h + 1, z), (x + 1, big_h + 1, z + 1), "minecraft:snow[layers=%d]" % layers)
            elif _chance(a.seed, x, z, "grassy") < 0.5:
                self.blocks.fill((x, big_h, z), (x + 1, big_h, z + 1), "minecraft:grass_block")
                if _chance(a.seed, x, z, "tree_rocky") < 0.15:
                    batch_trees.append([x, big_h + 1, z])
            else:
                self.blocks.fill((x, big_h, z), (x + 1, big_h, z + 1), m)
        self.state.zone = self.state.zone + batch_zone
        if batch_trees:
            self.state.trees_list = self.state.trees_list + batch_trees
        self.state.peak_max = peak_max
        self.state.cell_i = min(k + c.batch, nx * nz)

    # ---- two caves, carved from a nearby peak's slope towards it (main.py:178-207) ---------

    def build_cave(self, ctx):
        a, c = self.args, self.config
        x0, z0, base, ph = self.state.x0, self.state.z0, self.state.base, self.state.ph
        peaks = [[x0 + px, z0 + pz, pa, pr] for px, pz, pa, pr in c.peaks]
        i = self.state.cave
        if i >= 2 or len(peaks) < 3:
            self.state.phase = "trees"
            self.state.trees_list = self._pick_trees()
            return
        pk, ang = peaks[1 + i], (1.2, 0.6)[i]
        ex, ez = int(pk[0] + 60), int(pk[1])
        for r in range(10, 90):
            x, z = pk[0] + r * math.cos(ang), pk[1] + r * math.sin(ang)
            if _height(x, z, base, peaks, ph) - base < 16:
                ex, ez = int(x), int(z)
                break
        rng = random.Random("%d cave %d" % (a.seed, i))
        x, z, y = float(ex), float(ez), float(base + 13)
        d = math.hypot(pk[0] - x, pk[1] - z) or 1.0
        ux, uz = (pk[0] - x) / d, (pk[1] - z) / d
        for step in range(45 // 3):
            self.blocks.fill((int(x) - 2, int(y), int(z) - 2), (int(x) + 2, int(y) + 3, int(z) + 2),
                             "minecraft:air")
            x += ux * 3 + rng.uniform(-0.8, 0.8)
            z += uz * 3 + rng.uniform(-0.8, 0.8)
            y += 0.6
            if step % 5 == 2:
                self.blocks.fill((int(x) - 3, int(y), int(z) - 3), (int(x) + 3, int(y) + 4, int(z) + 3),
                                 "minecraft:air")
        self.state.cave = i + 1

    def _pick_trees(self):
        """Up to `trees` of the candidates found while building the terrain, in an order that is
        the same for the same seed every time (in place of a stateful random.shuffle)."""
        a = self.args
        cands = self.state.trees_list
        order = sorted(range(len(cands)),
                       key=lambda k: _chance(a.seed, cands[k][0], cands[k][2], "tree_pick %d" % cands[k][1]))
        return [cands[k] for k in order[:a.trees]]

    # ---- spruce, one place feature a call (main.py:209-216) --------------------------------

    def place_tree(self, ctx):
        trees = self.state.trees_list
        i = self.state.tree
        if i >= len(trees):
            self.state.phase = "biomes"
            self.state.biome_runs = self._biome_runs()
            return
        x, y, z = trees[i]
        self.ask("place feature minecraft:spruce %d %d %d" % (x, y, z), "tree_placed")
        self.state.tree = i + 1

    def tree_placed(self, ctx, reply):
        if "Placed" in (reply or ""):
            self.state.placed += 1

    # ---- biomes by height, in runs along z (main.py:218-235) -------------------------------

    def _biome_runs(self):
        a, c = self.args, self.config
        x0, z0 = self.state.x0, self.state.z0
        nx, nz = c.width // c.cell, c.depth // c.cell
        zone = self.state.zone

        def zone_at(ix, iz):
            if not (0 <= ix < nx and 0 <= iz < nz):
                return -1
            return zone[ix * nz + iz]

        runs = []
        for bx in range(x0, x0 + c.width, 4):
            here = []
            for bz in range(z0, z0 + c.depth, 4):
                z_ = max(zone_at((bx - x0) // c.cell + i, (bz - z0) // c.cell + j) for i in (0, 1) for j in (0, 1))
                if here and here[-1][0] == z_ and (bz - here[-1][1]) < 52:
                    here[-1][2] = bz + 3
                else:
                    here.append([z_, bz, bz + 3])
            runs += [[z_, bx, a_, b_] for z_, a_, b_ in here if z_ >= 0]
        return runs

    def place_biome(self, ctx):
        runs = self.state.biome_runs
        i = self.state.biome
        if i >= len(runs):
            self.finish(ctx)
            return
        base = self.state.base
        z_, bx, a_, b_ = runs[i]
        bio = self.config.biomes[str(z_)]
        self.ask("fillbiome %d %d %d %d %d %d %s" % (bx, base - 8, a_, bx + 3, base + 118, b_, bio), "biome_set")
        self.state.biome = i + 1

    def biome_set(self, ctx, reply):
        pass

    # ---- the result (not the engine's block-queue done(): biomes and trees run after it) --

    def finish(self, ctx):
        if self.state.phase == "done":
            return
        self.state.phase = "done"
        a, c = self.args, self.config
        x0, z0 = self.state.x0, self.state.z0
        self.result("mountain range built over x %d..%d, z %d..%d: its highest peak at y %d, %d trees" % (
            x0, x0 + c.width, z0, z0 + c.depth, self.state.peak_max, self.state.placed),
            corner="%d %d" % (x0, z0), size=[c.width, c.depth], peak=self.state.peak_max,
            trees=self.state.placed, seed=a.seed)
        self.end()
