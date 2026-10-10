"""Horde King: a giant zombie boss in netherite near a player, with a red bar, minions every 10 s, rage as it weakens, and loot where it falls."""
ASKS = ["make sure to add a boss fight at the end", "spawn a boss near the players",
        "a custom giant named boss with lots of health"]
from kit import values, result, fx, loop, mobs, world
import time
import mc


# --- preset: boss (copied from _presets/boss.py) ---
def boss(where, v, *, abilities=None, phases=None):
    """Summon the boss near a player (a player value: it waits until one of them is online)
    or at a place (a Pos), and run the fight until every body is dead: then its minions die,
    its loot drops where it fell, and it returns True. False if there was no room for it, or
    the script stopped first.

    abilities: functions f(b, v), one called in turn every boss_every s (default: minions,
    boss_minions of boss_minion; abilities of its own replace them, so those two values then
    do nothing). phases: functions f(b, v), the first called when its health falls below the
    first share in boss_phases, the next below the next (the last repeats; default: it
    enrages). b is the kit's mobs.Boss: b.tag selects the bodies, b.dim is the dimension it
    is in, b.phase counts the phases from 0."""
    b = mobs.Boss(v["boss_name"], v["boss_mob"], v["boss_health"], v["boss_bodies"], v["boss_scale"],
                  _boss_gear(v["boss_gear"]), v["boss_bar"])
    near = None
    if isinstance(where, str):
        for _ in loop(every=1):
            near = (loop.who(where) or [None])[0]
            if near:
                break
        if not near:
            return False
    mc.rcon("kill @e[tag=%s]" % b.tag)                 # what a run killed outright left behind
    mc.rcon("bossbar remove %s" % b.bar)
    if not v["boss_griefing"]:
        world.gamerule("mob_griefing", False)          # put back when it dies, or the script stops
    if not b.spawn(near=near, at=None if near else where):
        mc.log("no room for", v["boss_name"])
        b.remove()
        return False
    b.phase, body = 0, "@e[tag=%s,limit=1]" % b.tag
    mc.rcon("execute as @e[tag=%s] run attribute @s attack_damage base set %g" % (b.tag, v["boss_attack"]))
    fx.title("@a", v["boss_name"], sub="has risen", color="dark_red")
    fx.sound("entity.wither.spawn", body, volume=4, pitch=0.8)
    mc.log("boss", v["boss_name"], "is up")

    abilities = [_boss_minions] if abilities is None else list(abilities)
    phases = [_boss_enrage] if phases is None else list(phases)
    shares = sorted((s for s in v["boss_phases"] if 0 < s < 1), reverse=True)
    full = float(v["boss_health"] * v["boss_bodies"])
    music = "minecraft:music_disc." + v["boss_music"]
    track = _BOSS_TRACKS.get(v["boss_music"])
    if track:
        mc.on_stop(lambda: mc.rcon("stopsound @a record " + music))
    job, turn, due, replay, last = None, 0, time.time() + v["boss_every"], 0.0, None
    while not mc.stopping():
        if not b.update():                             # the bar follows the bodies; 0 when all are dead
            break
        at = mc.pos(body)
        last = world.Pos(at[0], at[1], at[2], b.dim) if at else last
        health = b.health() if shares else None
        if health and health[1] and health[0] < full * shares[0]:
            shares.pop(0)
            b.phase += 1
            if phases:
                loop.background(phases[min(b.phase, len(phases)) - 1], b, v)
        if abilities and time.time() >= due and not (job and job.is_alive()):
            job = loop.background(abilities[turn % len(abilities)], b, v)
            turn, due = turn + 1, time.time() + v["boss_every"]
        if track and time.time() >= replay:
            mc.rcon("execute as @a at @s run playsound %s record @s ~ ~ ~ 1 1" % music)
            replay = time.time() + track
        loop.wait(1)
    if track:
        mc.rcon("stopsound @a record " + music)
    if mc.stopping():
        return False
    if job:
        job.join(10)                                   # an ability still summoning
    mc.rcon("kill @e[tag=%s_minion]" % b.tag)
    if last and v["boss_reward_count"]:
        mc.rcon(world.run(last.dim, 'summon item %.2f %.2f %.2f {Item:{id:"%s",count:%d}}' % (
            last[0], last[1] + 0.5, last[2], v["boss_reward"].split("[")[0], v["boss_reward_count"])))
    fx.title("@a", "VICTORY", sub="%s has fallen" % v["boss_name"], color="gold")
    fx.sound("ui.toast.challenge_complete", None, to="@a")
    if not v["boss_griefing"]:
        world.restore("mob_griefing")
    mc.log("boss", v["boss_name"], "is dead")
    return True


# Each record's length in seconds, so the boss music comes round again.
_BOSS_TRACKS = {"pigstep": 148, "otherside": 195, "relic": 218, "creator": 176, "precipice": 299}


def _boss_gear(gear):
    """The NBT for a full set of armour and a sword, none of it dropped; "" for none."""
    if gear == "none":
        return ""
    worn = ['%s:{id:"%s_%s",count:1}' % (slot, gear, piece) for slot, piece in
            (("head", "helmet"), ("chest", "chestplate"), ("legs", "leggings"), ("feet", "boots"))]
    worn.append('mainhand:{id:"%s_sword",count:1}' % ("wooden" if gear == "leather" else gear))
    return "equipment:{%s},drop_chances:{head:0f,chest:0f,legs:0f,feet:0f,mainhand:0f}" % ",".join(worn)


def _boss_minions(b, v):
    """The default ability: boss_minions more boss_minion round one of its bodies, never more
    than three times that many alive."""
    tag = b.tag + "_minion"
    n = min(v["boss_minions"], 3 * v["boss_minions"] - mobs.count(tag))
    if n > 0 and mobs.around("@e[tag=%s,limit=1,sort=random]" % b.tag, v["boss_minion"], n, spread=6, tags=(tag,)):
        fx.sound("entity.evoker.prepare_summon", "@e[tag=%s,limit=1]" % b.tag, volume=3)


def _boss_enrage(b, v):
    """The default phase: faster and stronger with each phase, and a roar."""
    mc.rcon("effect give @e[tag=%s] speed infinite %d true" % (b.tag, b.phase - 1))
    mc.rcon("effect give @e[tag=%s] strength infinite %d true" % (b.tag, b.phase - 1))
    fx.sound("entity.ravager.roar", "@e[tag=%s,limit=1]" % b.tag, volume=4, pitch=0.7)
    fx.actionbar("@a", "%s is enraged" % v["boss_name"], "red")


def spot(where):
    """Where the boss rises, in words: near the first of a player value online, or at a place."""
    if isinstance(where, str):
        return "near %s" % (loop.who(where) or [where])[0]
    return "at %d %d %d%s" % (where[0], where[1], where[2], " in " + where.dim.split(":")[-1] if where.dim else "")


v = values()
where = world.place(v["at"]) if v["at"] else v["player"]    # a player value: near the first of them online
if where is None:
    result("no %s: %s is not a place now" % (v["boss_name"], v["at"]), boss=v["boss_name"], at=v["at"])
else:
    said = spot(where)
    facts = {"boss": v["boss_name"], "where": said, "health": v["boss_health"] * v["boss_bodies"]}
    result("%s rises %s, with %d health" % (v["boss_name"], said, facts["health"]), **facts)
    if boss(where, v):                                        # ends when it is dead
        result("%s, who rose %s, is dead" % (v["boss_name"], said), dead=True, **facts)
    elif not mc.stopping():
        result("no room for %s %s" % (v["boss_name"], said), **facts)
