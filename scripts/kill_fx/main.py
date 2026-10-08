#!/usr/bin/env python3
# Kill FX: sounds for each player: a death sting, kill-streak chimes, hit markers when they
# deal damage and a thud when they take it.
import time

from mc import rcon, players, score, on_stop, log

OBJ = {"vfg_fx_death": "deathCount", "vfg_fx_kill": "totalKillCount",
       "vfg_fx_dealt": "minecraft.custom:minecraft.damage_dealt",
       "vfg_fx_taken": "minecraft.custom:minecraft.damage_taken"}


def play(p, sound, vol, pitch):
    rcon("execute as %s at @s run playsound %s master @s ~ ~ ~ %s %s" % (p, sound, vol, pitch))


def cleanup():
    for o in OBJ:
        rcon("scoreboard objectives remove " + o)


on_stop(cleanup)
for o, c in OBJ.items():
    rcon("scoreboard objectives add %s %s" % (o, c))
log("kill fx on")
last, streak, streak_t = {}, {}, {}
while True:
    ps = players()
    if not ps:
        time.sleep(2)
        continue
    for p in ps:
        cur = {o: score(p, o) for o in OBJ}
        prev = last.get(p, cur)
        last[p] = cur
        if cur["vfg_fx_death"] > prev["vfg_fx_death"]:
            streak[p] = 0
            play(p, "entity.player.death", 1, 0.7)
        if cur["vfg_fx_kill"] > prev["vfg_fx_kill"]:
            now = time.time()
            streak[p] = streak.get(p, 0) + 1 if now - streak_t.get(p, 0) < 8 else 1
            streak_t[p] = now
            play(p, "entity.experience_orb.pickup", 1, "%.1f" % min(0.8 + 0.2 * streak[p], 2.0))
            if streak[p] >= 3:
                play(p, "entity.firework_rocket.twinkle", 1, 1)
        if cur["vfg_fx_dealt"] > prev["vfg_fx_dealt"]:
            play(p, "entity.arrow.hit_player", 0.6, 1.6)
        if cur["vfg_fx_taken"] > prev["vfg_fx_taken"]:
            play(p, "entity.player.hurt_sweet_berry_bush", 0.5, 0.6)
    time.sleep(0.25)
