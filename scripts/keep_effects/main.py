"""Keep Effects: keep one effect at a level on the chosen players, given again within a second after a death or milk; a stop takes it off them."""
ASKS = ["give me speed five", "give me speed five again", "reduce my speed to two instead of three",
        "give us night vision", "make us see everyone through walls", "remove the glow on everyone"]
from kit import values, result, loop
import mc

v = values()
given = {}                      # player -> (effect, level) given last
mc.on_stop(lambda: [mc.rcon("effect clear %s %s" % (p, e)) for p, (e, _) in list(given.items())])
for i in loop(every=1):
    effect, level, names = v["effect"], v["level"], loop.who(v["player"])
    for p, (e, lv) in list(given.items()):
        if p not in names or e != effect or lv > level:     # a weaker one does not replace a stronger: clear first
            mc.rcon("effect clear %s %s" % (p, e))
            del given[p]
    for p in names:
        # 30 s, given again every second: night vision never reaches its last 10 s, where it flickers.
        mc.rcon("effect give %s %s 30 %d %s" % (p, effect, level - 1, "false" if v["particles"] else "true"))
        given[p] = (effect, level)
    if i == 0:
        result("%s %d kept on %s until stopped" % (effect, level, ", ".join(names) or "%s, not online yet" % v["player"]),
               player=v["player"], given=names, effect=effect, level=level)
