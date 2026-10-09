"""Items a player holds and uses: a carrot on a stick with its own name and one custom_data
id, given at start, kept, or never; and its clicks, holds and charges.

    for p in loop(items.clicks(v["player"], "Railgun", cooldown=v["cooldown"])): fire(p)
    for p in loop(items.holds(v["player"], "Gatling Gun")): shoot(p)          # each pass while held down
    for p in loop(items.charged(v["player"], "Spirit Bomb", v["charge"], v["cooldown"])): throw(p)

A right-click with a carrot on a stick adds 1 to the statistic
minecraft.used:minecraft.carrot_on_a_stick, and holding the button adds 1 about every 0.2 s.
Every script reads that one statistic from the objective vfg_click, which nothing resets or
removes, as a change since it last looked, and in the same command only for players whose
click went to its item: in the main hand, or in the off hand under no other carrot on a
stick. A player who was not holding it at the last look starts again from 0, so two scripts
on one player never fire for each other's item. The item's id is its name in snake_case
(custom_data {vfg_item:"spirit_bomb"}).
"""
import json
import re
import threading
import time

import mc
from .loop import who
from .values import now

CLICK = ("vfg_click", "minecraft.used:minecraft.carrot_on_a_stick")
SNEAK = ("vfg_sneak", "minecraft.custom:minecraft.sneak_time")
HOLD_GAP = 0.4      # a hold is over when no use has come for this long
KEEP_EVERY = 5.0    # give="keep" gives a lost item back within this many seconds

# 26.3 glues the answers of an "execute as @a" together ("Steve has 5 [x]Alex has 7 [x]") and
# says nothing for a player with no score; a name starts the reply or follows a "]".
_SCORE = re.compile(r"(?:^|\]|\n)\s*([A-Za-z0-9_]{1,16}) has (-?\d+) \[")
_UNSET = re.compile(r"(?:^|\]|\n)\s*Can't get value of \S+ for ([A-Za-z0-9_]{1,16}); none is set")
_made = set()
_lock = threading.Lock()


