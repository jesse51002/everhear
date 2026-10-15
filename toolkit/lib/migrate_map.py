"""migrate_map: the old system's actions onto the engine's classes, for `vfg engine migrate`.

The old incantation store (/opt/vfg/incantations.json) bound each phrase to an action, with the
action's values. The engine binds a phrase to an ability (cast) or to a scenario with its args set
once (the game engine spec, part 2). MAP says, for every action a binding could name (the old
library's actions and made/main's), what its binding becomes. One row an action:

    {"ability": "<id>"}
        the binding casts that ability. An ability takes no args: its numbers are its Config, one
        version for everyone, so the binding's own values stay behind, said in a note.
    {"scenario": "<id>", "args": {...}, "values": {...}}
        the binding starts that scenario, its args set once: "args" the fixed ones (a merged
        script's recipe: {"mobs": "zoo"}), then each old value the binding gave, by "values":
            "old_field": "new_arg"                     renamed, the value as it was
            "old_field": ["new_arg", {old: new, ...}]   renamed, the value translated (a value the
                                                       table lacks is refused)
            "old_field": None                          dropped on purpose: it means nothing now
        An old value with no entry stays behind, said in a note; one given empty ("", [], {}) is
        none given, and the arg keeps its default. A player field left as the
        caster's (the binding's caster_fields) becomes "caster", which the engine fills with
        whoever says it.
    {"refuse": "<why, in words>"}
        it cannot be moved: the words say why and what to do instead.

and "note": "<words>" on any row, said with every binding it moves. An action with no row is
left behind, never guessed.

    translate(action, entry) -> (kind, id, args, notes)   or raises Refused(words)

Python 3.9+, standard library only, like toolkit/vfg.
"""


class Refused(Exception):
    """An old binding that cannot be moved; the message says why, in words."""


