"""Running a step: actions together or in turn with no task, each copy named for its player, a
refused command's error and reference as its result, the tools that refuse with no model, the read
tool, an ended step shown as what it sent, and the ending's pieces: a task opened with its brief, a
follow-up, the cancel words."""

import asyncio
import json

from brain.act import CANCEL, CANCEL_ROW, StepRun, changed_nothing, record_of
from brain.call import step_type
from brain.vfg import VfgResult
from brain.world import Seen

from conftest import REPO_COPY, STATE, act, cmd, follow_up, hand_off, ran, step

SEEN = Seen(places="SAVED PLACES: home 1 64 2 overworld (home_steve)", known={"home"})
RAILGUN = {"action": "railgun", "values": {"player": "Steve", "cooldown": 5}, "live": ["cooldown"], "task": None}


def decided(k, answer: dict, **moment):
    return step_type(k.cat, **moment).model_validate_json(json.dumps(answer))


async def run(k, answer: dict, player: str = "Steve") -> StepRun:
    got = await k.acts.run(decided(k, answer), n=1, player=player, seen=SEEN, refs=k.brain.refs, ref="#812 s1")
    await k.settle()
    return got


async def test_actions_run_together_at_once_each_copy_named_for_its_player_with_no_task(kit):
    k = kit()
    started, both = [], asyncio.Event()

    async def held(argv):
        started.append(argv[2])
        if len(started) == 3:
            both.set()
        await asyncio.wait_for(both.wait(), 1)  # each ends only once all three have begun
        return ran(True, "done", copy=argv[2])

    k.vfg.on("action", "run", answer=held)
    got = await run(k, step(act("command", "give Steve a diamond pickaxe", cmd("give Steve diamond_pickaxe")),
                            act("railgun", "a railgun for Alex", {"player": "Alex"}),
                            act("teleport", "teleport everyone home", {"player": "@a", "to": "place",
                                                                       "target": "home"}), reply="there"))
    assert sorted(started) == ["command", "railgun", "teleport"] and got.landed
    assert sorted(k.vfg.of("action", "run")) == sorted([
        ["action", "run", "command", 'commands=["give Steve diamond_pickaxe"]', "--for", "Steve"],
        ["action", "run", "railgun", "player=Alex", "--for", "Alex"],
        ["action", "run", "teleport", "player=@a", "to=place", "target=home", "--for", "Steve"]])
    assert [r.state for r in got.actions] == ["landed"] * 3
    assert k.vfg.of("feed") == [] and k.vfg.of("say") == [] and k.tasks.all() == []  # no row per action


async def test_in_turn_runs_one_after_another_and_stops_at_the_first_that_does_not_land(kit):
    k = kit()
    k.vfg.on("action", "run", "teleport", answer=ran(False, "no saved place called home"))
    got = await run(k, step(act("command", "give Steve bread", cmd("give Steve bread")),
                            act("teleport", "teleport Steve home",
                                {"player": "Steve", "to": "place", "target": "home"}),
                            act("command", "make it day", cmd("time set day")), order="in_turn", end="silent"))
    assert [a[2] for a in k.vfg.of("action", "run")] == ["command", "teleport"]
    assert [r.state for r in got.actions] == ["landed", "refused", "not run"] and not got.landed
    shown = got.show()
    assert shown.startswith("THE LOOP SO FAR (")
    assert ("STEP 1, in turn:\n- command: give Steve bread commands=[\"give Steve bread\"]\n  LANDED: done\n"
            "- teleport: teleport Steve home player=Steve to=place target=home\n"
            "  REFUSED: no saved place called home\n"
            "- command: make it day commands=[\"time set day\"]\n  NOT RUN: an earlier one did not land\n"
            "NOT SENT: you meant to end silent, but not everything landed") in shown
    assert record_of([got]) == "give Steve bread — done; teleport Steve home — refused: no saved place called home"