def item_id(name):
    """The id an item is known by, from its name: "Spirit Bomb" -> "spirit_bomb"."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "item"


def match(name):
    """The item predicate for an item, for clear, execute if items and the like:
    *[custom_data~{vfg_item:"spirit_bomb"}]."""
    return '*[custom_data~{vfg_item:"%s"}]' % item_id(name)


def item(name, color="aqua", model=None):
    """The item itself, for give: a glinting, unbreakable carrot on a stick named `name`,
    looking like `model` (an item id such as "nether_star") when given."""
    parts = ['custom_data={vfg_item:"%s"}' % item_id(name),
             "item_name=%s" % json.dumps({"text": str(name), "color": color}),
             "enchantment_glint_override=true", "unbreakable={}"]
    if model:
        parts.append('item_model="%s"' % (model if ":" in model else "minecraft:" + model))
    return "carrot_on_a_stick[%s]" % ",".join(parts)


def held(player, name):
    """True if the player holds the item in either hand, in one command."""
    return "passed" in mc.rcon("execute if items entity %s weapon.* %s" % (player, match(name))).lower()


def has(player, name):
    """True if the player has the item anywhere in their inventory or either hand."""
    m = match(name)
    return "passed" not in mc.rcon("execute unless items entity %s container.* %s unless items entity %s "
                                   "weapon.offhand %s" % (player, m, player, m)).lower()


def give(player, name, color="aqua", model=None):
    """Give the item unless the player already has it, in one command. True if it was given."""
    m = match(name)
    reply = mc.rcon("execute unless items entity %s container.* %s unless items entity %s weapon.offhand %s "
                    "run give %s %s" % (player, m, player, m, player, item(name, color, model)))
    return "gave" in reply.lower()


def take(player, name):
    """Take every one of the item from a player."""
    mc.rcon("clear %s %s" % (player, match(name)))


def _objective(obj, criterion):
    with _lock:
        if obj in _made:
            return
        _made.add(obj)
    mc.rcon("scoreboard objectives add %s %s" % (obj, criterion))


class Stat:
    """One player statistic, read as the change since this Stat last looked, from an
    objective every script shares (never reset, never removed). Stat(*items.CLICK),
    Stat(*items.SNEAK), or any criterion."""

    def __init__(self, objective, criterion):
        self.objective, self.criterion = objective, criterion
        self.last = {}
        self.refused = None     # the server's answer when it refused the last command
        self.seeded = 0.0

    def scores(self, cond=""):
        """{name: score} for every online player, or those `cond` ("if items entity @s ...")
        passes for, in one command."""
        _objective(self.objective, self.criterion)
        if time.time() - self.seeded > 2.0:
            # Every player gets a score (adding 0 changes none), so a player is never missing
            # for want of one, and their first use counts.
            self.seeded = time.time()
            mc.rcon("scoreboard players add @a %s 0" % self.objective)
        reply = mc.rcon("execute as @a %srun scoreboard players get @s %s" % (cond + " " if cond else "", self.objective))
        if "unknown scoreboard objective" in reply.lower():
            with _lock:
                _made.discard(self.objective)       # someone removed it: make it again
            _objective(self.objective, self.criterion)
            return {}
        if "<--[HERE]" in reply:
            self.refused = reply
            return {}
        out = {m.group(1): int(m.group(2)) for m in _SCORE.finditer(reply)}
        for m in _UNSET.finditer(reply):
            out.setdefault(m.group(1), 0)
        return out

    def changes(self, cond=""):
        """{name: change since the last look} for the players the command reports. A player
        new to the reply, or back in it, counts 0 this time; one gone from it is forgotten."""
        cur = self.scores(cond)
        out = {p: (s - self.last[p] if p in self.last and s >= self.last[p] else 0) for p, s in cur.items()}
        self.last = cur
        return out

    def forget(self):
        """Start every player again from 0: what happened before now is not counted."""
        self.last = {}


class _Watch:
    """One item's uses for the players a player value names, and giving it to them."""

    def __init__(self, player, name, give, color, model):
        self.player, self.name, self.mode = player, name, give
        self.color, self.model = color, model
        self.stat = Stat(*CLICK)
        m = match(name)
        # Its use is counted when it is in the main hand, or in the off hand under anything
        # but another carrot on a stick (which takes the click first). If the server refuses
        # that, in either hand.
        self.plain = "if items entity @s weapon.* %s" % m
        self.cond = self.plain + (' unless items entity @s weapon.mainhand '
                                  'minecraft:carrot_on_a_stick[!custom_data~{vfg_item:"%s"}]' % item_id(name))
        self.given, self.gave_at = set(), {}
        if give != "never":
            mc.on_stop(self._take_back)

    def _take_back(self):
        for p in sorted(self.given):
            take(p, self.name)

    def uses(self):
        """{name: uses since the last look} for the players this watch is for; gives the item
        to any of them who should have it."""
        people = who(self.player)
        if self.mode in ("start", "keep"):
            t = time.time()
            for p in people:
                first = p not in self.gave_at
                if first or (self.mode == "keep" and t - self.gave_at[p] >= KEEP_EVERY):
                    self.gave_at[p] = t
                    if give(p, self.name, self.color, self.model):
                        self.given.add(p)
                        mc.log("gave", self.name, "to", p)
                    elif first:
                        self.given.add(p)
        if not people:
            self.stat.forget()
            return {}
        got = self.stat.changes(self.cond)
        if self.stat.refused and self.cond != self.plain:
            mc.log("the server refused the click check, using the plainer one:", self.stat.refused[-120:])
            self.cond, self.stat.refused = self.plain, None
        return {p: n for p, n in got.items() if p in people}

    def pause(self, every):
        return max(0.05, now(every)) if who(self.player) else 1.0


