"""The block queue: `self.blocks` (DESIGN2 §6).

    self.blocks.fill(a, b, "stone")                       # a box, corners included
    self.blocks.fill(a, b, "70% stone 30% andesite")      # a mix: picked per cube of `cell` blocks
    self.blocks.setblock(pos, "oak_door[half=lower]")
    self.blocks.replace((a, b), "snow > air; solid > 70% lava 30% keep")
    self.blocks.sphere(center, 6, "glass", hollow=True)
    self.blocks.forceload(a, b)                           # load those chunks while this lives
    self.blocks.done                                      # nothing of this instance's left to place

Content asks; the engine places, in its write step, a little each tick:

- every job is cut along chunk edges and into pieces of at most 32,768 blocks (one `fill` each);
- only chunks that are loaded (or that this instance forceloaded, waited for up to 30 s) are
  touched; an unloaded chunk is skipped and counted;
- protected boxes (VFG_PROTECTED, the old kit's /opt/vfg/protected.json: a list of
  {"name", "dim", "box": [x1, y1, z1, x2, y2, z2]}) and the bedrock floor are never touched;
- it slows down when the server does: nothing while mspt is 45 or more, a quarter at 35;
- `strict=True` (the default) places without block updates: water stays, sand does not fall;
- `undo=True` keeps a note of each piece, and when the instance ends puts air back wherever its
  blocks still stand (meant for blocks placed into air: an ice shell, a hut);
- when the instance's last job is placed, its `done(ctx)` method is called, if it has one.

What is queued and not yet placed is saved with the engine's state; an instance that ends drops
what it had not placed yet.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import os

from . import args as argtypes
from .io import Fill, SetBlock

log = logging.getLogger("vfg_engine")

MAX_PIECE = 32768
CMDS_PER_TICK = 64          # fills a tick, when the server is fine
COLUMNS_PER_TICK = 64       # chunk columns planned a tick that ask whether they are loaded
VISITS_PER_TICK = 4096      # columns looked at a tick at most (one outside a ball costs nothing)
MAX_RADIUS = 1024           # a sphere's; a nuke's crater is about 1000
BLOCKS_PER_TICK = 4 * MAX_PIECE
SLOW_MSPT, BUSY_MSPT = 35.0, 45.0
FORCELOAD_WAIT_S = 30.0
FLOOR = {"minecraft:overworld": -64, "minecraft:the_nether": 0}      # the bedrock floor
HEIGHTS = {"minecraft:overworld": (-64, 319), "minecraft:the_nether": (0, 255), "minecraft:the_end": (0, 255)}
DEFAULT_PROTECTED = "/opt/vfg/protected.json"


def block_or_mix(text) -> list[tuple[str, float]]:
    """One block, with any [states] and {nbt} as written (a sign's text, a chest's items), or a mix
    ("70% stone 30% andesite"), as [(block, weight)]. Read once per text (a shape's thousand fills
    of one mix read it once)."""
    return list(_parsed(str(text or "").strip()))


@functools.lru_cache(maxsize=4096)
def _parsed(s: str) -> tuple:
    head = s.split("[", 1)[0].split("{", 1)[0]
    if "%" not in s and head and " " not in head and ("[" in s or "{" in s):
        return ((argtypes._id("block", argtypes.registries.BLOCKS, states=True)(s), 1.0),)
    return tuple(argtypes.mix(s))


@functools.lru_cache(maxsize=1024)
def _filter(text: str) -> str:
    return argtypes._block(text, tags=True)


def _cell(v) -> tuple[int, int, int]:
    if not isinstance(v, (tuple, list)):
        v = v.pos
    x, y, z = v
    return math.floor(x), math.floor(y), math.floor(z)


def _box(a, b):
    ax, ay, az = _cell(a)
    bx, by, bz = _cell(b)
    return [min(ax, bx), min(ay, by), min(az, bz)], [max(ax, bx), max(ay, by), max(az, bz)]


def _volume(lo, hi) -> int:
    return (hi[0] - lo[0] + 1) * (hi[1] - lo[1] + 1) * (hi[2] - lo[2] + 1)


def subtract(lo, hi, cut_lo, cut_hi) -> list[tuple[list, list]]:
    """The box lo..hi without the box cut_lo..cut_hi, as up to six boxes."""
    if any(cut_hi[i] < lo[i] or cut_lo[i] > hi[i] for i in range(3)):
        return [(lo, hi)]
    out, lo, hi = [], list(lo), list(hi)
    for i in range(3):
        if lo[i] < cut_lo[i]:
            a, b = list(lo), list(hi)
            b[i] = cut_lo[i] - 1
            out.append((a, b))
            lo[i] = cut_lo[i]
        if hi[i] > cut_hi[i]:
            a, b = list(lo), list(hi)
            a[i] = cut_hi[i] + 1
            out.append((a, b))
            hi[i] = cut_hi[i]
    return out


class Protected:
    """The protected boxes, read again whenever the file changes."""

    def __init__(self, path: str | None):
        self.path = path
        self._stamp = None
        self.boxes: list[tuple[str, list, list]] = []      # (dim, lo, hi)

    def get(self, dim: str) -> list[tuple[list, list]]:
        if not self.path:
            return []
        try:
            stamp = os.stat(self.path).st_mtime_ns
        except OSError:
            self._stamp, self.boxes = None, []
            return []
        if stamp != self._stamp:
            boxes = []
            try:
                with open(self.path, encoding="utf-8") as f:
                    for e in json.load(f):
                        b = [int(v) for v in e["box"]]
                        d = e.get("dim") or "minecraft:overworld"
                        d = d if ":" in d else "minecraft:" + d
                        boxes.append((d, [min(b[0], b[3]), min(b[1], b[4]), min(b[2], b[5])],
                                      [max(b[0], b[3]), max(b[1], b[4]), max(b[2], b[5])]))
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                log.error("protected file %s unreadable (%s): nothing is protected", self.path, exc)
                boxes = []
            self._stamp, self.boxes = stamp, boxes
        return [(lo, hi) for d, lo, hi in self.boxes if d == dim]

    def covers(self, dim: str, pos) -> bool:
        x, y, z = _cell(pos)
        return any(lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1] and lo[2] <= z <= hi[2]
                   for lo, hi in self.get(dim))


def _rings(job) -> int:
    """How many rings of chunk columns round its middle a sphere's job covers."""
    lo, hi = job["lo"], job["hi"]
    cx0, cz0 = int(math.floor(job["center"][0])) >> 4, int(math.floor(job["center"][2])) >> 4
    return max(cx0 - (lo[0] >> 4), (hi[0] >> 4) - cx0, cz0 - (lo[2] >> 4), (hi[2] >> 4) - cz0, 0)


