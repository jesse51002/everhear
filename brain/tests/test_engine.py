"""The game engine in the brain: its part of THE WORLD NOW read in one call, its bindings among the
incantations, the Config read (read_configs) and its rewrite (engine_config) gated on it, the engine
actions run through `vfg action run`, and the engine not running a "did not run"."""

import json

from brain.act import CHANGED_NOTHING, DID_NOT_RUN, LANDED, READ, REFUSED
from brain.call import step_type
from brain.vfg import VfgResult
from brain.world import Seen, World, incantation_lines

from conftest import Tick, act, engine_catalog, ran, step

SEEN = Seen(places="SAVED PLACES: none", known=set())
CLASSES = [
    {"id": "fireball", "kind": "ability", "file": "/opt/vfg/made/main/content/fireball.py",
     "summary": "Burns for burn_s seconds, then explodes in a radius; both scale with power."},
    {"id": "horde", "kind": "scenario", "file": "/opt/vfg/engine/library/horde.py", "summary": "Waves of zombies.",
     "args": [{"name": "count", "type": "int", "default": 10, "bounds": {"ge": 1, "le": 100},
               "description": "how many"}]}]
INSTANCES = [{"instance": "ability:fireball#1", "kind": "ability", "id": "fireball", "user": "Steve", "started": 1.0},
             {"instance": "scenario:horde#5", "kind": "scenario", "id": "horde", "user": None, "started": 1.0}]
ENGINE = ("ENGINE (every class the game engine has, one version for everyone: a scenario is started, an ability "
          "granted, an item given, a mark applied; under each scenario and mark its args, each by its name):\n"
          "SCENARIOS:\n"
          "- horde: Waves of zombies.\n"
          "    args.count: int 1..100, default 10: how many\n"
          "ABILITIES:\n"
          "- fireball: Burns for burn_s seconds, then explodes in a radius; both scale with power.")
CONFIG = ("made/main/content/fireball.py, lines 8 to 17: the Config of Fireball, as written\n"
          "    class Config(AbilityConfig):\n"
          "        # the blast\n"
          "        explosion_base: float = Field(8, ge=0, le=30)\n"
          "        radius: float = Field(3, ge=1, le=8)")
NEW = ("    class Config(AbilityConfig):\n"
       "        # the blast\n"
       "        explosion_base: float = Field(12, ge=0, le=30)\n"
       "        radius: float = Field(3, ge=1, le=8)\n")


def answered(obj, code=0) -> VfgResult:
    return VfgResult(code, json.dumps(obj) + "\n" if obj is not None else "", "")


def answer(k, *prefix: str, now) -> None:
    """FakeVfg's answer to `prefix` from now on, in place of the one it had."""
    k.vfg.rules = [r for r in k.vfg.rules if r[0] != prefix]
    k.vfg.on(*prefix, answer=now)


def test_an_engine_binding_shows_the_ability_it_casts_or_the_scenario_it_starts():
    shown = incantation_lines([
        {"player": "Steve", "phrase": "fire ball", "ability": "fireball", "scenario": None, "args": {},
         "cooldown_s": 5.0, "aliases": ["fireball"], "everyday_aliases": []},
        {"player": "Alex", "phrase": "horde", "ability": None, "scenario": "horde", "args": {"count": 4}}])
    assert shown.splitlines()[1:] == ['- "fire ball" for Steve: casts fireball',
                                      '- "horde" for Alex: starts horde {"count":4}']


async def test_the_world_reads_the_engine_in_one_call_shared_while_young_and_says_when_it_is_down(kit):
    k = kit()
    tick = Tick(100.0)
    world = World(k.vfg, clock=tick)
    overview = {"ok": True, "classes": CLASSES, "instances": INSTANCES, "places": [], "incantations": []}
    k.vfg.on("engine", "overview", answer=answered(overview))
    seen = await world.seen()
    assert seen.engine == ENGINE and seen.show().endswith(ENGINE)
    assert seen.running.splitlines()[1:] == ["- scenario:horde#5: scenario horde, for the world",
                                             "- ability:fireball#1: ability fireball, granted to Steve"]
    assert k.vfg.of("engine") == [["engine", "overview", "--json"]]   # every part: one SSH call
    tick.t += 1.5
    await world.seen()
    assert len(k.vfg.of("engine")) == 1              # young enough (as the state): not read again
    world.engine_changed()                           # an engine action ran
    answer(k, "engine", "overview", now=VfgResult(6, "", "vfg: the engine is not running: no control socket"))
    assert (await world.seen()).engine == ("ENGINE: not running (vfg-engine is down): nothing the engine has can be "
                                           "started, granted or bound now")
    tick.t += 11
    answer(k, "engine", "overview", now=VfgResult(4, "", "vfg: EC2 did not answer within 40 s"))
    seen = await world.seen()          # unread: what was read before it went down is gone, and it says why
    assert seen.engine == "ENGINE: unknown (it could not be read: vfg: EC2 did not answer within 40 s)"
    assert (await world.glance()).engine.startswith("ENGINE: unknown")
    tick.t += 11                                     # the engine gave its classes, refused its instances
    half = {"ok": False, "classes": CLASSES, "places": [], "incantations": [],
            "error": "instances: the engine failed on instances"}
    answer(k, "engine", "overview", now=VfgResult(1, json.dumps(half) + "\n", "vfg: instances: the engine failed"))
    seen = await world.seen()
    assert seen.engine == ENGINE
    assert seen.running == "RUNNING NOW: unknown (it could not be read: instances: the engine failed on instances)"
    await world.close()


