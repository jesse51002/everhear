"""Lucky Blocks: sponges named Lucky Block that give the player who breaks one a random reward or
trap: diamonds, apples, a totem, netherite, XP bottles, a cake, a pet wolf, a saddled horse, speed
and haste, a cursed sword, zombies, a lightning strike, a TNT surprise, levitation, a cobweb trap,
a rain of chickens, or more Lucky Blocks. A chat line and totem particles each time. As before, any
sponge broken counts, not only one named Lucky Block (the old lucky_blocks script watched the same
`minecraft.mined:minecraft.sponge` stat). (The old lucky_blocks script and action.)"""

import random

from vfg_engine import Args, Field, Mine, Players, Scenario, ScenarioConfig, UNTIL_STOPPED, players
from vfg_engine.kit import lightning, speak

# NEEDS: a self.* verb to give a player a vanilla item stack (with components, for the sword): every
# "give" line below is the self.command escape hatch, there being no such verb yet.
OUTCOMES = [
    ("8 diamonds", "diamonds"),
    ("4 golden apples", "golden_apples"),
    ("an enchanted golden apple", "enchanted_apple"),
    ("a totem", "totem"),
    ("2 netherite ingots", "netherite"),
    ("a stack of XP bottles", "xp_bottles"),
    ("a cake", "cake"),
    ("a pet wolf", "wolf"),
    ("a horse with a saddle", "horse"),
    ("speed and haste for a minute", "buffs"),
    ("a lucky sword", "sword"),
    ("zombies!", "zombies"),
    ("a lightning strike", "lightning"),
    ("a TNT surprise", "tnt"),
    ("a ride into the sky", "levitate"),
    ("a cobweb trap", "cobweb"),
    ("a rain of chickens", "chickens"),
    ("more lucky blocks", "more_blocks"),
]


class LuckyBlocks(Scenario):
    class Config(ScenarioConfig):
        summary = ("Sponges named Lucky Block: breaking one rolls a random reward or trap, with a chat line "
                   "and totem particles. Any sponge counts, not only ones named Lucky Block.")
        duration_s = UNTIL_STOPPED

    class Args(Args):
        players: Players = Field("@a", description="who gets them, and whose breaking counts: @a, @r, or "
                                                    "names")
        give: int = Field(3, ge=0, le=64, description="how many Lucky Blocks (sponges named Lucky Block) "
                                                      "each of them gets at the start (0: none)")

    def start(self, ctx):
        a = self.args
        given = []
        if a.give:
            for name in players(ctx, a.players):
                self.command('give %s sponge[item_name="Lucky Block"] %d' % (name, a.give))
                given.append(name)
        self.listen(Mine, "on_mine", blocks=["minecraft:sponge"])
        extra = "; gave %d each to %s" % (a.give, ", ".join(given)) if given else ""
        self.result("lucky blocks on for %s%s" % (a.players, extra), players=a.players, given=given, each=a.give)

    def on_mine(self, ctx, ev):
        if ev.who not in players(ctx, self.args.players):
            return
        p = ctx.world.player(ev.who)
        if p is None:
            return
        for _ in range(ev.count):
            self.roll(ctx, ev.who, p.pos, p.dim)

    def roll(self, ctx, p, pos, dim):
        name, key = random.choice(OUTCOMES)
        getattr(self, "outcome_" + key)(ctx, p, pos, dim)
        speak(self, ctx, "You got %s!" % name, voice="Lucky Block", color="gold", to=[p])
        self.particle("minecraft:totem_of_undying", (pos[0], pos[1] + 1, pos[2]), count=40,
                      delta=(0.5, 0.8, 0.5), speed=0.3, dim=dim)

    # ---- the outcomes, each at the player's spot -------------------------------------------

    def outcome_diamonds(self, ctx, p, pos, dim):
        self.command("give %s diamond 8" % p)

    def outcome_golden_apples(self, ctx, p, pos, dim):
        self.command("give %s golden_apple 4" % p)

    def outcome_enchanted_apple(self, ctx, p, pos, dim):
        self.command("give %s enchanted_golden_apple 1" % p)

    def outcome_totem(self, ctx, p, pos, dim):
        self.command("give %s totem_of_undying 1" % p)

    def outcome_netherite(self, ctx, p, pos, dim):
        self.command("give %s netherite_ingot 2" % p)

    def outcome_xp_bottles(self, ctx, p, pos, dim):
        self.command("give %s experience_bottle 64" % p)

    def outcome_cake(self, ctx, p, pos, dim):
        x, y, z = (round(c) for c in pos)
        self.blocks.setblock((x + 1, y, z), "minecraft:cake", dim=dim)

    def outcome_wolf(self, ctx, p, pos, dim):
        # NEEDS: a player's uuid (Owner wants one; a name here is probably left untamed on 26.3, as before)
        self.spawn("minecraft:wolf", (pos[0] + 1, pos[1], pos[2]), dim=dim, nbt='{Tame:1b,Owner:"%s"}' % p)

    def outcome_horse(self, ctx, p, pos, dim):
        self.spawn("minecraft:horse", (pos[0] + 1, pos[1], pos[2]), dim=dim,
                  nbt='{Tame:1b,SaddleItem:{id:"minecraft:saddle",Count:1b}}')

    def outcome_buffs(self, ctx, p, pos, dim):
        self.potion("speed", 60, 2, who=p)
        self.potion("haste", 60, 2, who=p)

    def outcome_sword(self, ctx, p, pos, dim):
        self.command('give %s diamond_sword[item_name="Lucky Sword",'
                     'enchantments={sharpness:6,looting:4}] 1' % p)

    def outcome_zombies(self, ctx, p, pos, dim):
        for dx, dz in ((3, 0), (-3, 0), (0, 3)):
            self.spawn("minecraft:zombie", (pos[0] + dx, pos[1], pos[2] + dz), dim=dim)

    def outcome_lightning(self, ctx, p, pos, dim):
        lightning(self, ctx, (pos[0] + 2, pos[1], pos[2]), count=1, spread=0, keep_away=0, dim=dim)

    def outcome_tnt(self, ctx, p, pos, dim):
        self.spawn("minecraft:tnt", (pos[0], pos[1] + 3, pos[2]), dim=dim, nbt="{Fuse:60s}")

    def outcome_levitate(self, ctx, p, pos, dim):
        self.potion("levitation", 4, 1, who=p)

    def outcome_cobweb(self, ctx, p, pos, dim):
        x, y, z = (round(c) for c in pos)
        self.blocks.fill((x - 1, y, z - 1), (x + 1, y + 1, z + 1), "minecraft:cobweb", replace="minecraft:air",
                        dim=dim)

    def outcome_chickens(self, ctx, p, pos, dim):
        for _ in range(8):
            self.spawn("minecraft:chicken", (pos[0], pos[1] + 6, pos[2]), dim=dim)

    def outcome_more_blocks(self, ctx, p, pos, dim):
        self.command('give %s sponge[item_name="Lucky Block"] 3' % p)
