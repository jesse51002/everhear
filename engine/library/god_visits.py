"""God Visits: every few minutes of play, one random player sees a giant glowing god appear in
front of them, face them, whisper to them alone, and give a gift or take something, then vanish.
Until stopped."""

import json
import random

from vfg_engine import Args, Field, ItemId, Players, Scenario, ScenarioConfig, State, UNTIL_STOPPED
from vfg_engine.kit import ahead, director, yaw_to

# (name, skin profile, the colour of its name), one chosen at random each visit.
GODS = [("The Radiant One", "Notch", "gold"), ("The Storm God", "jeb_", "aqua"),
       ("Thanatos, God of Death", "Grim", "dark_gray")]


class GodVisits(Scenario):
    every = 1

    class Config(ScenarioConfig):
        summary = ("Every few minutes of play, a giant glowing god appears to one random player, faces them, "
                   "whispers to them alone, and gives a gift or takes something, then vanishes.")
        duration_s = UNTIL_STOPPED
        ahead: float = Field(6, ge=0, le=32)       # how far in front of the player it appears
        up: float = Field(1, ge=0, le=8)           # how far above their feet
        sound: str = "minecraft:block.beacon.activate"

    class Args(Args):
        players: Players = Field("@a", description="who it may visit: @a, @r, or names separated by commas")
        min_s: float = Field(180, ge=10, le=3600, description="the shortest wait between visits, in seconds "
                                                              "someone is online")
        max_s: float = Field(360, ge=10, le=3600, description="the longest wait between visits, in seconds "
                                                              "(under min_s: min_s)")
        each: bool = Field(False, description="each player gets their own clock; off: one clock, a visit goes "
                                             "to one player at random")
        gift_weight: float = Field(6, ge=0, le=100, description="how often a visit gives a gift, against "
                                                                "take_weight")
        take_weight: float = Field(4, ge=0, le=100, description="how often a visit takes something, against "
                                                                "gift_weight")
        gifts: list[str] = Field(
            ["diamond 4", "golden_apple 3", "enchanted_golden_apple 1", "netherite_ingot 1", "totem_of_undying 1",
             "ender_pearl 8", "experience_bottle 32", "cooked_beef 32"],
            max_length=20, description='what a god may give, one at random: a vanilla item and how many '
                                       '("diamond 4")')
        take_items: list[ItemId] = Field(
            ["diamond", "golden_apple", "ender_pearl", "cooked_beef", "iron_ingot", "arrow", "torch", "bread"],
            max_length=20, description="what a god may take, one at random, up to take_most of it")
        take_most: int = Field(8, ge=1, le=64, description="the most of an item a god takes at once")
        stay_s: float = Field(8, ge=2, le=60, description="how long the god stays in front of the player, in "
                                                          "seconds")

    class State(State):
        visits: int = 0

    def tick(self, ctx):
        for event, p in director(self, ctx, ["gift", "take"], [self.args.gift_weight, self.args.take_weight],
                                self.args.min_s, self.args.max_s, self.args.each, self.args.players):
            self.visit(ctx, p, event == "gift")

    def visit(self, ctx, p, give):
        pl = ctx.world.player(p)
        if pl is None:
            return
        name, skin, color = random.choice(GODS)
        at = ahead(ctx, p, self.config.ahead, up=self.config.up)
        if at is None:
            return
        yaw, _ = yaw_to(at, pl.pos)
        label = json.dumps({"text": name, "color": color, "bold": True})
        # NEEDS: a mannequin's profile and a coloured CustomName vary with which god is chosen, and
        # turning it to face the player; written here as extra nbt, merged onto god's Config.
        nbt = '{profile:{name:"%s"},CustomName:%s,Rotation:[%.1ff,0f]}' % (skin, label, yaw)
        uuid = self.spawn("god", at, dim=pl.dim, nbt=nbt)
        self.sound(self.config.sound, at, volume=1.0, pitch=0.6, to=[p], dim=pl.dim)
        a = self.args
        if give and a.gifts:
            what = random.choice(a.gifts).split()
            item, n = what[0], (what[1] if len(what) > 1 else "1")
            # NEEDS: a give verb for vanilla items (self.* has none); self.command is the escape hatch.
            self.command("give %s %s %s" % (p, item, n))
            msg = "You amuse me, %s. Take this gift: %sx %s." % (p, n, item.replace("minecraft:", "").replace("_", " "))
        elif not give and a.take_items:
            item = random.choice(a.take_items)
            # NEEDS: a clear verb for vanilla items (self.* has none); self.command is the escape hatch.
            self.command("clear %s %s %d" % (p, item, a.take_most))
            msg = "You have grown too comfortable, %s. I take your %s." % (p, item.replace("minecraft:", "").replace(
                "_", " "))
        else:
            msg = "I am watching you, %s." % p
        self.message("§7%s whispers to you: %s" % (name, msg), to=[p])
        self.state.visits += 1
        self.after(a.stay_s, "leave", uuid)

    def leave(self, ctx, uuid):
        if uuid in self.spawned:
            self.remove(uuid)
