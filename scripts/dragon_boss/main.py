"""Three-Headed Dragon: three ender dragons rise in the sky over a player, one red bar of their summed health, boss music for everyone until they fall."""
ASKS = ["summon a three-headed dragon", "play boss music and make it sound really cool",
        "back to the music, the music stopped", "a three-headed dragon boss with music"]
from kit import values, fx, loop, mobs, world
import time
import mc

VALUES = {
    "player":            {"type": "player", "default": "@a"},
    "at":                {"type": "list", "of": "number", "default": []},
    "height":            {"type": "int", "min": 0, "max": 200, "default": 30},
    "boss_name":         {"type": "str", "default": "Three-Headed Dragon"},
    "boss_mob":          {"type": "mob", "default": "ender_dragon"},
    "boss_bodies":       {"type": "int", "min": 1, "max": 8, "default": 3},
    "boss_health":       {"type": "int", "min": 1, "max": 1024, "default": 600},
    "boss_scale":        {"type": "number", "min": 0.25, "max": 16, "default": 1},
    "boss_attack":       {"type": "number", "min": 0, "max": 100, "default": 12},
    "boss_gear":         {"type": "enum", "of": ["none", "leather", "golden", "iron", "diamond", "netherite"], "default": "none"},
    "boss_bar":          {"type": "enum", "of": ["red", "blue", "green", "pink", "purple", "white", "yellow"], "default": "red"},
    "boss_every":        {"type": "number", "min": 1, "max": 120, "default": 10, "live": True},
    "boss_minion":       {"type": "mob", "default": "zombie", "live": True},
    "boss_minions":      {"type": "int", "min": 0, "max": 20, "default": 0, "live": True},
    "boss_phases":       {"type": "list", "of": "number", "default": [0.5, 0.25]},
    "boss_reward":       {"type": "item", "default": "diamond", "live": True},
    "boss_reward_count": {"type": "int", "min": 0, "max": 64, "default": 8, "live": True},
    "boss_music":        {"type": "enum", "of": ["none", "pigstep", "otherside", "relic", "creator", "precipice"], "default": "none"},
    "boss_griefing":     {"type": "bool", "default": False},
    "theme_tune":        {"type": "enum", "of": ["galick_gun", "hollow_purple", "ice_beam", "spirit_bomb", "spirit_bomb_throw", "super_saiyan", "boss", "none"], "default": "boss", "live": True},
    "theme_to":          {"type": "enum", "of": ["near", "everyone", "player"], "default": "everyone", "live": True},
    "theme_reach":       {"type": "int", "min": 8, "max": 256, "default": 48, "live": True},
    "theme_loop":        {"type": "bool", "default": True, "live": True},
}


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


# --- preset: theme_music (copied from _presets/theme_music.py) ---
def theme_music(at, v, *, tune=None):
    """Play the tune theme_tune names (or `tune`, for this call: the throw after a charge's
    choir), in the background, at a player (or a place), and return how many seconds it
    lasts. theme_to: "near" is heard within theme_reach blocks of it, "everyone" by every
    player where they stand, "player" by that player alone. theme_loop: again each time it
    ends (boss music while the boss lives), until a newer theme, "none" or a stop. A newer
    theme at the same player (or for everyone) cuts this one off, a record too; "none" plays
    nothing and only cuts off what plays there."""
    to = v["theme_to"]
    if to == "player" and not isinstance(at, str):
        to = "near"                     # a place has nobody to play it to alone
    where = at if isinstance(at, str) else "%d_%d_%d" % tuple(at[:3])
    key = "theme" if to == "everyone" else "theme_" + where      # one theme at a time per player or place
    hear = {"everyone": "@a", "player": at}.get(to)
    _THEME_MUSIC_GEN[key] = gen = _THEME_MUSIC_GEN.get(key, 0) + 1   # a loop under this key ends
    fx.stop_melody(key)
    seconds = _theme_music_play(at, v, tune or v["theme_tune"], hear, key)
    if seconds and v["theme_loop"]:
        loop.background(_theme_music_again, at, v, tune, hear, key, gen, seconds)
    return seconds