def _bar(player, text, color):
    mc.rcon("title %s actionbar %s" % (player, json.dumps({"text": text, "color": color})))


def clicks(player="@a", name="", cooldown=0, give="keep", every=0.1, color="aqua", model=None):
    """Yield a player's name each time they use the item called `name`: a right-click, and
    again about every 0.2 s while the button is held. Within `cooldown` s of the last one a
    click only shows the time left. give: "start" gives the item once to each player when
    first seen, "keep" also gives it back within 5 s when lost (the default), "never" not at
    all; given items are taken back when the script stops. Uses made while the loop's body
    ran are not counted. A live cooldown that drops takes effect at once."""
    w = _Watch(player, name, give, color, model)
    done = {}
    while not mc.stopping():
        due = []
        for p, n in w.uses().items():
            if n <= 0:
                continue
            left = done.get(p, -1e9) + now(cooldown) - time.time()
            if left > 0:
                _bar(p, "%s: %.1f s" % (name, left), "gray")
            else:
                due.append(p)
        for p in due:
            yield p
            done[p] = time.time()
        if due:
            w.stat.forget()
        time.sleep(w.pause(every))


def holds(player="@a", name="", give="keep", every=0.1, color="aqua", model=None):
    """Yield a player's name on every pass (each `every` s) while they hold the use button
    down with the item called `name`, from the first use until no use has come for 0.4 s."""
    w = _Watch(player, name, give, color, model)
    last = {}
    while not mc.stopping():
        t = time.time()
        for p, n in w.uses().items():
            if n > 0:
                last[p] = t
        for p in list(last):
            if t - last[p] > HOLD_GAP:
                del last[p]
            else:
                yield p
        time.sleep(w.pause(every))


def charged(player="@a", name="", charge=1.0, cooldown=0, give="keep", every=0.1, color="aqua", model=None):
    """Yield a player's name once they have held the use button down with the item called
    `name` for `charge` s, while an actionbar shows the charge filling; letting go sooner
    cancels it. After that, nothing until they let go, and `cooldown` s have passed since the
    loop's body finished."""
    w = _Watch(player, name, give, color, model)
    start, last, done, spent, shown = {}, {}, {}, set(), {}
    while not mc.stopping():
        t = time.time()
        for p, n in w.uses().items():
            if n <= 0:
                continue
            if p not in last or t - last[p] > HOLD_GAP:
                start[p] = t
            last[p] = t
        fired = False
        for p in list(last):
            if t - last[p] > HOLD_GAP:
                for d in (start, last, shown):
                    d.pop(p, None)
                spent.discard(p)
                continue
            if p in spent:
                continue
            left = done.get(p, -1e9) + now(cooldown) - t
            if left > 0:
                if t - shown.get(p, 0.0) >= 0.5:
                    shown[p] = t
                    _bar(p, "%s: %.1f s" % (name, left), "gray")
                start[p] = t
                continue
            full = max(0.0, float(now(charge)))
            if t - start[p] >= full:
                spent.add(p)
                yield p
                done[p] = time.time()
                fired = True
                break
            if t - shown.get(p, 0.0) >= 0.25:
                shown[p] = t
                k = int(10 * (t - start[p]) / full) if full else 10
                _bar(p, "%s %s" % (name, "■" * k + "□" * (10 - k)), color)
        if not fired:
            time.sleep(w.pause(every))


_sneak = None


def sneaking(player="@a"):
    """The set of players (of those a player value names) who crouched since the last call,
    from the shared objective vfg_sneak (minecraft.custom:minecraft.sneak_time), in one
    command. Call it from one place in a script; a second caller needs its own
    items.Stat(*items.SNEAK)."""
    global _sneak
    if _sneak is None:
        _sneak = Stat(*SNEAK)
    people = who(player)
    return {p for p, n in _sneak.changes().items() if n > 0 and p in people}