async def test_a_refused_command_comes_back_with_minecrafts_error_and_its_reference_never_retried(kit):
    k = kit()
    k.vfg.on("action", "run", answer=ran(False, "Fire Aspect V is higher than the maximum level of II"))
    got = await run(k, step(act("command", "Fire Aspect V on KrownJM's axe",
                                cmd("enchant KrownJM minecraft:fire_aspect 5")), reply="your axe burns now"),
                    player="KrownJM")
    assert len(k.vfg.of("action", "run")) == 1 and not got.landed  # no retry, no planner
    [r] = got.actions
    assert (r.state, r.said) == ("refused", "Fire Aspect V is higher than the maximum level of II")
    assert r.reference == "## enchant\nSyntax on this server:\nenchant <targets> …"
    assert ("  REFUSED: Fire Aspect V is higher than the maximum level of II\n"
            "  THE REFERENCE OF THE COMMANDS IT USED (each command's syntax on this server, then the wiki's Java "
            "Edition notes):\n## enchant\nSyntax on this server:") in got.show()
    assert "you meant to end reply, reply 'your axe burns now'" in got.show()


async def test_what_cannot_land_is_a_result_offline_failed_or_refused_before_it_ran(kit):
    k = kit()
    k.running({"railgun_steve": RAILGUN})
    k.vfg.on("action", "run", "railgun", answer=ran(False, "Alex is not online", offline=["Alex"]))
    k.vfg.on("action", "run", "summon_around", answer=VfgResult(124, "", "timed out"))
    got = await run(k, step(act("railgun", "a railgun for Alex", {"player": "Alex"}),
                            act("summon_around", "5 zombies round Steve", {"players": "Steve", "mob": "zombie",
                                                                           "count": 5}),
                            act("stop", "stop Alex's railgun", {"copy": "railgun_alex"}),
                            act("edit", "a far railgun for Steve", {"copy": "railgun_steve", "values.range": "far"}),
                            end="silent"))
    assert [(r.state, r.said.split(";")[0]) for r in got.actions] == [
        ("refused", "Alex not online"), ("failed", "timed out after 60 s"),
        ("refused", "refused before it ran: nothing called railgun_alex is running"),
        ("refused", "refused before it ran: range=far: Input should be a valid integer, unable to parse string as an "
                    "integer")]
    assert [a[2] for a in k.vfg.of("action", "run")] == ["railgun", "summon_around"]


async def test_what_could_not_run_did_not_happen_a_value_vfg_refused_is_refused_a_box_out_of_reach_did_not_run(kit):
    k = kit()
    k.vfg.on("action", "run", "railgun", answer=VfgResult(2, "", "vfg: railgun has no value range_x"))
    k.vfg.on("action", "run", "command", answer=VfgResult(4, "", "vfg: cannot reach EC2"))
    got = await run(k, step(act("railgun", "a railgun for Alex", {"player": "Alex"}),
                            act("command", "give Steve bread", cmd("give Steve bread")), end="silent"))
    assert [(r.state, r.said) for r in got.actions] == [
        ("refused", "vfg refused it before it ran: vfg: railgun has no value range_x"),
        ("did not run", "vfg: cannot reach EC2")]
    assert "  DID NOT RUN: vfg: cannot reach EC2" in got.show()


async def test_an_action_whose_facts_say_it_changed_nothing_did_not_land(kit):
    k = kit()
    k.running({"railgun_steve": RAILGUN})
    k.vfg.on("action", "run", "edit", answer=[ran(True, "railgun_steve: nothing changed", copy="railgun_steve",
                                                  changed=[]),
                                              ran(True, "railgun_steve: cooldown=0, live", copy="railgun_steve",
                                                  changed={"cooldown": 0})])
    zero = act("edit", "no cooldown on Steve's railgun", {"copy": "railgun_steve", "values.cooldown": 0})
    got = await run(k, step(zero, reply="no more cooldown"))
    assert [(r.state, r.said) for r in got.actions] == [("changed nothing", "railgun_steve: nothing changed")]
    assert not got.landed and "  CHANGED NOTHING: railgun_steve: nothing changed" in got.show()
    got = await run(k, step(zero, reply="no more cooldown"))  # it changed something: landed
    assert got.landed and got.actions[0].state == "landed"
    assert ran(True, "done").stdout and not changed_nothing({}) and not changed_nothing({"changed": ["cooldown 5 s"]})