# Each tune is a list of notes: (seconds from the start, a note block instrument or a sound
# id, a note from 0 to 24 where 12 is the sound's own pitch, volume). The run's tunes, as
# its scripts played them; a volume of 3 is heard 48 blocks away.
_THEME_MUSIC_TUNES = {
    "galick_gun": [         # a rising purple fanfare, 1.5 s
        (0, "guitar", 0, 3), (0, "block.beacon.power_select", 4, 2), (0.2, "pling", 4, 3), (0.4, "guitar", 7, 3),
        (0.6, "pling", 12, 3), (0.8, "guitar", 16, 3), (0.8, "bell", 16, 3), (1.0, "pling", 19, 3),
        (1.2, "bell", 24, 3),
        (1.5, "bell", 24, 3), (1.5, "pling", 24, 3), (1.5, "guitar", 19, 3), (1.5, "guitar", 16, 3),
        (1.5, "block.beacon.activate", 12, 3), (1.5, "block.amethyst_block.chime", 24, 3),
    ],
    "hollow_purple": [      # a slow fall from bell to bass, then a boom, 3 s
        (0, "bass", 7, 3), (0, "didgeridoo", 7, 3), (0, "block.beacon.power_select", 5, 2),
        (0.4, "bell", 19, 3), (0.4, "block.amethyst_block.chime", 3, 2),
        (0.8, "bell", 15, 3), (0.8, "bass", 3, 3),
        (1.2, "bell", 14, 3),
        (1.6, "bell", 12, 3), (1.6, "didgeridoo", 2, 3),
        (2.0, "bell", 10, 3),
        (2.4, "bell", 7, 3), (2.4, "bass", 0, 3), (2.4, "didgeridoo", 0, 3),
        (3.0, "bell", 0, 3), (3.0, "block.amethyst_block.resonate", 0, 3), (3.0, "entity.generic.explode", 0, 2),
    ],
    "ice_beam": [           # high chimes and xylophone, 2.5 s
        (0, "chime", 24, 3), (0.2, "iron_xylophone", 22, 3), (0.4, "bell", 19, 3), (0.6, "chime", 17, 3),
        (0.8, "iron_xylophone", 19, 3), (1.0, "bell", 22, 3), (1.2, "chime", 24, 3), (1.4, "iron_xylophone", 22, 3),
        (1.6, "bell", 24, 3), (1.6, "block.amethyst_block.chime", 24, 2), (1.9, "chime", 17, 3),
        (2.2, "iron_xylophone", 24, 3), (2.5, "bell", 24, 3), (2.5, "chime", 19, 3),
    ],
    "spirit_bomb": [        # a choir swelling while the bomb charges: a chord every 1.1 s, louder each time, 6 s
        (0.0, "flute", 2, 1), (0.05, "flute", 9, 1), (0.0, "chime", 6, 1), (0.1, "bell", 14, 1),
        (0.0, "block.beacon.ambient", 2, 1),
        (1.1, "flute", 4, 1.5), (1.15, "flute", 11, 1.5), (1.1, "chime", 8, 1.5), (1.2, "bell", 16, 1.5),
        (1.1, "block.beacon.ambient", 4, 1.5), (1.6, "chime", 13, 1.5),
        (2.2, "flute", 5, 2), (2.25, "flute", 12, 2), (2.2, "chime", 9, 2), (2.3, "bell", 17, 2),
        (2.2, "block.beacon.ambient", 5, 2),
        (3.3, "flute", 7, 2.5), (3.35, "flute", 14, 2.5), (3.3, "chime", 11, 2.5), (3.4, "bell", 19, 2.5),
        (3.3, "block.beacon.ambient", 7, 2.5), (3.8, "chime", 16, 2.5),
        (4.4, "flute", 9, 3), (4.45, "flute", 16, 3), (4.4, "chime", 13, 3), (4.5, "bell", 21, 3),
        (4.4, "block.beacon.ambient", 9, 3),
        (5.5, "flute", 11, 3), (5.55, "flute", 18, 3), (5.5, "chime", 15, 3), (5.6, "bell", 23, 3),
        (5.5, "block.beacon.ambient", 11, 3), (6.0, "chime", 20, 3),
    ],
    "spirit_bomb_throw": [  # the release: a bright chord climbing, 1 s
        (0, "flute", 12, 3), (0, "chime", 16, 3), (0, "bell", 19, 3), (0, "block.beacon.activate", 12, 3),
        (0.25, "flute", 16, 3), (0.25, "bell", 24, 3),
        (0.5, "flute", 19, 3), (0.5, "chime", 24, 3), (0.5, "block.amethyst_block.resonate", 24, 3),
        (0.9, "bell", 24, 3), (0.9, "chime", 19, 3),
    ],
    "super_saiyan": [       # a fast climbing riff with a bass under every third note, then a chord, 2 s
        (0, "block.beacon.activate", 18, 0.8),
        (0.0, "bit", 6, 1), (0.0, "bass", 0, 1), (0.14, "pling", 9, 1), (0.28, "guitar", 11, 1),
        (0.42, "bell", 12, 1), (0.42, "bass", 0, 1), (0.56, "bit", 14, 1), (0.7, "pling", 16, 1),
        (0.84, "guitar", 17, 1), (0.84, "bass", 5, 1), (0.98, "bell", 19, 1), (1.12, "bit", 21, 1),
        (1.26, "pling", 22, 1), (1.26, "bass", 10, 1), (1.4, "guitar", 24, 1), (1.54, "bell", 21, 1),
        (1.68, "bit", 24, 1), (1.68, "bass", 12, 1), (1.82, "pling", 24, 1),
        (1.96, "pling", 12, 1), (1.96, "bell", 12, 1), (1.96, "pling", 16, 1), (1.96, "bell", 16, 1),
        (1.96, "pling", 19, 1), (1.96, "bell", 19, 1), (1.96, "pling", 24, 1), (1.96, "bell", 24, 1),
    ],
    "boss": [               # Pigstep, with a growl, a wither and thunder over it, 148 s
        (0, "music_disc.pigstep", 12, 2),
        (20, "entity.ender_dragon.growl", 12, 0.4), (55, "entity.wither.ambient", 12, 0.4),
        (90, "entity.lightning_bolt.thunder", 12, 0.4), (125, "entity.ender_dragon.growl", 12, 0.4),
    ],
}