MAP = {
    # ---- KrownJM's three, bound today (inventory G4): blizzard_freeze and fire_aura_krown are made/main's,
    # their abilities made/main/content's; fireball_cast is the library's, merged into its fireball.
    "blizzard_freeze": {"ability": "blizzard"},
    "fire_aura_krown": {"ability": "flame"},
    "fireball_cast": {"ability": "fireball"},

    # ---- the rest of made/main (inventory G4); nothing binds these today.
    # blizzard_styrka → blizzard: the same freeze-and-hurt pulse round the caster, repeated; the storm is a Config.
    "blizzard_styrka": {"ability": "blizzard",
                        "note": "blizzard_styrka's storm (pulses for some seconds) is blizzard's storm_s Config, one "
                                "for everyone; at its default 0 the phrase casts one burst, as KrownJM's freeze"},
    # destroy_everything → nuke: it was made --from nuke and only added the fire phase, which is nuke's fire_s now.
    # Its countdown fields were never used (the script skipped its countdown), so they are dropped. Its nuke_at
    # default "KrownJM" is not carried over: it goes off where its players stand (the caster), as it says it does.
    "destroy_everything": {"scenario": "nuke",
                           "args": {"countdown_s": 0, "countdown_text": "Destroy everything", "radius": 30,
                                    "fire_s": 5, "leave": "fire"},
                           "values": {"player": "players", "nuke_at": "at", "nuke_kill": "kill",
                                      "fire_seconds": "fire_s", "crater_radius": "radius", "crater_leave": "leave",
                                      "countdown_seconds": None, "countdown_text": None},
                           "note": "destroy_everything went off where KrownJM stood unless told where (its nuke_at "
                                   "default); now it goes off where whoever says it stands"},
    # hot_fireball → fireball: merged into fireball_cast's class, and fireball's Config defaults are its numbers.
    "hot_fireball": {"ability": "fireball"},
    # ice_beam_cast → ice_beam: a one-shot cast of the library's beam (made --from ice_beam), merged into it.
    "ice_beam_cast": {"ability": "ice_beam"},
    # speed_dash → made/main's own speed_dash ability (a push forward), as inventory G4 plans it.
    "speed_dash": {"ability": "speed_dash"},
    # made/main's three made on 2026-10-13, after the port (inventory G4 has none of them): no engine class yet.
    "black_hole_krown": {"refuse": "black_hole_krown is made/main's, made after the port, and has no engine class "
                                   "yet: port it into made/main/content/ as an ability, then bind its phrase by hand (engine_bind)."},
    "vortex_charge_krown": {"refuse": "vortex_charge_krown is made/main's, made after the port, and has no engine "
                                      "class yet: port it into made/main/content/ as an ability, then bind its "
                                      "phrase by hand (engine_bind)."},
    "red_sword_krown": {"refuse": "red_sword_krown gave a sword, and has no engine class yet (made/main's, made "
                                  "after the port): port it as an item and its sweep as an ability, then give the "
                                  "item with engine_give; an incantation casts an ability or starts a scenario."},

    # ---- the library's utilities, each a scenario that takes args now (DESIGN2 §7).
    "command": {"scenario": "command", "values": {"commands": "commands"}},
    "teleport": {"scenario": "teleport",
                 "values": {"player": "players", "to": "to", "target": "target", "dimension": "dimension"}},
    "save_place": {"scenario": "save_place", "values": {"name": "name", "player": "player", "at": "at"}},
    # summon_around → horde with stay (one wave, left in the world), at summon_around's own count, radius and
    # no "Wave N"; its one mob becomes horde's list of one (the table, as the row format cannot wrap a value).
    "summon_around": {"scenario": "horde",
                      "args": {"waves": 1, "stay": True, "announce": False, "per_player": 10, "spread": 16},
                      "values": {"players": "players", "count": "per_player", "radius": "spread", "scale": "scale",
                                 "name": "name", "glowing": "glowing",
                                 "mob": ["mobs", {k: [m] for m in (
                                     "allay armadillo axolotl bat bee blaze bogged breeze camel camel_husk cat "
                                     "cave_spider chicken cod copper_golem cow creaking creeper dolphin donkey drowned "
                                     "elder_guardian ender_dragon enderman endermite evoker fox frog ghast giant "
                                     "glow_squid goat guardian happy_ghast hoglin horse husk illusioner iron_golem "
                                     "llama magma_cube mooshroom mule nautilus ocelot panda parched parrot phantom pig "
                                     "piglin piglin_brute pillager polar_bear pufferfish rabbit ravager salmon sheep "
                                     "shulker silverfish skeleton skeleton_horse slime sniffer snow_golem spider squid "
                                     "stray strider sulfur_cube tadpole trader_llama tropical_fish turtle vex villager "
                                     "vindicator wandering_trader warden witch wither wither_skeleton wolf zoglin "
                                     "zombie zombie_horse zombie_nautilus zombie_villager zombified_piglin").split()
                                     for k in (m, "minecraft:" + m)}]}},

    # ---- the library's scenarios, each its own class under the same name; "player" is "players" now. A value
    # left out has no arg (most are Config now, one for everyone) and stays behind, said in a note.
    "anti_camp": {"scenario": "anti_camp",
                  "values": {"player": "players", "stands_seconds": "seconds", "stands_radius": "radius",
                             "stands_warning": "warning", "tnt": "tnt", "height": "height", "fuse": "fuse",
                             "mock": "mock"}},
    # arena: leaves_area's "player" (stay near the others) is shape "near" now.
    "arena": {"scenario": "arena",
              "values": {"player": "players", "message": "message",
                         "leaves_area": ["shape", {"radius": "radius", "box": "box", "player": "near"}],
                         "leaves_radius": "radius", "leaves_center": "center", "leaves_box": "box",
                         "leaves_of_player": "near"}},
    "castle": {"scenario": "castle",
               "values": {"player": "players", "castle_size": "size", "castle_height": "height",
                          "shape_blocks": "walls", "castle_tower_blocks": "towers", "castle_ahead": "ahead",
                          "castle_at": "at", "castle_sign": "sign", "castle_stairs": "stairs",
                          "shape_facing": "facing"}},
    "chosen_one": {"scenario": "chosen_one", "values": {"player": "player", "blade": "blade"}},
    "demon_lord": {"scenario": "demon_lord", "values": {"round": "round", "level": "level"}},
    # god_visits: the director's events and weights have no args (a gift or a take, by gift_weight/take_weight).
    "god_visits": {"scenario": "god_visits",
                   "values": {"player": "players", "director_min_s": "min_s", "director_max_s": "max_s",
                              "director_each": "each", "gifts": "gifts", "take_items": "take_items",
                              "take_most": "take_most", "stay_s": "stay_s"}},
    "grave_marks": {"scenario": "grave_marks",
                    "values": {"player": "players", "seconds": "seconds", "found_within": "found_within",
                               "on_death_event": "on"}},
    "horde": {"scenario": "horde",
              "values": {"player": "players", "horde_mobs": "mobs", "horde_per_player": "per_player",
                         "horde_waves": "waves", "horde_every": "every_s", "horde_spread": "spread",
                         "horde_cap": "cap", "horde_explode": "explode", "horde_announce": "announce"}},
    "kill_fx": {"scenario": "kill_fx",
                "values": {"player": "players", "on_death_event": "on", "streak_s": "streak_s",
                           "hit_markers": "hit_markers", "hurt_sound": "hurt_sound", "volume": "volume"}},
    # launch_pad: launch_method is dropped on purpose (its "ride" is gone: one way to throw, the kit's push).
    "launch_pad": {"scenario": "launch_pad",
                   "values": {"player": "players", "pad_block": "block", "launch_way": "way",
                              "launch_strength": "strength", "launch_landing": "landing", "cooldown": "cooldown",
                              "at": "at", "launch_method": None}},
    "lava_rain": {"scenario": "lava_rain",
                  "values": {"player": "players", "every": "every_s", "drops": "drops", "sky_count": "count",
                             "sky_radius": "radius"}},
    "lucky_blocks": {"scenario": "lucky_blocks", "values": {"player": "players", "give": "give"}},
    "meteor": {"scenario": "meteor",
               "values": {"player": "players", "sky_count": "count", "sky_radius": "radius", "meteor_size": "size",
                          "explosion_power": "power", "explosion_breaks": "breaks"}},
    "mountains": {"scenario": "mountains",
                  "values": {"player": "players", "away": "away", "corner": "corner", "seed": "seed"}},
    "nuke": {"scenario": "nuke",
             "values": {"player": "players", "nuke_at": "at", "nuke_kill": "kill", "countdown_seconds": "countdown_s",
                        "countdown_text": "countdown_text", "crater_radius": "radius", "crater_leave": "leave"}},
    # sky_platform: shape_facing is dropped on purpose (a square platform looks the same each way).
    "sky_platform": {"scenario": "sky_platform",
                     "values": {"player": "players", "platform_y": "y", "platform_size": "size",
                                "platform_at": "at", "shape_blocks": "blocks", "platform_spawn": "spawn",
                                "shape_facing": None}},
    "sneaky_creepers": {"scenario": "sneaky_creepers",
                        "values": {"player": "players", "invisible_within": "invisible_within",
                                   "hiss_within": "hiss_within", "fuse": "fuse"}},
    "sniper_nest": {"scenario": "sniper_nest",
                    "values": {"player": "players", "nest_height": "height", "nest_ahead": "ahead", "nest_at": "at",
                               "nest_sign": "sign", "nest_spawn": "spawn"}},
    "tnt_rain": {"scenario": "tnt_rain",
                 "values": {"player": "players", "every": "every_s", "sky_count": "count", "sky_radius": "radius",
                            "fuse": "fuse_s"}},
    "walking_dead": {"scenario": "walking_dead",
                     "values": {"player": "players", "walk_speed": "walk_speed", "sneak_range": "sneak_range",
                                "walk_range": "walk_range", "sprint_range": "sprint_range", "friends": "friends"}},
    "world_replace": {"scenario": "world_replace",
                      "values": {"player": "players", "replace_rules": "rules", "radius": "radius", "height": "height",
                                 "dims": "dims", "replace_rate": "rate"}},

    # ---- library scripts merged into another scenario (DESIGN2 §7): its recipe as fixed args, then its values.
    # chunk_changer → walk_replace per chunk, with its own snow rule and enters_chunk's 32 below, 40 above.
    # replace_rate has no arg there (walk_replace's NEEDS: a weighted "keep" in the rule does it), so it stays.
    "chunk_changer": {"scenario": "walk_replace",
                      "args": {"per": "chunk", "below": 32, "above": 40,
                               "rules": "#minecraft:snow > white_wool light_gray_wool red_wool blue_wool lime_wool "
                                        "yellow_wool cyan_wool purple_wool orange_wool pink_wool white_concrete "
                                        "light_blue_concrete green_concrete magenta_concrete terracotta "
                                        "orange_terracotta brown_terracotta oak_planks spruce_planks dark_oak_planks "
                                        "stone andesite granite diorite stone_bricks deepslate quartz_block glass "
                                        "coal_ore lapis_ore"},
                      "values": {"player": "players", "replace_rules": "rules", "around": "around_chunks",
                                 "replace_cell": "cell", "enters_dims": "dims", "enters_below": "below",
                                 "enters_above": "above"}},
    # hellscape → world_replace with rules "hellscape", and hellscape's own reach: 48 round, 32 up and down, overworld.
    "hellscape": {"scenario": "world_replace",
                  "args": {"rules": "hellscape", "radius": 48, "height": 32, "dims": ["overworld"]},
                  "values": {"player": "players", "radius": "radius", "height": "height", "dims": "dims",
                             "replace_rules": "rules", "replace_rate": "rate"}},
    # herobrine → horror with herobrine's nine events, weights, waits and words, and no ash (haze off: his own look).
    "herobrine": {"scenario": "horror",
                  "args": {"events": ["sound", "door", "fake_join", "message", "shadow", "torches", "dark", "sign",
                                      "lightning"],
                           "weights": [3, 1, 1, 1, 1, 1, 1, 1, 1], "min_s": 60, "max_s": 150,
                           "messages": ["i see you", "turn around", "you are not alone", "behind you"],
                           "haze": False},
                  "values": {"player": "players", "director_events": "events", "director_weights": "weights",
                             "director_min_s": "min_s", "director_max_s": "max_s", "director_messages": "messages",
                             "director_each": "each"}},
    # horror_mode → horror: its defaults are horror_mode's.
    "horror_mode": {"scenario": "horror",
                    "values": {"player": "players", "no_dark": "no_dark", "director_events": "events",
                               "director_weights": "weights", "director_min_s": "min_s",
                               "director_max_s": "max_s", "director_messages": "messages", "director_each": "each"}},
    # horde_boss → boss_fight's horde_king (its defaults). The old boss_music was a disc on top of the fight; the
    # engine has one boss music, "boss" (Pigstep with a growl), so any disc becomes it. Minions, attack, gear, bar
    # colour and phases are the Actor's own Config now, so they stay behind.
    "horde_boss": {"scenario": "boss_fight",
                   "args": {"boss": "horde_king"},
                   "values": {"player": "near", "at": "at", "boss_name": "name", "boss_mob": "mob",
                              "boss_health": "health", "boss_scale": "scale", "boss_bodies": "bodies",
                              "boss_reward": "reward", "boss_reward_count": "reward_count",
                              "boss_griefing": "griefing",
                              "boss_music": ["music", {"none": "none", "pigstep": "boss", "otherside": "boss",
                                                       "relic": "boss", "creator": "boss", "precipice": "boss"}]}},
    # ice_castle → castle with ice_castle's packed ice walls and blue ice towers.
    "ice_castle": {"scenario": "castle",
                   "args": {"walls": "packed_ice", "towers": "blue_ice"},
                   "values": {"player": "players", "castle_size": "size", "castle_height": "height",
                              "shape_blocks": "walls", "castle_tower_blocks": "towers", "castle_ahead": "ahead",
                              "castle_at": "at", "castle_sign": "sign", "castle_stairs": "stairs",
                              "shape_facing": "facing"}},
    # ice_path → walk_replace per step, a path under the feet alone: radius 2, 1 below, 0 above.
    "ice_path": {"scenario": "walk_replace",
                 "args": {"per": "step", "radius": 2, "below": 1, "above": 0},
                 "values": {"player": "players", "flash": "flash", "under_radius": "radius", "under_depth": "below",
                            "under_above": "above", "walk_every": "every_s", "replace_rules": "rules",
                            "replace_cell": "cell"}},
    # ice_queen → boss_fight's ice_queen, with her blue ice reward. Her freeze, servants, cold and gear are her
    # abilities' and the Actor's Config now, so they stay behind.
    "ice_queen": {"scenario": "boss_fight",
                  "args": {"boss": "ice_queen", "reward": "blue_ice", "reward_count": 16},
                  "values": {"player": "near", "at": "at", "boss_name": "name", "boss_mob": "mob",
                             "boss_health": "health", "boss_scale": "scale", "boss_bodies": "bodies",
                             "boss_reward": "reward", "boss_reward_count": "reward_count",
                             "boss_griefing": "griefing",
                             "boss_music": ["music", {"none": "none", "pigstep": "boss", "otherside": "boss",
                                                      "relic": "boss", "creator": "boss", "precipice": "boss"}]}},
    # ice_walk → walk_replace per step: its defaults are ice_walk's.
    "ice_walk": {"scenario": "walk_replace",
                 "args": {"per": "step"},
                 "values": {"player": "players", "flash": "flash", "under_radius": "radius", "under_depth": "below",
                            "under_above": "above", "walk_every": "every_s", "replace_rules": "rules",
                            "replace_cell": "cell"}},
    # meteor_shower → meteor with eight meteors round each, 40 out.
    "meteor_shower": {"scenario": "meteor",
                      "args": {"count": 8, "radius": 40},
                      "values": {"player": "players", "sky_count": "count", "sky_radius": "radius",
                                 "meteor_size": "size", "explosion_power": "power", "explosion_breaks": "breaks"}},
    # tether → arena kept near the others: shape near, 30 out, "Stay with the group".
    "tether": {"scenario": "arena",
               "args": {"shape": "near", "radius": 30, "message": "Stay with the group"},
               "values": {"player": "players", "message": "message", "leaves_of_player": "near",
                          "leaves_radius": "radius",
                          "leaves_area": ["shape", {"radius": "radius", "box": "box", "player": "near"}],
                          "leaves_center": "center", "leaves_box": "box"}},
    # three_headed_dragon → boss_fight's dragon_head, three bodies 30 up, its boss music, no griefing. Its boss
    # tune always played (a disc only went on top), so music is "boss" whatever boss_music said.
    "three_headed_dragon": {"scenario": "boss_fight",
                            "args": {"boss": "dragon_head", "bodies": 3, "height": 30, "music": "boss",
                                     "griefing": False, "name": "Three-Headed Dragon"},
                            "values": {"player": "near", "at": "at", "height": "height", "boss_name": "name",
                                       "boss_mob": "mob", "boss_health": "health", "boss_scale": "scale",
                                       "boss_bodies": "bodies", "boss_reward": "reward",
                                       "boss_reward_count": "reward_count", "boss_griefing": "griefing",
                                       "boss_music": ["music", {"none": "boss", "pigstep": "boss",
                                                                "otherside": "boss", "relic": "boss",
                                                                "creator": "boss", "precipice": "boss"}]}},
    # zoo → horde with the zoo's twenty animals, sent until stopped, one wave a second, 20 each, 200 at most, 8 out.
    "zoo": {"scenario": "horde",
            "args": {"mobs": ["pig", "cow", "sheep", "chicken", "rabbit", "fox", "goat", "llama", "cat", "wolf",
                              "parrot", "frog", "turtle", "axolotl", "panda", "horse", "donkey", "bee", "armadillo",
                              "camel"],
                     "waves": 0, "every_s": 1, "per_player": 20, "cap": 200, "spread": 8, "announce": False},
            "values": {"player": "players", "horde_mobs": "mobs", "horde_per_player": "per_player",
                       "horde_cap": "cap", "horde_every": "every_s", "horde_spread": "spread",
                       "horde_waves": "waves", "horde_explode": "explode", "horde_announce": "announce"}},

    # ---- library actions that gave a weapon, until stopped: the phrase gave the item, and an incantation can only
    # cast an ability or start a scenario. Each weapon is an Item now, and most fire an ability of their own.
    "boom_bow": {"refuse": "boom_bow made a player's arrows explode until stopped; it is now the boom_bow item, a "
                           "bow with no ability to cast, and an incantation can only cast an ability or start a "
                           "scenario: give the bow with engine_give."},
    "boom_trident": {"refuse": "boom_trident gave an exploding trident; it is now the boom_trident item, with no "
                               "ability to cast, and an incantation can only cast an ability or start a scenario: "
                               "give it with engine_give."},
    "galick_gun": {"refuse": "galick_gun gave a weapon; an incantation can only cast an ability or start a "
                             "scenario: give the galick_gun item with engine_give, or bind galick_gun_beam to the "
                             "phrase by hand."},
    "gatling_gun": {"refuse": "gatling_gun gave a weapon; an incantation can only cast an ability or start a "
                              "scenario: give the gatling_gun item with engine_give, or bind gatling_shot to the "
                              "phrase by hand."},
    "gravity_crush": {"refuse": "gravity_crush gave a staff; an incantation can only cast an ability or start a "
                                "scenario: give the gravity_crush_staff item with engine_give, or bind gravity_crush "
                                "to the phrase by hand."},
    "gravity_lift": {"refuse": "gravity_lift gave a staff; an incantation can only cast an ability or start a "
                               "scenario: give the gravity_lift_staff item with engine_give, or bind gravity_lift "
                               "to the phrase by hand."},
    "hollow_purple": {"refuse": "hollow_purple gave a weapon; an incantation can only cast an ability or start a "
                                "scenario: give the hollow_purple item with engine_give, or bind hollow_purple_beam "
                                "to the phrase by hand."},
    "ice_beam": {"refuse": "ice_beam gave a staff; an incantation can only cast an ability or start a scenario: "
                           "give the ice_beam_staff item with engine_give, or bind the ice_beam ability to the "
                           "phrase by hand."},
    "ice_staff": {"refuse": "ice_staff gave a staff; an incantation can only cast an ability or start a scenario: "
                            "give the ice_staff item with engine_give, or bind ice_staff_freeze to the phrase by "
                            "hand."},
    "infinity_gauntlet": {"refuse": "infinity_gauntlet gave a weapon; an incantation can only cast an ability or "
                                    "start a scenario: give the infinity_gauntlet item with engine_give, or bind "
                                    "infinity_gauntlet_shot to the phrase by hand."},
    "lightning_staff": {"refuse": "lightning_staff gave a staff; an incantation can only cast an ability or start a "
                                  "scenario: give the lightning_staff item with engine_give, or bind "
                                  "lightning_strike to the phrase by hand."},
    "meteor_staff": {"refuse": "meteor_staff gave a staff; an incantation can only cast an ability or start a "
                               "scenario: give the meteor_staff item with engine_give, or bind meteor_strike to the "
                               "phrase by hand."},
    "railgun": {"refuse": "railgun gave a weapon; an incantation can only cast an ability or start a scenario: give "
                          "the railgun item with engine_give, or bind railgun_shot to the phrase by hand."},
    "sniper_rifle": {"refuse": "sniper_rifle gave a weapon; an incantation can only cast an ability or start a "
                               "scenario: give the super_sniper item with engine_give, or bind sniper_shot to the "
                               "phrase by hand."},
    "spear_bomb": {"refuse": "spear_bomb gave an exploding trident; it is now the spear_bomb item, with no ability "
                             "to cast, and an incantation can only cast an ability or start a scenario: give it with "
                             "engine_give."},
    "spirit_bomb": {"refuse": "spirit_bomb gave a weapon; an incantation can only cast an ability or start a "
                              "scenario: give the spirit_bomb item with engine_give, or bind spirit_bomb_cast to the "
                              "phrase by hand."},
    "summoning_staff": {"refuse": "summoning_staff gave a staff; an incantation can only cast an ability or start a "
                                  "scenario: give the summoning_staff item with engine_give, or bind "
                                  "summoning_staff_summon to the phrase by hand."},
    "wither_wand": {"refuse": "wither_wand gave a wand; an incantation can only cast an ability or start a "
                              "scenario: give the wither_wand item with engine_give, or bind wither_wand_summon to "
                              "the phrase by hand."},

    # ---- library actions that gave a lasting power: a passive ability or a mark now, which a phrase cannot give.
    "barrier_shield": {"refuse": "barrier_shield kept a barrier round a player until stopped; it is now the "
                                 "barrier_shield ability, a passive one granted with engine_grant, or the "
                                 "place_barrier scenario for one at a place, and an incantation can only cast a "
                                 "triggered ability or start a scenario: bind place_barrier by hand if that is "
                                 "what the phrase meant."},
    "death_gaze": {"refuse": "death_gaze gave a player the gaze until stopped; it is now the death_gaze ability, a "
                             "passive one that fires while they sneak, and an incantation can only cast a triggered "
                             "ability or start a scenario: grant it with engine_grant."},
    "keep_effects": {"refuse": "keep_effects kept an effect on players until stopped; it is now the potion_effect "
                               "mark (its amplifier one below the old level), applied with engine_apply and taken "
                               "off with engine_clear, and an incantation can only cast an ability or start a "
                               "scenario."},
    "projectile_shield": {"refuse": "projectile_shield turned back what was shot at a player until stopped; it is "
                                    "now the barrier_shield ability with its Config what set to projectiles (one "
                                    "for everyone, engine_config), granted with engine_grant, or place_barrier with "
                                    "what projectiles at a place, and an incantation can only cast a triggered "
                                    "ability or start a scenario."},
    # super_saiyan's ability is triggered, but the old phrase gave the power (three crouches, or the star) until
    # stopped; it never powered up at once, so casting it would be a different thing.
    "super_saiyan": {"refuse": "super_saiyan gave a player the power to go Super Saiyan (three crouches, or the "
                               "star) until stopped: grant the super_saiyan ability with engine_grant and give the "
                               "super_saiyan_star item with engine_give, or bind super_saiyan to the phrase by hand "
                               "to power up by voice."},
    "zeus": {"refuse": "zeus made a player's thrown tridents call lightning until stopped; it is now the zeus "
                       "ability, a passive one, and an incantation can only cast a triggered ability or start a "
                       "scenario: grant it with engine_grant."},

    # ---- library actions with nothing to move to.
    # attribute_guard: dropped (DESIGN2 §7): the engine owns the attributes it changes.
    "attribute_guard": {"refuse": "attribute_guard is dropped: the engine owns the attributes it changes and puts "
                                  "them back itself, so nothing replaces it; to make a player normal again, clear "
                                  "their marks with engine_clear."},
    # mountain_trees: merged into mountains as its trees arg, which plants as it builds; the old action planted on a
    # range already built, and starting mountains would build a new one.
    "mountain_trees": {"refuse": "mountain_trees planted spruce on mountains already built; the engine's mountains "
                                 "plants its own trees as it builds (its trees arg), and nothing plants on a range "
                                 "already there: bind mountains by hand if a new range is what the phrase meant."},
    # weapon_chest and gun_chests: kept as one weapon_chest scenario (inventory G1, DESIGN2 §7), not ported yet.
    "weapon_chest": {"refuse": "weapon_chest has no engine class yet: its scenario, the chest of six powered "
                               "weapons, is not in engine/library, so bind it again by hand once it is."},
    "gun_chests": {"refuse": "gun_chests has no engine class yet: it merges into the weapon_chest scenario, which "
                             "is not in engine/library, so bind it again by hand once it is."},
}