async def test_edit_and_stop_run_on_copies_running_now_as_actions_of_the_step(kit):
    k = kit()
    k.running({"railgun_steve": RAILGUN})
    got = await run(k, step(act("edit", "no cooldown on Steve's railgun", {"copy": "railgun_steve",
                                                                         "values.cooldown": 0}),
                            act("stop", "stop Steve's railgun", {"copy": "railgun_steve"}), order="in_turn",
                            end="silent"))
    assert k.vfg.of("action", "run") == [["action", "run", "edit", "copy=railgun_steve", "cooldown=0"],
                                         ["action", "run", "stop", "copy=railgun_steve"]]
    assert got.landed and k.vfg.of("feed") == []  # no AI note rows any more


async def test_the_same_action_twice_in_a_step_runs_once(kit):
    k = kit()
    bread = act("command", "give Steve bread", cmd("give Steve bread"))
    got = await run(k, step(bread, bread, end="silent"))
    assert len(k.vfg.of("action", "run")) == 1 and len(got.actions) == 1


async def test_a_follow_up_of_no_such_task_is_refused_at_the_tool_and_comes_back_as_a_result(kit):
    k = kit()
    got = await run(k, step(act("command", "give Steve bread", cmd("give Steve bread")), end="handoff",
                            reply="on it", follow_ups=(follow_up("fireball_krown", "bigger", "A bigger fireball."),)))
    assert not got.landed and got.actions[0].state == "landed"  # the actions ran all the same
    [r] = got.refused
    assert r.what == "follow up fireball_krown" and r.said.startswith("there is no task fireball_krown under TASKS")
    assert "- follow up fireball_krown\n  REFUSED: there is no task fireball_krown" in got.show()


async def test_read_items_gives_a_players_items_by_slot_from_a_state_fetched_for_it(kit):
    k = kit()
    alex = {**STATE["online"][0], "name": "Alex",
            "items": ["Alex has the following entity data: [{Slot: 0b, id: \"minecraft:diamond_axe\", count: 1}]",
                      "Alex has the following entity data: 0"]}
    k.vfg.on("state", answer=VfgResult(0, json.dumps({**STATE, "online": [alex]}), ""))
    await k.world.fresh_state()                             # a young state: the read fetches its own all the same
    got = await run(k, step(end="next", read=("Alex", "Bob")))
    assert [(r.state, r.said) for r in got.reads] == [("read", "Alex's items, by slot: hotbar.0 diamond_axe (held)"),
                                                     ("refused", "Bob is not online")]
    assert len(k.vfg.of("state")) == 3 and got.landed  # a state of its own for each read; a refused read ends nothing
    assert "YOU ENDED IT next: these results are what it was for; nothing was sent." in got.show()


async def test_an_ended_step_a_later_step_follows_shows_what_it_sent(kit):
    """A line added to the loop while its ending went out: the next step reads what the players got."""
    k = kit()
    got = await run(k, step(act("command", "give Steve bread", cmd("give Steve bread")), reply="bread's yours",
                            record="gave Steve bread"))
    shown = got.show(sent=True)
    assert shown.startswith("THE LOOP SO FAR (") and "  LANDED: done\n" in shown and "NOT SENT" not in shown
    assert shown.endswith("ENDED reply AND SENT: reply “bread's yours”, record “gave Steve bread”.\n\n")
    got.said = "sorry, I couldn't start “a hook” — say it again?"  # code's line went out in place of the step's
    assert "SENT: reply “sorry, I couldn't start “a hook” — say it again?”" in got.show(sent=True)
    handed = await run(k, step(end="handoff", hand_offs=(hand_off(),)))
    assert handed.show(sent=True).endswith("- ran nothing\nENDED handoff AND SENT: hand off hot_fireball_krown.\n\n")
    quiet = await run(k, step(act("command", "make it day", cmd("time set day")), end="silent"))
    assert quiet.show(sent=True).endswith("ENDED silent AND SENT: nothing in chat.\n\n")


