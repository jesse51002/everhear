"""sign / chest on a real server: a chest holding vanilla items and an EverHear item (its
custom_data the engine's), and a standing sign with its lines."""

from vfg_engine.livetest import verdict


def test(t):
    t.start("kit_chest", wait=5, at=t.at(0, 0, 6))
    t.check("it placed them", t.said("placed"))
    t.check("a chest", t.block(0, 0, 6, "chest"))
    t.check("a sign beside it", t.block(1, 0, 6, "oak_sign"))
    x, y, z = (int(v // 1) for v in t.xyz(0, 0, 6))
    items = t.cmd("data get block %d %d %d Items" % (x, y, z))
    t.check("4 diamonds in it", verdict('id: "minecraft:diamond"' in items and "count: 4" in items, items[:300]))
    t.check("64 arrows in it", verdict('id: "minecraft:arrow"' in items and "count: 64" in items, items[:300]))
    t.check("the EverHear gun in it, by its id", verdict('item: "kit_gun"' in items, items[:400]))
    text = t.cmd("data get block %d %d %d front_text.messages" % (x + 1, y, z))
    t.check("the sign says its lines", verdict("Kit Test" in text and "Sniper Nest" in text, text[:200]))
    quiet = t.no_errors()                     # it watches beings; here only blocks change
    t.check("no error in the engine's log", verdict(quiet[0] is not False, quiet[1]))
