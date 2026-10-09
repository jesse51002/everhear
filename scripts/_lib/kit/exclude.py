"""What a hit never touches: one selector fragment for mobs and one for players, which every
hit puts between its position and its "run":

    execute positioned 10 64 -3 <exclude.mobs("distance=..4")> run damage @s 6 minecraft:player_attack by Steve
    execute positioned 10 64 -3 <exclude.players("Steve", "distance=..4")> run damage @s 6 minecraft:player_attack by Steve

Never touched: anything with an Owner (tamed wolves, cats, parrots, horses), villagers,
traders, golems, allays, armor stands, mannequins, items and experience orbs, anything else
not alive (frames, displays, markers, projectiles, boats, minecarts: no Health), what this script made
itself (tag TAG), the shooter, every spared name, and players in creative or spectator.
On 26.3 a selector takes one type=X alone or any number of type=!X, never both, and potion,
boat and chest_boat are not entity types.
"""
import mc

# Living types a hit spares. The not-living are left out by "if data entity @s Health",
# except items and experience orbs, which have a Health of their own.
SPARED_TYPES = ["villager", "wandering_trader", "iron_golem", "snow_golem", "copper_golem",
                "allay", "armor_stand", "mannequin", "item", "experience_orb"]


def _names(spare):
    if isinstance(spare, str):
        spare = spare.split(",")
    return [mc.bare(s) for s in (spare or ()) if str(s).strip() and not str(s).startswith("@")]


def _opts(parts, where):
    return ",".join([p for p in parts if p] + ([where] if where else []))


def mobs(where="", spare=()):
    """'as @e[...] if data entity @s Health unless data entity @s Owner': every living mob a
    hit may touch. where: more selector options ("distance=..4", or "type=zombie" for one
    kind, which 26.3 takes only alone, so the kit's type=! go); spare: names to leave alone."""
    one_type = any(o.strip().startswith("type=") and not o.strip().startswith("type=!") for o in where.split(","))
    parts = [] if one_type else ["type=!player"] + ["type=!" + t for t in SPARED_TYPES]
    parts.append("tag=!" + mc.TAG)
    parts += ["name=!" + n for n in _names(spare)]
    return "as @e[%s] if data entity @s Health unless data entity @s Owner" % _opts(parts, where)


def players(shooter="", where="", spare=()):
    """'as @a[...]': every player a hit may touch: not the shooter, not a spared name, not in
    creative or spectator."""
    names = _names([shooter] if shooter else []) + _names(spare)
    parts = ["name=!" + n for n in names] + ["gamemode=!creative", "gamemode=!spectator"]
    return "as @a[%s]" % _opts(parts, where)


def targets(shooter="", where="", spare=(), with_players=True):
    """Both fragments, mobs first, for a hit that touches players too: run the command once
    with each."""
    out = [mobs(where, spare)]
    if with_players:
        out.append(players(shooter, where, spare))
    return out
