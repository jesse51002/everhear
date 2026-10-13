"""Chosen One: a private, roughly 18 s cinematic for one player alone. They are slowed and the
dark falls on them; a giant, glowing Notch-skinned mannequin appears above and in front, facing
them, a title reads THE CHOSEN ONE, it whispers five lines to them by name, then they are given an
enchanted, unbreakable netherite sword, a flash, and the mannequin is gone. A one-shot: it ends
once the sword is given."""

import json
import math

from vfg_engine import Args, Field, Players, Scenario, ScenarioConfig, State, players
from vfg_engine.kit import ahead, speak, yaw_to


class ChosenOne(Scenario):
    class Config(ScenarioConfig):
        summary = "An 18 s private cinematic: a giant radiant god names a player the chosen one and gives them a holy sword."
        duration_s = 25
        scale: float = Field(5, ge=1, le=16)        # how big the god is
        ahead: float = Field(9, ge=1, le=32)        # how far in front of the player it appears
        up: float = Field(4, ge=0, le=16)           # how far above their feet
        appear_s: float = Field(3, ge=0.5, le=30)   # the wait before it appears
        line_gap_s: float = Field(2.5, ge=0.5, le=10)   # between each whispered line
        lines: list[str] = [
            "{player}... do not be afraid.",
            "I am the one who waits at the end of all things.",
            "Of all who walk this world, only YOU were chosen.",
            "The others must not know. Not yet.",
            "Take this blade. The dead will fall before it.",
        ]
        farewell: str = "We will meet again... at the end."
        slowness_s: float = Field(14, ge=1, le=60)
        darkness_s: float = Field(3, ge=0, le=60)

    class Args(Args):
        player: Players = Field("@r", description="who is chosen: the first online of the names given, or @r "
                                                  "for one at random; nobody else sees or hears it")
        blade: str = Field("Holy Zombie Slayer", max_length=50, description="the sword's name, a netherite sword "
                                                                           "with smite 10 that never breaks")

    class State(State):
        player: str = ""
        uuid: str = ""

    def start(self, ctx):
        names = players(ctx, self.args.player)
        if not names:
            self.result("nobody was chosen: %s is not online" % self.args.player, player=None)
            self.end()
            return
        p = names[0]
        self.state.player = p
        pl = ctx.world.player(p)
        self.potion("slowness", self.config.slowness_s, 4, who=p, particles=False)
        if self.config.darkness_s:
            self.potion("darkness", self.config.darkness_s, 0, who=p, particles=False)
        self.sound("minecraft:block.beacon.activate", pl.pos, volume=1.0, pitch=0.5, to=[p], dim=pl.dim)
        self.after(self.config.appear_s, "appear")

    def appear(self, ctx):
        p = self.state.player
        pl = ctx.world.player(p)
        if pl is None:
            self.result("nobody was chosen: %s left" % p, player=None)
            self.end()
            return
        at = ahead(ctx, p, self.config.ahead, up=self.config.up)
        yaw, _ = yaw_to(at, pl.pos)
        label = json.dumps({"text": "The Radiant One", "color": "gold", "bold": True})
        # NEEDS: a mannequin's profile, a coloured bold CustomName, its size and turning it to face the
        # player: this is a one-off cinematic figure (no reused Actor class), so it is a raw vanilla
        # spawn with everything written into its nbt by hand.
        nbt = ('{profile:{name:"Notch"},Invulnerable:1b,NoGravity:1b,immovable:1b,Glowing:1b,'
              'attributes:[{id:"minecraft:scale",base:%g}],CustomName:%s,CustomNameVisible:1b,'
              'Rotation:[%.1ff,0f]}' % (self.config.scale, label, yaw))
        self.state.uuid = self.spawn("minecraft:mannequin", at, dim=pl.dim, nbt=nbt)
        self.particle("minecraft:end_rod", (at[0], at[1] + self.config.up, at[2]), count=15, delta=(3.0, 5.0, 3.0),
                     speed=0.05, force=True, to=[p], dim=pl.dim)
        self.sound("minecraft:ui.toast.challenge_complete", pl.pos, volume=1.0, pitch=0.6, to=[p], dim=pl.dim)
        self.message("THE CHOSEN ONE", to=[p], where="title")
        self.message("a voice only you can hear...", to=[p], where="subtitle")
        lines = self.config.lines
        for i in range(len(lines)):
            self.after(self.config.line_gap_s * (i + 1), "whisper", i)
        self.after(self.config.line_gap_s * (len(lines) + 1), "farewell")

    def whisper(self, ctx, i):
        p = self.state.player
        if ctx.world.player(p) is None:
            return
        lines = self.config.lines
        text = lines[i].replace("{player}", p)
        speak(self, ctx, text, voice="<The Radiant One>", color="gold", to=[p])
        if i == len(lines) - 1:
            self.give_blade(ctx, p)

    def give_blade(self, ctx, p):
        blade = json.dumps(self.args.blade)
        # NEEDS: a give verb for vanilla items with components (enchantments, unbreakable, lore);
        # self.command is the escape hatch.
        self.command('give %s netherite_sword[item_name=%s,enchantments={smite:10,fire_aspect:2,looting:3,'
                    'unbreaking:3,mending:1},unbreakable={},lore=["Gifted by The Radiant One","to the '
                    'Chosen"]] 1' % (p, blade))
        pl = ctx.world.player(p)
        if pl is not None:
            self.sound("minecraft:entity.player.levelup", pl.pos, volume=1.0, pitch=0.6, to=[p], dim=pl.dim)

    def farewell(self, ctx):
        p = self.state.player
        if ctx.world.player(p) is not None:
            speak(self, ctx, self.config.farewell, voice="<The Radiant One>", color="yellow", to=[p])
        uuid = self.state.uuid
        pl = ctx.world.player(p)
        if uuid and uuid in self.spawned:
            e = ctx.world.get(uuid)
            if e is not None:
                self.particle("minecraft:flash{color:[1.0,1.0,1.0,1.0]}", e.pos, force=True, to=[p], dim=e.dim)
            self.remove(uuid)
        at = ("%d %d %d" % tuple(math.floor(c) for c in pl.pos)) if pl is not None else None
        self.result("%s is the chosen one and has the %s" % (p, self.args.blade), player=p, blade=self.args.blade,
                   at=at)
        self.end()