def _words(v):
    """A value as a note shows it."""
    import json
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def translate(action, entry):
    """One old binding's action and values as (kind, id, args, notes): kind "ability" or
    "scenario". Raises Refused with why when it cannot be moved, or when the action has no row."""
    row = MAP.get(action)
    if row is None:
        raise Refused("%s has no engine class in the migration table" % action)
    if row.get("refuse"):
        raise Refused(row["refuse"])
    asked = entry.get("asked") if isinstance(entry.get("asked"), dict) else entry.get("values")
    asked = dict(asked) if isinstance(asked, dict) else {}
    casters = [str(f) for f in entry.get("caster_fields") or []]
    notes = [row["note"]] if row.get("note") else []
    if row.get("ability"):
        given = dict((k, v) for k, v in asked.items() if k not in casters)
        if given:
            notes.append("its values %s stay behind: %s's Config is one version for everyone"
                         % (_words(given), row["ability"]))
        return "ability", row["ability"], {}, notes
    scenario = row.get("scenario")
    if not scenario:
        raise Refused("%s's row in the migration table names no ability, scenario or refusal" % action)
    args = dict(row.get("args") or {})
    rules = row.get("values") or {}
    left = {}
    for field, value in asked.items():
        if field in casters or value in ("", [], {}, None):   # empty: none given, the arg's own default stands
            continue
        if field not in rules:
            left[field] = value
            continue
        rule = rules[field]
        if rule is None:
            continue
        if isinstance(rule, (list, tuple)):
            arg, table = rule[0], rule[1]
            key = value if isinstance(value, (str, int, float, bool)) else _words(value)
            if key not in table:
                raise Refused("%s=%s has no %s %s in the engine: bind it again by hand"
                              % (field, _words(value), scenario, arg))
            args[arg] = table[key]
        else:
            args[rule] = value
    for field in casters:
        rule = rules.get(field)
        arg = rule[0] if isinstance(rule, (list, tuple)) else rule
        if arg:
            args[arg] = "caster"
        else:
            notes.append("its caster's %s has no arg in %s: whoever says it is not filled in" % (field, scenario))
    if left:
        notes.append("its values %s stay behind: %s has no arg for them" % (_words(left), scenario))
    return "scenario", scenario, args, notes