def ring_cell(n: int) -> tuple[int, int]:
    """The n-th cell of the square rings round (0, 0), nearest ring first: 0 is (0, 0), then the
    8 round it, then the 16 round those..."""
    if n == 0:
        return 0, 0
    k = (math.isqrt(n) + 1) // 2                 # the ring: (2k-1)^2 <= n < (2k+1)^2
    if (2 * k - 1) ** 2 > n:
        k -= 1
    i = n - (2 * k - 1) ** 2                     # 0 .. 8k-1 along the ring
    side, off = divmod(i, 2 * k)
    if side == 0:
        return -k + off, -k
    if side == 1:
        return k, -k + off
    if side == 2:
        return k - off, k
    return -k, k - off


# ---- the content side: self.blocks -------------------------------------------------------------

class Blocks:
    def __init__(self, rt, handle):
        self._rt, self._h = rt, handle

    def _dim(self, at, dim):
        if dim:
            return dim
        d = getattr(at, "dim", None)
        if isinstance(d, str):
            return d
        return self._h._dim()

    def _add(self, job):
        job["owner"] = self._h.record.iid
        self._rt.block_queue.add(job)

    def fill(self, a, b, block, replace=None, strict=True, mode="replace", undo=False, cell=2, dim=None):
        """Fill the box a..b with a block or a mix ("70% stone 30% andesite"); `replace` a block
        or #tag to change only those; mode "replace", "keep" (only air), "destroy", "hollow" or
        "outline"; a mix is picked per cube of `cell` blocks."""
        with self._rt.guard.engine_code():
            if mode not in ("replace", "keep", "destroy", "hollow", "outline"):
                raise ValueError("mode is replace, keep, destroy, hollow or outline, not %r" % mode)
            lo, hi = _box(a, b)
            choices = block_or_mix(block)
            filters = [_filter(replace)] if replace else None
            self._add({"kind": "box", "dim": self._dim(a, dim), "lo": lo, "hi": hi, "mix": choices,
                       "replace": filters, "strict": bool(strict), "mode": mode, "undo": bool(undo),
                       "cell": max(1, int(cell)), "seed": str(block)})

    def setblock(self, pos, block, strict=True, undo=False, dim=None):
        with self._rt.guard.engine_code():
            p = _cell(pos)
            choices = block_or_mix(block)
            self._add({"kind": "box", "dim": self._dim(pos, dim), "lo": list(p), "hi": list(p), "mix": choices,
                       "replace": None, "strict": bool(strict), "mode": "replace", "undo": bool(undo), "cell": 1,
                       "seed": str(block)})

    def replace(self, box, rules, undo=False, cell=4, dim=None):
        """Apply replace rules ("snow > air; solid > 70% lava 30% keep") in order to the box."""
        with self._rt.guard.engine_code():
            a, b = box
            lo, hi = _box(a, b)
            parsed = argtypes.rules(rules)
            for rule in parsed:
                self._add({"kind": "box", "dim": self._dim(a, dim), "lo": lo, "hi": hi, "mix": list(rule.to),
                           "replace": list(rule.froms), "strict": True, "mode": "replace", "undo": bool(undo),
                           "cell": max(1, int(cell)), "seed": rule.text})

    def sphere(self, center, radius, block, hollow=False, undo=False, replace=None, dim=None):
        """A ball of `radius` blocks round `center` (only its shell when hollow)."""
        with self._rt.guard.engine_code():
            c = [float(v) for v in (center.pos if hasattr(center, "pos") and not isinstance(center, (tuple, list))
                                    else center)]
            r = float(radius)
            if not 0 < r <= MAX_RADIUS:
                raise ValueError("a sphere's radius is above 0 and at most %d, not %r" % (MAX_RADIUS, r))
            choices = block_or_mix(block)
            filters = [_filter(replace)] if replace else None
            lo = [int(math.floor(c[i] - r)) for i in range(3)]
            hi = [int(math.floor(c[i] + r)) for i in range(3)]
            self._add({"kind": "sphere", "dim": self._dim(center, dim), "lo": lo, "hi": hi, "center": c, "radius": r,
                       "hollow": bool(hollow), "mix": choices, "replace": filters, "strict": True, "mode": "replace",
                       "undo": bool(undo), "cell": 1, "seed": str(block)})

    def forceload(self, a, b, dim=None):
        """Keep the chunks under a..b loaded while this instance lives."""
        from .io import Forceload
        with self._rt.guard.engine_code():
            (x1, _, z1), (x2, _, z2) = _box(a, b)
            d = self._dim(a, dim)
            corners = [d, (x1 >> 4) * 16, (z1 >> 4) * 16, (x2 >> 4) * 16, (z2 >> 4) * 16]
            if (x2 // 16 - x1 // 16 + 1) * (z2 // 16 - z1 // 16 + 1) > 256:
                raise ValueError("forceload at most 256 chunks at once")
            self._rt.queue(Forceload(d, (corners[1], corners[2]), (corners[3], corners[4]), add=True))
            self._h.record.owned.forceloads.append(corners)

    @property
    def done(self) -> bool:
        return not self._rt.block_queue.pending(self._h.record.iid)

    @property
    def pending(self) -> int:
        """Jobs of this instance not yet placed."""
        return self._rt.block_queue.pending(self._h.record.iid)


# ---- the engine side ---------------------------------------------------------------------------

class BlockQueue:
    def __init__(self, rt):
        self.rt = rt
        self.jobs: list[dict] = []
        self.protected = Protected(getattr(rt.settings, "protected", None))
        self._loaded: dict = {}
        self._inflight: dict[str, int] = {}         # owner -> pieces sent, result not back yet
        self._jobs_of: dict[str, int] = {}          # owner -> jobs not finished
        self._asked = False

    def add(self, job: dict):
        job.setdefault("next", 0)
        job.setdefault("skipped", 0)
        job["columns"] = self._columns(job)
        self.jobs.append(job)
        owner = job.get("owner")
        if owner:
            self._jobs_of[owner] = self._jobs_of.get(owner, 0) + 1

    def pending(self, owner: str) -> int:
        return self._jobs_of.get(owner, 0) + self._inflight.get(owner, 0)

    def drop(self, owner: str):
        n = self._jobs_of.pop(owner, 0)
        if n:
            self.jobs = [j for j in self.jobs if j.get("owner") != owner]
            log.info("%s ended with %d block job(s) not placed; dropped", owner, n)

    def undo(self, entries):
        """Put air back where these pieces' blocks still stand (an instance with undo=True ended)."""
        for dim, a, b, block in entries:
            self.jobs.append({"owner": None, "kind": "pieces", "dim": dim, "next": 0, "skipped": 0,
                              "pieces": [[a, b, block]], "undoing": True, "columns": 1})

    @staticmethod
    def _columns(job) -> int:
        if job["kind"] == "pieces":
            return len(job["pieces"])
        lo, hi = job["lo"], job["hi"]
        if job["kind"] == "sphere":         # rings of chunk columns round the middle, nearest first
            k = _rings(job)
            return (2 * k + 1) ** 2
        return ((hi[0] >> 4) - (lo[0] >> 4) + 1) * ((hi[2] >> 4) - (lo[2] >> 4) + 1)

    # ---- each tick ---------------------------------------------------------------------------

    def step(self):
        """Send this tick's share of block work (the engine's write step)."""
        if not self.jobs:
            return
        mspt = self.rt.mspt()
        if mspt >= BUSY_MSPT:
            return
        share = 0.25 if mspt >= SLOW_MSPT else 1.0
        cmds, blocks = int(CMDS_PER_TICK * share), int(BLOCKS_PER_TICK * share)
        columns, visits = COLUMNS_PER_TICK, VISITS_PER_TICK
        self._loaded = {}
        for job in self.jobs:
            while cmds > 0 and blocks > 0 and columns > 0 and visits > 0 and not job.get("finished"):
                if job["next"] >= job["columns"]:
                    self._finish(job)
                    break
                self._asked = False
                pieces = self._column(job, job["next"])
                visits -= 1
                if self._asked:                       # it asked whether a chunk is loaded
                    columns -= 1
                if pieces is None:                    # waiting for a forceloaded chunk
                    break
                for lo, hi, block, filt in pieces:
                    self._send(job, lo, hi, block, filt)
                    cmds -= 1
                    blocks -= _volume(lo, hi)
                job["next"] += 1
            if cmds <= 0 or blocks <= 0 or columns <= 0 or visits <= 0:
                break
        if any(j.get("finished") for j in self.jobs):
            self.jobs = [j for j in self.jobs if not j.get("finished")]

    def _finish(self, job):
        job["finished"] = True
        owner = job.get("owner")
        if owner and self._jobs_of.get(owner):
            self._jobs_of[owner] -= 1
            if not self._jobs_of[owner]:
                del self._jobs_of[owner]
        if job.get("skipped"):
            log.warning("%s: %d chunk(s) of a block job were not loaded and were left as they were",
                        job.get("owner") or "undo", job["skipped"])
        self._maybe_done(job.get("owner"))

    def _maybe_done(self, owner):
        if owner and not self.pending(owner) and owner in self.rt.records:
            rec = self.rt.records[owner]
            self.rt.later(lambda: self.rt.call(rec, "done") if rec.iid in self.rt.records else None)

    def _send(self, job, lo, hi, block, filt):
        dim, owner = job["dim"], job.get("owner")
        if lo == hi and filt is None and job.get("mode", "replace") == "replace":
            op = SetBlock(dim, tuple(lo), block, job.get("strict", True))
        else:
            op = Fill(dim, tuple(lo), tuple(hi), block, filt, job.get("strict", True), job.get("mode", "replace"))
        if owner:
            self._inflight[owner] = self._inflight.get(owner, 0) + 1
        if job.get("undo") and owner in self.rt.records and block != "minecraft:air":
            self.rt.records[owner].owned.undo.append([dim, list(lo), list(hi), block])

        def took(op, result, owner=owner):
            if owner:
                self._inflight[owner] = max(0, self._inflight.get(owner, 1) - 1)
                if not self._inflight[owner]:
                    del self._inflight[owner]
            words = (result.error or result.reply or "").lower()
            if not result.ok and "no blocks were filled" not in words and "could not set" not in words:
                log.warning("%s: a block piece at %s..%s did not take: %s", owner or "undo", op.a if
                            isinstance(op, Fill) else op.pos, op.b if isinstance(op, Fill) else "",
                            result.error or result.reply)
            if owner and not self._jobs_of.get(owner):
                self._maybe_done(owner)
        self.rt.queue(op, took)

    def _is_loaded(self, dim, cx, cz) -> bool:
        key = (dim, cx, cz)
        if key not in self._loaded:
            self._asked = True
            try:
                self._loaded[key] = bool(self.rt.io.loaded(dim, cx * 16, cz * 16))
            except Exception as exc:
                log.debug("loaded() failed (%s): taken as loaded", exc)
                self._loaded[key] = True
        return self._loaded[key]

    def _forceloaded_by(self, owner, dim, cx, cz) -> bool:
        rec = self.rt.records.get(owner) if owner else None
        if rec is None:
            return False
        return any(d == dim and x1 >> 4 <= cx <= x2 >> 4 and z1 >> 4 <= cz <= z2 >> 4
                   for d, x1, z1, x2, z2 in rec.owned.forceloads)

    def _column(self, job, index):
        """The pieces of one chunk column of a job: [(lo, hi, block, filter)]; None to wait."""
        dim = job["dim"]
        if job["kind"] == "pieces":
            a, b, block = job["pieces"][index]
            return [(a, b, "minecraft:air", block)]
        lo, hi = job["lo"], job["hi"]
        if job["kind"] == "sphere":
            cx0, cz0 = int(math.floor(job["center"][0])) >> 4, int(math.floor(job["center"][2])) >> 4
            dx, dz = ring_cell(index)
            cx, cz = cx0 + dx, cz0 + dz
            if not (lo[0] >> 4 <= cx <= hi[0] >> 4 and lo[2] >> 4 <= cz <= hi[2] >> 4):
                return []
            # a column wholly outside the ball's disc: nothing to do, nothing to ask
            nx = min(max(job["center"][0], cx * 16), cx * 16 + 16)
            nz = min(max(job["center"][2], cz * 16), cz * 16 + 16)
            if (nx - job["center"][0]) ** 2 + (nz - job["center"][2]) ** 2 > job["radius"] ** 2:
                return []
        else:
            ncz = (hi[2] >> 4) - (lo[2] >> 4) + 1
            cx, cz = (lo[0] >> 4) + index // ncz, (lo[2] >> 4) + index % ncz
        if not self._is_loaded(dim, cx, cz):
            if self._forceloaded_by(job.get("owner"), dim, cx, cz):
                job.setdefault("waiting_since", self.rt.now)
                if self.rt.now - job["waiting_since"] < FORCELOAD_WAIT_S:
                    return None
            job["skipped"] += 1
            job.pop("waiting_since", None)
            return []
        job.pop("waiting_since", None)
        bottom, top = HEIGHTS.get(dim, (-64, 319))
        floor = FLOOR.get(dim)
        c_lo = [max(lo[0], cx * 16), max(lo[1], bottom + (1 if floor is not None else 0)), max(lo[2], cz * 16)]
        c_hi = [min(hi[0], cx * 16 + 15), min(hi[1], top), min(hi[2], cz * 16 + 15)]
        if any(c_lo[i] > c_hi[i] for i in range(3)):
            return []
        boxes = [(c_lo, c_hi)]
        for p_lo, p_hi in self.protected.get(dim):
            boxes = [part for b in boxes for part in subtract(b[0], b[1], p_lo, p_hi)]
        out = []
        for b_lo, b_hi in boxes:
            if job["kind"] == "sphere":
                out.extend(self._sphere_runs(job, b_lo, b_hi))
            else:
                out.extend(self._box_pieces(job, b_lo, b_hi))
        return out

    def _box_pieces(self, job, lo, hi):
        mix, filters = job["mix"], job.get("replace") or [None]
        out = []
        if len(mix) == 1:
            block = mix[0][0]
            if block == "keep":
                return []
            layers = max(1, MAX_PIECE // ((hi[0] - lo[0] + 1) * (hi[2] - lo[2] + 1)))
            for y in range(lo[1], hi[1] + 1, layers):
                p_lo, p_hi = [lo[0], y, lo[2]], [hi[0], min(hi[1], y + layers - 1), hi[2]]
                out.extend((p_lo, p_hi, block, f) for f in filters)
            return out
        n = job.get("cell", 2)
        for x in range(lo[0] - lo[0] % n, hi[0] + 1, n):
            for y in range(lo[1] - lo[1] % n, hi[1] + 1, n):
                for z in range(lo[2] - lo[2] % n, hi[2] + 1, n):
                    block = argtypes.pick(mix, "%s %d %d %d" % (job.get("seed", ""), x, y, z))
                    if block == "keep":
                        continue
                    p_lo = [max(x, lo[0]), max(y, lo[1]), max(z, lo[2])]
                    p_hi = [min(x + n - 1, hi[0]), min(y + n - 1, hi[1]), min(z + n - 1, hi[2])]
                    out.extend((p_lo, p_hi, block, f) for f in filters)
        return out

    def _sphere_runs(self, job, lo, hi):
        """The ball inside lo..hi (one chunk column's part of it): for a solid ball of one block,
        the core every column shares as one box (in slices under 32,768), then what is left of
        each column; runs of the same height next to each other along z are one fill. A hollow
        ball is its shell only."""
        cx, cy, cz = job["center"]
        r = job["radius"]
        inner = r - 1.0
        mix, filters = job["mix"], job.get("replace") or [None]
        runs: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for x in range(lo[0], hi[0] + 1):
            for z in range(lo[2], hi[2] + 1):
                d2 = (x + 0.5 - cx) ** 2 + (z + 0.5 - cz) ** 2
                if d2 > r * r:
                    continue
                h = math.sqrt(r * r - d2)
                y0, y1 = max(lo[1], int(math.floor(cy - h))), min(hi[1], int(math.floor(cy + h)))
                cell = [(y0, y1)]
                if job.get("hollow") and d2 < inner * inner:
                    hi_in = math.sqrt(inner * inner - d2)
                    a, b = int(math.floor(cy - hi_in)) + 1, int(math.floor(cy + hi_in)) - 1
                    cell = [(y0, min(y1, a - 1)), (max(y0, b + 1), y1)]
                cell = [(a, b) for a, b in cell if a <= b]
                if cell:
                    runs[(x, z)] = cell
        out = []
        full = (hi[0] - lo[0] + 1) * (hi[2] - lo[2] + 1)
        if not job.get("hollow") and len(mix) == 1 and len(runs) == full and runs:
            core_lo = max(c[0][0] for c in runs.values())
            core_hi = min(c[0][1] for c in runs.values())
            if core_lo <= core_hi and mix[0][0] != "keep":
                layers = max(1, MAX_PIECE // full)
                for y in range(core_lo, core_hi + 1, layers):
                    out.extend(([lo[0], y, lo[2]], [hi[0], min(core_hi, y + layers - 1), hi[2]], mix[0][0], f)
                               for f in filters)
                runs = {k: [(a, b) for a, b in ((c[0][0], core_lo - 1), (core_hi + 1, c[0][1])) if a <= b]
                        for k, c in runs.items()}
        for x in range(lo[0], hi[0] + 1):
            z = lo[2]
            while z <= hi[2]:
                here = runs.get((x, z))
                if not here:
                    z += 1
                    continue
                end = z
                while end + 1 <= hi[2] and runs.get((x, end + 1)) == here:
                    end += 1
                for ya, yb in here:
                    block = argtypes.pick(mix, "%s %d %d %d" % (job.get("seed", ""), x, ya, z))
                    if block != "keep":
                        out.extend(([x, ya, z], [x, yb, end], block, f) for f in filters)
                z = end + 1
        return out

    # ---- saving ------------------------------------------------------------------------------

    def to_list(self) -> list[dict]:
        return [dict(j) for j in self.jobs]

    def load(self, jobs, owners):
        self.jobs = [j for j in (jobs or []) if (j.get("owner") is None or j.get("owner") in owners)
                     and not j.get("finished")]
        self._jobs_of = {}
        for j in self.jobs:
            j["mix"] = [tuple(m) for m in j.get("mix", [])]
            if j.get("owner"):
                self._jobs_of[j["owner"]] = self._jobs_of.get(j["owner"], 0) + 1
