"""What the engine takes from the datapack's queue, and what it leaves (measured on 26.3 by the
mc builder, 2026-10-13):

1. **A page handed over twice after a crash.** The datapack takes an event off its queue only on
   the engine's next read, so after a crash up to a page comes again, and McIo's dedupe by number
   lives in memory. The engine saves `last_n`, the number of the last event it took, with its
   state. On start-up it gives it to the Io before opening it (McIo's `last_n`), and in its own
   first batch of events it drops every one numbered up to it.
   A new world restarts the datapack's counter at 0. McIo finds that when it opens (its counter
   is behind the `last_n` it was given) and lowers its `last_n`: the engine sees that and drops
   nothing. For an Io that does not say, a first event more than REPLAY_WINDOW below the saved
   number cannot be a page handed again, so it is taken as a new counter, and nothing is dropped.
2. **Stale input.** Events queued while the engine was down come in its first read, each with
   its game time. A use, hit, hurt or kill more than `stale_s` (VFG_ENGINE_STALE_S, 5) older than
   the snapshot's game time is not acted on, and the count is logged. A death, respawn or milk is
   kept whatever its age: marks depend on them.
3. **The engine's own damage, reported back.** A `Damage` op with `by=<player>` comes back from
   26.3 as that player's own `hit` on the target (and the target's `hurt`, and a `kill`), so an
   ability that answers a hit with damage would trigger itself for ever. Each such op sent is
   remembered for ECHO_S. A `hit` matching one (same player, same target) is the engine's own
   and is not handed to content; a `hurt` matching one (that target, hurt by that player, or by
   no one named) or a `kill` matching one is handed over flagged `ev.engine = True`. A mob is
   dead before the datapack reads it, so its `kill`, and the killing blow's `hit`, name no
   target: such a hit or kill matches by the player alone. Each op matches one event of each
   kind, once. (A damage that does not
   land, inside the target's invulnerable ticks, may leave its match unused: then one real hit by
   that player on that target within ECHO_S is taken for it.)
"""

from __future__ import annotations

import dataclasses
import logging

from .io import Damage

log = logging.getLogger("vfg_engine")

GAME_TICKS_PER_S = 20
STALE_KINDS = ("use", "hit", "hurt", "kill")
REPLAY_WINDOW = 1000       # a page handed again is at most 12 events; 600 is McIo's largest read
ECHO_S = 1.5               # how long a Damage op sent may come back as a hit


def _same(a, b) -> bool:
    return a is not None and b is not None and str(a).lower() == str(b).lower()


class Intake:
    def __init__(self, stale_s: float):
        self.stale_s = stale_s
        self.last_n = 0                     # the number of the last event taken (saved)
        self.skip_upto: int | None = None   # on start-up: drop up to this number, once
        self._echoes: list[dict] = []

    # ---- start-up ----------------------------------------------------------------------

    def before_open(self, io, saved_n: int):
        """Give the Io the last number taken, so it hands nothing up to it again (McIo's last_n)."""
        self.last_n = int(saved_n or 0)
        have = getattr(io, "last_n", None)       # CONTRACT: McIo(last_n=...) / McIo.last_n
        if isinstance(have, int) and have < self.last_n:
            io.last_n = self.last_n

    def after_open(self, io):
        have = getattr(io, "last_n", None)
        if isinstance(have, int) and have < self.last_n:
            log.warning("the datapack's event counter (%d) is behind the last event the engine took "
                        "(%d): a new world; its events are taken from there", have, self.last_n)
            self.last_n = have
            self.skip_upto = None
        else:
            self.skip_upto = self.last_n or None

    # ---- each read ---------------------------------------------------------------------

    def screen(self, raw: list, game_tick: int | None) -> list:
        """The events to act on, in order: a page handed again on start-up and stale input left out."""
        if not raw:
            return raw
        self.last_n = raw[-1].n
        out = raw
        if self.skip_upto is not None:
            upto, self.skip_upto = self.skip_upto, None
            if upto - raw[0].n > REPLAY_WINDOW:
                log.warning("the first event is number %d, far below the %d last taken: a new counter "
                            "(a new world); nothing is dropped", raw[0].n, upto)
            else:
                out = [r for r in raw if r.n > upto]
                if len(out) < len(raw):
                    log.info("%d event(s) handed over again after a restart, already taken; dropped",
                             len(raw) - len(out))
        if game_tick is not None and self.stale_s >= 0:
            oldest = game_tick - self.stale_s * GAME_TICKS_PER_S
            fresh = [r for r in out if r.kind not in STALE_KINDS or r.game_tick >= oldest]
            if len(fresh) < len(out):
                kinds = sorted({r.kind for r in out if r not in fresh})
                log.info("%d stale event(s) (%s, over %g s old) not acted on", len(out) - len(fresh),
                         ", ".join(kinds), self.stale_s)
            out = fresh
        return out

    # ---- the engine's own damage ---------------------------------------------------------

    def sent(self, op, now: float):
        """A Damage op went out: remember who dealt it to whom."""
        if isinstance(op, Damage) and op.by:
            self._echoes.append({"by": op.by, "target": op.target, "until": now + ECHO_S,
                                 "hit": False, "hurt": False, "kill": False})

    def mark(self, ev, now: float):
        """The event as content should get it: None for the engine's own hit; a hurt or kill the
        engine's damage caused, flagged `engine=True`; anything else as it is."""
        self._echoes = [e for e in self._echoes if e["until"] >= now]
        kind = ev.kind
        if kind not in ("hit", "hurt", "kill"):
            return ev
        for e in self._echoes:
            if e[kind]:
                continue
            if kind == "hurt":
                match = _same(ev.who, e["target"]) and (ev.attacker is None or _same(ev.attacker, e["by"]))
            elif not ev.target:          # a mob's kill, and its killing blow's hit, name no target
                match = _same(ev.who, e["by"])
            else:
                match = _same(ev.who, e["by"]) and _same(ev.target, e["target"])
            if match:
                e[kind] = True
                if kind == "hit":
                    log.debug("hit by %s on %s is the engine's own damage; not handed over", ev.who, ev.target)
                    return None
                return dataclasses.replace(ev, engine=True)
        return ev
