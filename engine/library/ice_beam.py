"""Ice beam: an instant freezing beam along the caster's look, through walls, slowing, weakening
and blinding everything near it (players too), through the shared `frozen` mark; the beam itself
deals no damage. A non-player bearer also gets frozen's vanilla frost look, which (frozen.py's own
docstring) carries its own tiny vanilla frost tick every couple of seconds -- the same "cold's 1
damage every 2 s, as in powder snow" the old freeze preset documented; it is not this beam's own
damage (declared 0, never dealt through self.damage). Along the beam it carves an open lane and
turns the ground round it to ice, lays an icy path under it, and a pad under the caster, to a
chiming tune. Granted and bound, it is cast by voice; the Ice Beam Staff fires it on a right-click.
(The old ice_beam script, merged with made/main's ice_beam_cast: engine/library/README.md's merge
rule, "A merged script becomes Args or Config of the class it joins". ice_beam_cast's own carved
cube was 10 wide, swallowing its 5-wide ice replace entirely so no ice ever showed [inv_G4,
"ice_beam_cast"]; tunnel_r and ice_r below keep the library script's own promise instead, an open
lane with a visible ice shell round it. Freezing now goes through library/frozen.py, the one
shared mark (engine/library/README.md: "Name another class by its id instead: ... self.apply(uuid,
"frozen")"), instead of re-doing its potions and its TicksFrozen by hand.)"""

from typing import Literal

from vfg_engine import Ability, AbilityConfig, Field
from vfg_engine.kit import beam_line, hitscan, melody


class IceBeam(Ability):
    class Config(AbilityConfig):
        summary = ("An instant beam along your look, through walls, that freezes and blinds everything near it and "
                   "ices the ground along it.")
        length: float = Field(80, ge=4, le=300)
        # near_line's width is how close a body must come to the line; the old script's own
        # "width" field (5) was twice that ("within 2.5 of the line", ice_beam/main.py's does).
        width: float = Field(2.5, ge=0.25, le=16)
        through_walls: bool = True
        what: Literal["everything", "mobs", "hostile", "players"] = "everything"
        freeze_s: float = Field(10, ge=1, le=120)
        blind: bool = True
        particle: str = "minecraft:snowflake"
        tunnel: bool = True                     # carve an open lane along the beam
        tunnel_r: int = Field(1, ge=0, le=8)     # the open lane's half-width ("three blocks across")
        ice_r: int = Field(2, ge=1, le=8)        # how far round each point, outside the lane, turns to ice
        ice: str = "minecraft:packed_ice"
        step: int = Field(2, ge=1, le=10)        # a point every this many blocks along the beam
        path: bool = True                        # an icy path two blocks under the beam
        path_r: int = Field(1, ge=0, le=4)
        tune: str = "ice_beam"
        damage = 0
        damage_type = "minecraft:freeze"
        charge_s = 0
        cooldown_s = 0.5
        scale_per_point = 0
        duration_s = 2.5                         # long enough for the "ice_beam" tune to finish

    def on_cast(self, ctx, ev):
        c = self.config
        shot = hitscan(self, ctx, c.length, c.width, c.what, through_walls=c.through_walls)
        for t in shot.targets:
            self.apply(t.key, "frozen", c.freeze_s, blind=c.blind)
        beam_line(self, ctx, shot.start, shot.end, c.particle)
        n = int(shot.length)
        points = [[s + d * k for s, d in zip(shot.start, shot.direction)] for k in range(0, n + 1, c.step)]
        cells = [tuple(round(v) for v in p) for p in points]
        for px, py, pz in cells:
            if c.tunnel:
                r = c.tunnel_r
                self.blocks.fill((px - r, py - r, pz - r), (px + r, py + r, pz + r), "minecraft:air")
            r = c.ice_r
            self.blocks.replace(((px - r, py - r, pz - r), (px + r, py + r, pz + r)),
                                "solid water lava > %s" % c.ice)
        if c.path:
            for px, py, pz in cells:
                self.blocks.fill((px - c.path_r, py - 2, pz - c.path_r), (px + c.path_r, py - 2, pz + c.path_r), c.ice)
        fx, fy, fz = (round(v) for v in ctx.user.pos)
        self.blocks.fill((fx - 2, fy - 1, fz - 2), (fx + 2, fy - 1, fz + 2), c.ice)
        melody(self, ctx, c.tune)