async def test_read_configs_reads_it_as_written_and_shows_it_whole(kit):
    k = kit(cat=engine_catalog())
    k.vfg.on("engine", "config", "get", answer=VfgResult(0, CONFIG + "\n", ""))
    said = step(end="next")
    said["read_configs"] = ["fireball"]
    got = await k.acts.run(step_type(k.cat).model_validate_json(json.dumps(said)), n=1, player="Steve", seen=SEEN,
                           refs=k.brain.refs, ref="#9 s1")
    [r] = got.reads
    assert (r.state, r.config_of, r.said) == (READ, "fireball", CONFIG)
    assert k.vfg.of("engine") == [["engine", "config", "get", "fireball"]]
    assert "- read fireball's Config\n  READ:\n" + CONFIG in got.show()   # every line, as written
    answer(k, "engine", "config", "get", now=VfgResult(6, "", "vfg: the engine is not running"))
    got = await k.acts.run(step_type(k.cat).model_validate_json(json.dumps(said)), n=2, player="Steve", seen=SEEN,
                           refs=k.brain.refs, ref="#9 s2")
    assert (got.reads[0].state, got.reads[0].said) == (REFUSED, "the engine is not running: nothing read")


def test_a_class_name_to_read_follows_the_engines_id_rule():
    from brain.call import CLASS_NAME
    for x in ("fireball", "fire_staff", "a", "a" + "b" * 63, "x9_"):
        assert CLASS_NAME.match(x), x
    for x in ("", "Fireball", "fire-staff", "9lives", "_x", "a" + "b" * 64, "fire ball", "fireball\n"):
        assert not CLASS_NAME.match(x), x


def test_read_configs_go_with_end_next_and_the_last_step_drops_them():
    Step = step_type(engine_catalog())
    said = step(end="next")
    said["read_configs"] = ["fireball"]
    assert Step.model_validate_json(json.dumps(said)).read_configs == ["fireball"]   # a read alone is a next step
    said["read_configs"] = ["Fire Ball"]
    try:
        Step.model_validate_json(json.dumps(said))
        raise AssertionError("a name that is no class's went through")
    except ValueError as e:
        assert "a class's name as ENGINE shows it" in str(e)
    replied = dict(step(reply="hi"), read_configs=["fireball"])
    assert Step.model_validate_json(json.dumps(replied)).read_configs == []           # dropped: it would come to nothing
    last = step_type(engine_catalog(), last=True)                                   # the last step reads nothing:
    read = last.model_validate_json(json.dumps(dict(step(reply="hi"), read_configs=["fireball"])))
    assert (read.read_configs, read.reply) == ([], "hi")                                # dropped, its ending kept


