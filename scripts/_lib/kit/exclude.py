"""What a hit never touches: one selector fragment for mobs and one for players, which every
hit puts between its position and its "run":

    execute positioned 10 64 -3 <exclude.mobs("distance=..4")> run damage @s 6 minecraft:player_attack by Steve
    execute positioned 10 64 -3 <exclude.players("Steve", "distance=..4")> run damage @s 6 minecraft:player_attack by Steve

Never touched: anything with an Owner (tamed wolves, cats, parrots, horses), villagers,
traders, golems, allays, armor stands, mannequins, items and experience orbs, anything else
not alive (frames, displays, markers, projectiles, boats, minecarts: no Health), what this script made
itself (tag TAG), the shooter, every spared name, and players in creative or spectator.
hostile=True keeps to monsters (26.3's MONSTER mobs: zombies, skeletons, creepers, spiders,
endermen, piglins, slimes, the dragon and the wither...) and leaves every animal, fish, bat
and golem alone. On 26.3 a selector takes one type=X alone or any number of type=!X, never
both, and potion, boat and chest_boat are not entity types.
"""
import mc

# Living types a hit spares. The not-living are left out by "if data entity @s Health",
# except items and experience orbs, which have a Health of their own.
SPARED_TYPES = ["villager", "wandering_trader", "iron_golem", "snow_golem", "copper_golem",
                "allay", "armor_stand", "mannequin", "item", "experience_orb"]
# Every living type 26.3 does not count as a monster (its animals, fish, bats and the like), read
# from the 26.3 server jar's mob categories: hostile=True leaves these out too.
PEACEFUL_TYPES = ["armadillo", "axolotl", "bat", "bee", "camel", "cat", "chicken", "cod", "cow", "dolphin",
                  "donkey", "fox", "frog", "glow_squid", "goat", "happy_ghast", "horse", "llama", "mooshroom",
                  "mule", "nautilus", "ocelot", "panda", "parrot", "pig", "polar_bear", "pufferfish", "rabbit",
                  "salmon", "sheep", "skeleton_horse", "sniffer", "squid", "strider", "tadpole", "trader_llama",
                  "tropical_fish", "turtle", "wolf"]
# The monsters, for reference (what hostile=True keeps); not used in a selector.
HOSTILE_TYPES = ["blaze", "bogged", "breeze", "camel_husk", "cave_spider", "creaking", "creeper", "drowned",
                 "elder_guardian", "ender_dragon", "enderman", "endermite", "evoker", "ghast", "giant", "guardian",
                 "hoglin", "husk", "illusioner", "magma_cube", "parched", "phantom", "piglin", "piglin_brute",
                 "pillager", "ravager", "shulker", "silverfish", "skeleton", "slime", "spider", "stray",
                 "sulfur_cube", "vex", "vindicator", "warden", "witch", "wither", "wither_skeleton", "zoglin",
                 "zombie", "zombie_horse", "zombie_nautilus", "zombie_villager", "zombified_piglin"]


def _names(spare):
    if isinstance(spare, str):
        spare = spare.split(",")
    return [mc.bare(s) for s in (spare or ()) if str(s).strip() and not str(s).startswith("@")]


def _opts(parts, where):
    return ",".join([p for p in parts if p] + ([where] if where else []))


def mobs(where="", spare=(), hostile=False):
    """'as @e[...] if data entity @s Health unless data entity @s Owner': every living mob a
    hit may touch. where: more selector options ("distance=..4", or "type=zombie" for one
    kind, which 26.3 takes only alone, so the kit's type=! go); spare: names to leave alone;
    hostile: monsters only, never an animal (PEACEFUL_TYPES go too)."""
    one_type = any(o.strip().startswith("type=") and not o.strip().startswith("type=!") for o in where.split(","))
    kinds = SPARED_TYPES + ([t for t in PEACEFUL_TYPES if t not in SPARED_TYPES] if hostile else [])
    parts = [] if one_type else ["type=!player"] + ["type=!" + t for t in kinds]
    parts.append("tag=!" + mc.TAG)
    parts += ["name=!" + n for n in _names(spare)]
    return "as @e[%s] if data entity @s Health unless data entity @s Owner" % _opts(parts, where)


def players(shooter="", where="", spare=()):
    """'as @a[...]': every player a hit may touch: not the shooter, not a spared name, not in
    creative or spectator."""
    names = _names([shooter] if shooter else []) + _names(spare)
    parts = ["name=!" + n for n in names] + ["gamemode=!creative", "gamemode=!spectator"]
    return "as @a[%s]" % _opts(parts, where)


def targets(shooter="", where="", spare=(), with_players=True, hostile=False, with_mobs=True):
    """Both fragments, mobs first, for a hit that touches players too: run the command once
    with each. hostile: monsters only among the mobs; with_mobs=False: players only."""
    out = [mobs(where, spare, hostile)] if with_mobs else []
    if with_players:
        out.append(players(shooter, where, spare))
    return out