# A record plays on in the players' game once started (fx.melody stops it when a newer
# melody starts, and on a stop); each with how long it lasts, in seconds.
_THEME_MUSIC_RECORDS = {"music_disc.pigstep": 148.0}
_THEME_MUSIC_GEN = {}           # key -> how many themes have started under it: a loop plays on while it is the last


def _theme_music_play(at, v, name, hear, key):
    """Start one tune under a key; how long it lasts (0 for "none")."""
    notes = _THEME_MUSIC_TUNES.get(name)
    if not notes:
        return 0
    fx.melody(notes, at, to=hear, key=key, reach=v["theme_reach"])
    return max([n[0] + 0.5 for n in notes] + [_THEME_MUSIC_RECORDS.get(n[1], 0) for n in notes])


def _theme_music_again(at, v, tune, hear, key, gen, seconds):
    """Play it again each time it ends, while theme_loop is on and nothing newer started there."""
    while loop.wait(seconds) and v["theme_loop"] and _THEME_MUSIC_GEN.get(key) == gen:
        seconds = _theme_music_play(at, v, tune or v["theme_tune"], hear, key)
        if not seconds:
            return


def sky(v):
    """Where the dragons rise: at (x y z, in the overworld), else height blocks over the first
    player online, below the top of the world; None if the script stopped first."""
    if len(v["at"]) >= 3:
        return world.Pos(v["at"][0], v["at"][1], v["at"][2])
    for _ in loop(every=1):
        for p in loop.who(v["player"]):
            feet, dim = mc.pos(p), world.dim_of(p)
            if feet:
                return world.Pos(feet[0], min(feet[1] + v["height"], world.limits(dim)[1] - 20), feet[2], dim)
    return None


v = values(VALUES)
at = sky(v)
if at:
    fight = loop.background(boss, at, v)            # boss_minions 0: there is no ground in the sky
    theme_music(at, v)                              # theme_loop: it comes round again while they live
    while fight.is_alive() and loop.wait(1):
        pass
    theme_music(at, v, tune="none")                 # and stops when they are dead
