"""A boss fight: a big named mob (or several bodies, a three-headed dragon) with armour, a health bar, minions or its own abilities every few seconds, rage as it weakens, boss music, and loot where it dies."""
import time

import mc
from kit import fx, loop, mobs, world
from actions._types import Field, ItemId, Literal, Live, MobId, Values  # the values class: read on the PC only


class Boss(Values):
    boss_name: str = "Horde King"
    """the boss's name, over its head, on its health bar and in the titles"""
    boss_mob: MobId = "zombie"
    """the mob the boss is"""
    boss_bodies: int = Field(1, ge=1, le=8)
    """how many bodies it has, on one health bar; it dies when every one is dead (3: a three-headed dragon)"""
    boss_health: int = Field(800, ge=1, le=1024)
    """each body's health, in half-hearts (a player has 20)"""
    boss_scale: float = Field(3, ge=0.25, le=16)
    """its size: 1 is the mob's own, 3 three times as big"""
    boss_attack: float = Field(12, ge=0, le=100)
    """the damage of each of its hits, in half-hearts, before armour"""
    boss_gear: Literal["none", "leather", "golden", "iron", "diamond", "netherite"] = "netherite"
    """the armour and sword it wears, never dropped (leather comes with a wooden sword); none: nothing"""
    boss_bar: Literal["red", "blue", "green", "pink", "purple", "white", "yellow"] = "red"
    """the colour of its health bar"""
    boss_every: Live[float] = Field(10, ge=1, le=120)
    """seconds between its abilities, used in turn (by default its one ability is calling minions)"""
    boss_minion: Live[MobId] = "zombie"
    """the mob it calls as minions; does nothing when the script gives it abilities of its own"""
    boss_minions: Live[int] = Field(3, ge=0, le=20)
    """how many it calls each time, never over three times this many alive (0: none); not with abilities of its own"""
    boss_phases: list[float] = [0.5, 0.25]
    """the shares of its full health at which a phase starts (0.5: at half); by default it enrages in each, faster and stronger"""
    boss_reward: Live[ItemId] = "diamond"
    """the item it drops where it dies (its id only)"""
    boss_reward_count: Live[int] = Field(8, ge=0, le=64)
    """how many of boss_reward it drops (0: none)"""
    boss_music: Literal["none", "pigstep", "otherside", "relic", "creator", "precipice"] = "none"
    """a music disc every player hears, over and over, until it dies; none: no music"""
    boss_griefing: bool = True
    """whether mobs may break blocks in the fight; off turns mob griefing off for every mob until it dies"""


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
