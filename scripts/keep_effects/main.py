"""Keep Effects: keep one effect at a level on the chosen players, given again within a second after a death or milk; a stop takes it off them."""
ASKS = ["give me speed five", "give me speed five again", "reduce my speed to two instead of three",
        "give us night vision", "make us see everyone through walls", "remove the glow on everyone"]
from kit import values, loop
import mc

VALUES = {
    "player":    {"type": "player", "default": "@a", "live": True},
    "effect":    {"type": "enum", "default": "speed", "live": True,
                  "of": ["speed", "slowness", "haste", "mining_fatigue", "strength", "jump_boost", "nausea",
                         "regeneration", "resistance", "fire_resistance", "water_breathing", "invisibility",
                         "blindness", "night_vision", "hunger", "weakness", "poison", "wither", "health_boost",
                         "absorption", "saturation", "glowing", "levitation", "luck", "unluck", "slow_falling",
                         "conduit_power", "dolphins_grace", "bad_omen", "hero_of_the_village", "darkness",
                         "trial_omen", "raid_omen", "wind_charged", "weaving", "oozing", "infested",
                         "breath_of_the_nautilus"]},
    "level":     {"type": "int", "min": 1, "max": 128, "default": 5, "live": True},
    "particles": {"type": "bool", "default": False, "live": True},
}

v = values(VALUES)
given = {}                      # player -> (effect, level) given last
mc.on_stop(lambda: [mc.rcon("effect clear %s %s" % (p, e)) for p, (e, _) in list(given.items())])
for _ in loop(every=1):
    effect, level, names = v["effect"], v["level"], loop.who(v["player"])
    for p, (e, lv) in list(given.items()):
        if p not in names or e != effect or lv > level:     # a weaker one does not replace a stronger: clear first
            mc.rcon("effect clear %s %s" % (p, e))
            del given[p]
    for p in names:
        # 30 s, given again every second: night vision never reaches its last 10 s, where it flickers.
        mc.rcon("effect give %s %s 30 %d %s" % (p, effect, level - 1, "false" if v["particles"] else "true"))
        given[p] = (effect, level)