# ------------------------------------------------------------------ the ending's pieces

async def test_a_hand_offs_task_opens_under_a_free_name_with_the_brief_rendered_and_its_row(kit):
    (REPO_COPY / "made" / "main" / "scripts" / "cold_fireball_krown").mkdir(parents=True, exist_ok=True)
    k = kit()
    h = decided(k, step(end="handoff", hand_offs=(hand_off("cold_fireball_krown"),))).hand_offs[0]
    name = k.acts.open_task(h, player="KrownJM", line="make a hot fireball incantation")
    assert name == "cold_fireball_krown_2"                 # a script folder had the name
    await k.settle()
    t = k.tasks.get(name)
    assert t["brief"] == h.brief() and t["brief"].startswith("THE PLAYER'S WORDS\n")
    assert (t["description"], t["player"], t["status"]) == ("Hot Fireball for KrownJM: say hot fireball.", "KrownJM",
                                                            "open")
    assert k.vfg.rows("task") == [("task cold_fireball_krown_2", "new: Hot Fireball for KrownJM: say hot fireball.")]


async def test_a_follow_up_opens_its_task_again_and_a_cancel_sends_the_cancel_words_and_keeps_the_description(kit):
    k = kit()
    k.tasks.open("frost_bow_alex", "Alex", "a frost bow", "the brief", "A Frost Bow for Alex.")
    k.tasks.close("frost_bow_alex", "done", "frost_bow_alex done")
    s = decided(k, step(end="handoff", follow_ups=(follow_up("frost_bow_alex", "make it shoot fire too",
                                                              "A Frost Bow for Alex that shoots fire too."),)))
    assert k.acts.reopen_task(s.follow_ups[0]) == "make it shoot fire too"
    t = k.tasks.get("frost_bow_alex")
    assert (t["status"], t["brief"], t["description"]) == ("open", "make it shoot fire too",
                                                           "A Frost Bow for Alex that shoots fire too.")
    s = decided(k, step(end="handoff", follow_ups=(follow_up("frost_bow_alex", cancel=True),)))
    assert k.acts.reopen_task(s.follow_ups[0]) == CANCEL
    assert CANCEL == ("CANCELLED: the player said never mind. Stop building and put everything back the way it was "
                      "before the run this cancels, then report Cancelled: and what you put back.")
    t = k.tasks.get("frost_bow_alex")
    assert (t["brief"], t["description"]) == (CANCEL, "A Frost Bow for Alex that shoots fire too.")
    await k.settle()
    assert k.vfg.rows("task") == [("task frost_bow_alex", "follow-up: make it shoot fire too"),
                                  ("task frost_bow_alex", f"follow-up: {CANCEL_ROW}")]
    assert k.acts.reopen_task(decided(k, step(end="handoff", follow_ups=(follow_up("x_y", cancel=True),)))
                              .follow_ups[0]) is None


async def test_an_implementer_that_cannot_start_fails_its_task_by_name(kit):
    async def broken(name, brief):
        raise RuntimeError("no CLI")

    k = kit()
    k.impl.start = broken
    k.tasks.open("hot_fireball_krown", "KrownJM", "…", "the brief", "Hot Fireball.")
    await k.acts.start("hot_fireball_krown", "the brief", "KrownJM")
    await k.settle()
    t = k.tasks.get("hot_fireball_krown")
    assert (t["status"], t["result"]) == ("failed", "hot_fireball_krown failed: the implementer could not start: "
                                                    "RuntimeError: no CLI")
    assert k.vfg.rows("result") == [("result hot_fireball_krown", t["result"])]


async def test_a_private_line_goes_to_one_player_with_vfg_say_to(kit):
    k = kit()
    await k.acts.say("Still on: “a bow”.", to="Steve")
    assert k.vfg.said() == [["Still on: “a bow”.", "--to", "Steve"]]