async def test_a_rewrite_runs_only_after_its_config_was_read_in_an_earlier_step(kit):
    k = kit(cat=engine_catalog())
    k.vfg.on("engine", "config", "get", answer=VfgResult(0, CONFIG + "\n", ""))
    rewrite = act("engine_config", "fireball's blast 8 to 12, for everyone", {"class_id": "fireball", "config": NEW})
    Step = step_type(k.cat)
    first = await k.acts.run(Step.model_validate_json(json.dumps(step(rewrite, reply="harder now"))), n=1,
                             player="Steve", seen=SEEN, refs=k.brain.refs, ref="#9 s1")
    [r] = first.actions
    assert r.state == REFUSED and r.said.startswith("refused before it ran: read fireball's Config first")
    assert k.vfg.of("action") == [] and not first.landed
    reading = dict(step(end="next"), read_configs=["fireball"])
    read = await k.acts.run(Step.model_validate_json(json.dumps(reading)), n=2, player="Steve", seen=SEEN,
                            refs=k.brain.refs, ref="#9 s2", earlier=[first])
    k.vfg.on("action", "run", "engine_config", answer=ran(True, "fireball's new Config is in, and reloaded for "
                                                          "everyone", changed=["fireball"]))
    third = await k.acts.run(Step.model_validate_json(json.dumps(step(rewrite, reply="harder now"))), n=3,
                             player="Steve", seen=SEEN, refs=k.brain.refs, ref="#9 s3", earlier=[first, read])
    assert [x.state for x in third.actions] == [LANDED] and third.landed
    [argv] = k.vfg.of("action", "run")
    assert argv == ["action", "run", "engine_config", "class_id=fireball", "config=" + NEW]
    answer(k, "action", "run", "engine_config", now=ran(True, "fireball's Config is already as written: nothing "
                                                        "changed", changed=[]))
    again = await k.acts.run(Step.model_validate_json(json.dumps(step(rewrite, reply="harder now"))), n=4,
                             player="Steve", seen=SEEN, refs=k.brain.refs, ref="#9 s4", earlier=[first, read])
    assert again.actions[0].state == CHANGED_NOTHING


async def test_engine_actions_run_as_actions_and_the_engine_down_did_not_run(kit):
    k = kit(cat=engine_catalog())
    k.vfg.on("action", "run", "engine_grant", answer=ran(True, "granted fireball to Steve"))
    k.world.engine_at = 50.0
    grant = act("engine_grant", "give Steve the fireball ability", {"player": "Steve", "ability": "fireball"})
    got = await k.acts.run(step_type(k.cat).model_validate_json(json.dumps(step(grant, end="next"))), n=1,
                           player="Steve", seen=SEEN, refs=k.brain.refs, ref="#9 s1")
    assert got.actions[0].state == LANDED
    assert k.vfg.of("action", "run") == [["action", "run", "engine_grant", "player=Steve", "ability=fireball"]]
    assert k.world.engine_at == float("-inf")          # the next loop reads the engine again
    line = {"ok": False, "action": "engine_give", "running": False, "summary": "", "facts": {},
            "notes": [], "error": "the engine is not running: no control socket at /opt/vfg/engine/engine.sock"}
    k.vfg.on("action", "run", "engine_give", answer=VfgResult(6, json.dumps(line) + "\n", ""))
    give = act("engine_give", "give Alex a fire staff", {"player": "Alex", "item": "fire_staff"})
    got = await k.acts.run(step_type(k.cat).model_validate_json(json.dumps(step(give, reply="here"))), n=1,
                           player="Alex", seen=SEEN, refs=k.brain.refs, ref="#9 s1")
    assert (got.actions[0].state, got.actions[0].said) == (DID_NOT_RUN, line["error"])
    refused = dict(line, error="no ability lightning")
    answer(k, "action", "run", "engine_grant", now=VfgResult(1, json.dumps(refused) + "\n", ""))
    zap = act("engine_grant", "give Steve lightning", {"player": "Steve", "ability": "lightning"})
    got = await k.acts.run(step_type(k.cat).model_validate_json(json.dumps(step(zap, reply="zap"))), n=1,
                           player="Steve", seen=SEEN, refs=k.brain.refs, ref="#9 s1")
    assert (got.actions[0].state, got.actions[0].said) == (REFUSED, "no ability lightning")


async def test_a_loop_reads_the_config_then_rewrites_it_and_replies(kit):
    reading = dict(step(end="next"), read_configs=["fireball"])
    rewrite = step(act("engine_config", "fireball's blast 8 to 12", {"class_id": "fireball", "config": NEW}),
                   reply="fireball hits harder now, for everyone", record="fireball's explosion_base 8 -> 12")
    k = kit(cat=engine_catalog(), Step=[reading, rewrite])
    k.vfg.on("engine", "config", "get", answer=VfgResult(0, CONFIG + "\n", ""))
    k.vfg.on("action", "run", "engine_config", answer=ran(True, "fireball's new Config is in", changed=["fireball"]))
    assert k.brain.feed("LINE#5 18:29:58 Steve voice action_oriented 0.9 - | make my fireball's blast 12") == "started"
    await k.settle()
    second = k.ask.of("Step")[1]["cached"][-1][0]
    assert "read fireball's Config\n  READ:\n" + CONFIG in second
    assert k.vfg.said() == [["fireball hits harder now, for everyone"]]
    assert ("action -", "fireball's explosion_base 8 -> 12") in k.vfg.rows("action")
