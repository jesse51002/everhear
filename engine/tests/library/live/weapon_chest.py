"""weapon_chest, live: started for Steve, a chest of six named power weapons and 64 arrows
appears 2 blocks ahead of him (contents powers, the old weapon_chest script); started again with
contents guns (the old gun_chests script, merged in), nearest and farthest both 0, a chest of two
library guns and a glowing "GUN CHEST" label appears right at him."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("weapon_chest", wait=3, players="Steve")
    t.check("it says the chest of powers is ready", t.said("a chest of powers is ready"))
    t.check("a chest sits 2 ahead of Steve", t.block(0, 0, 2, "minecraft:chest"))
    t.wait(0.5)                                   # the stacks past one command's bytes go in a tick later
    x, y, z = (int(v // 1) for v in t.xyz(0, 0, 2))
    items = t.cmd("data get block %d %d %d Items" % (x, y, z))
    held = [w for w in ("hellfire_longbow", "glacier_bow", "stormcaller", "thunderbrand", "doomcleaver",
                        "gravebreaker") if 'item: "%s"' % w in items]
    t.check("that one chest holds all six power weapons and 64 arrows",
            verdict(len(held) == 6 and "count: 64" in items, "%d of 6: %s" % (len(held), ", ".join(held))))

    t.start("weapon_chest", wait=5, players="Steve", contents="guns", chests_per_player=1, nearest=0, farthest=0)
    t.check("it says a gun chest is hidden", t.said("gun chest"))
    t.check("a gun chest sits right at Steve", t.block(0, 0, 0, "minecraft:chest"))
    t.check("its glowing label stands near it", t.near("armor_stand", 0, 0, 0, radius=2, count=1))
    t.check("no error in the engine's log", t.no_errors())
