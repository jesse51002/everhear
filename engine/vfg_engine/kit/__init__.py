"""The kit (DESIGN2 §5): the helpers content shares, so each thing is done once.

    from vfg_engine.kit import hitscan, hit, beam_line

    def on_cast(self, ctx, ev):
        shot = hitscan(self, ctx, self.config.range, self.config.width, through_walls=True)
        hit(self, ctx, shot.targets, self.config.damage(ctx), "minecraft:magic")
        beam_line(self, ctx, shot.start, shot.end, "minecraft:end_rod")

The reference, each helper with what it does and an example, is engine/KIT.md.

**What a helper is.** It takes the instance (`self`) and/or `ctx` and does its work only through
the content API everyone has (`self.*`, `ctx.*`, `self.state`). It never blocks or waits: what
takes time goes on timers (the kit's own, `kit_step`) and on the engine's block queue
(`self.blocks`). It keeps nothing of its own outside `self.state` (its memory is
`self.state.kit`). Each call fits well inside a content call's 50 ms.

**What takes time or remembers** (melody, countdown, fly, held_use, sneak_gesture, steps,
chunks_entered, director, boss_bar without `most`, summon_near with a `cap`, world_mob_rule)
uses the kit's timers (every class's `kit_step`) and the kit's memory (every State's `kit`):
content declares nothing for it.

Modules: finders (targets, near_line, hitscan, hit), blasts (blast, lightning, crater), blocks
(build_shape, sign, chest), movement (push, land_safely), spawning (safe_spots, sky_spots,
summon_near, mob_nbt, boss_bar, world_mob_rule), show (beam_line, charge_frames, melody, countdown,
speak, fly), input (held_use, sneak_gesture, steps, chunks_entered, director). Reading a Players
arg is the core's `players(ctx, self.args.players)` (from vfg_engine), as are block mixes and
replace rules (the `self.blocks` queue reads them). `kit_step` is the core's hook into the kit's
timers, not for content.
"""

from ._base import ahead, direction, yaw_to
from ._base import kit_step as kit_step          # the core's Thing.kit_step lands here
from .blasts import blast, blast_damage, blast_points, crater, lightning
from .blocks import BACK, Built, build_shape, chest, compass, sign
from .input import Step, chunks_entered, director, held_use, sneak_gesture, steps
from .movement import land_safely, push
from .show import TUNES, beam_line, charge_frames, countdown, fly, melody, speak
from .spawning import boss_bar, mob_nbt, safe_spots, sky_spots, summon_near, undo_world_mob_rule, world_mob_rule
from .finders import (HOSTILE_TYPES, PEACEFUL_TYPES, PROJECTILE_TYPES, SPARED_TYPES, Shot, Target, hit, hitscan,
                      near_line, targets)

__all__ = [
    # targets
    "targets", "near_line", "hitscan", "hit", "Target", "Shot",
    "SPARED_TYPES", "PEACEFUL_TYPES", "HOSTILE_TYPES", "PROJECTILE_TYPES",
    # blasts
    "blast", "blast_points", "blast_damage", "lightning", "crater",
    # blocks
    "build_shape", "Built", "compass", "BACK", "sign", "chest",
    # movement
    "push", "land_safely",
    # spawning
    "safe_spots", "sky_spots", "summon_near", "mob_nbt", "boss_bar", "world_mob_rule", "undo_world_mob_rule",
    # show
    "beam_line", "charge_frames", "melody", "TUNES", "countdown", "speak", "fly",
    # input
    "held_use", "sneak_gesture", "steps", "Step", "chunks_entered", "director",
    # places round someone
    "ahead", "yaw_to", "direction",
]
