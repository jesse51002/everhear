"""Args (DESIGN2 §1): a Scenario's or Mark's typed args, checked at start and apply, shown in
`classes` and `meta`; a scenario's result and `start`'s wait; a scenario starting another; and a
phrase bound to a scenario with fixed args, "caster" filled with whoever spoke."""

import pytest

from vfg_engine import args as argtypes
from vfg_engine.io import Command

METEOR = '''
class Meteor(Scenario):
    class Config(ScenarioConfig):
        summary = "A meteor falls on a place."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        at: Place = Field("", description="where it falls: a saved place, x y z, or a player")
        count: int = Field(3, ge=1, le=20, description="how many rocks")
        who: Players = Field("@a", description="who sees the warning")
        rock: BlockMix = Field("70% stone 30% magma_block", description="what the rocks are")
        mob: EntityType = Field("zombie", description="what climbs out")
        loot: ItemId = Field("diamond", description="what it drops")
        after: Seconds = Field(2, le=60, description="seconds before it lands")
        rules: ReplaceRules = Field("snow > air", description="what it melts")
        then: Command = Field("", description="a command run when it lands")
        caller: Player = Field("", description="who called it")
        """unused"""

    def start(self, ctx):
        a = self.args
        self.command("say at %s %s %d %s %s %s" % (a.at.dim, list(a.at.pos), a.count, a.mob, a.loot,
                                                     ",".join(players(ctx, a.who))))
        self.after(a.after, "land")

    def land(self, ctx):
        self.result("landed", count=self.args.count, at=list(self.args.at.pos))
        self.end()


class Rounds(Scenario):
    class Config(ScenarioConfig):
        summary = "Starts meteors it owns."
        duration_s = UNTIL_STOPPED

    def start(self, ctx):
        self.start_scenario("meteor", at="0 70 0", after=60)


class Brand(Mark):
    class Config(MarkConfig):
        summary = "A coloured brand."
        duration_s = 30
        damage = 0

    class Args(Args):
        colour: str = Field("red", description="its colour")

    def start(self, ctx):
        self.command("say brand " + self.args.colour)
'''


def _rig(rig):
    r = rig({"meteor": METEOR})
    assert r.start()["errors"] == []
    r.io.join("Steve", pos=(10.0, 64.0, 20.0))
    r.io.join("Alex")
    r.tick()
    return r


def says(r):
    return [op.text for op in r.io.ops_of(Command) if op.text.startswith("say ")]


def test_args_are_checked_and_resolved_at_start(rig):
    r = _rig(rig)
    reply = r.op("start", scenario="meteor", args={"at": "Steve", "count": 5, "mob": "minecraft:husk"})
    assert reply["ok"]
    assert says(r) == ["say at minecraft:overworld [10.0, 64.0, 20.0] 5 minecraft:husk minecraft:diamond Steve,Alex"]
    rec = r.rt.records[reply["instance"]]
    assert rec.args["at"] == {"dim": "minecraft:overworld", "pos": [10.0, 64.0, 20.0], "yaw": 0.0, "name": "Steve"}


@pytest.mark.parametrize("given,words", [
    ({"count": 50}, "count: Input should be less than or equal to 20"),
    ({"at": "nowhere_special"}, "is not a saved place"),
    ({"mob": "dragonling"}, "dragonling is not an entity type"),
    ({"loot": "magic_beans"}, "magic_beans is not an item"),
    ({"rock": "70% stone 30% fluff"}, "fluff is not a block"),
    ({"rules": "snow air"}, "is not a rule"),
    ({"then": "/op Steve"}, "op is the server owner's"),
    ({"caller": "Notch"}, "Notch is not online"),
    ({"speed": 3}, "speed: not an arg of this"),
])
def test_bad_args_are_refused_in_words(rig, given, words):
    r = _rig(rig)
    reply = r.op("start", scenario="meteor", args=dict({"at": "1 64 1"}, **given))
    assert reply["ok"] is False and words in reply["error"], reply
    assert not r.records("scenario")


def test_the_schema_is_in_classes_and_meta(rig):
    r = _rig(rig)
    rows = {c["id"]: c for c in r.op("classes")["classes"]}
    args = {a["name"]: a for a in rows["meteor"]["args"]}
    assert args["count"] == {"name": "count", "type": "whole number", "default": 3, "bounds": {"ge": 1, "le": 20},
                             "description": "how many rocks"}
    assert args["at"]["type"] == "place" and args["who"]["type"] == "players" and args["rock"]["type"] == "block mix"
    assert args["after"]["bounds"] == {"ge": 0, "le": 60}
    assert rows["rounds"]["args"] == [] and rows["brand"]["args"][0]["name"] == "colour"
    assert {a["name"] for a in r.op("meta", id="meteor")["rows"][0]["args"]} == set(args)


def test_a_list_arg_names_what_it_holds():
    # a list of commands reads as one in the schema, so the brain finds a scenario's commands
    # (brain/brain/actions.py commands_in) and gives a refused one its reference
    class Run(argtypes.Args):
        commands: list[argtypes.Command] = argtypes.Field([], description="the commands")
        names: list[str] = argtypes.Field([], description="names")
    kinds = {a["name"]: a["type"] for a in argtypes.schema(Run)}
    assert kinds == {"commands": "list of command", "names": "list of text"}


def test_start_waits_for_the_result(rig):
    r = _rig(rig)
    fut = r.engine.submit("start", {"scenario": "meteor", "args": {"at": "0 64 0", "after": 1}, "wait_s": 5})
    r.tick()
    assert not fut.done()                                   # waiting, not answered with an error
    r.run_for(1.2)
    reply = fut.result(timeout=0)
    assert reply["ok"] and reply["ended"] == "ended"
    assert reply["result"] == {"summary": "landed", "facts": {"count": 3, "at": [0.0, 64.0, 0.0]}}


def test_start_answers_still_running_after_its_wait(rig):
    r = _rig(rig)
    fut = r.engine.submit("start", {"scenario": "meteor", "args": {"at": "0 64 0", "after": 30}, "wait_s": 1})
    r.run_for(1.3)
    reply = fut.result(timeout=0)
    assert reply["ok"] and reply["running"] is True and reply["note"] == "still running"
    assert reply["result"] is None
    assert r.op("start", scenario="meteor", wait_s=31)["ok"] is False


EARLY = '''
class Hut(Scenario):
    class Config(ScenarioConfig):
        summary = "Builds a hut, says so, and stays a while."
        duration_s = UNTIL_STOPPED

    def start(self, ctx):
        self.after(0.5, "built")

    def built(self, ctx):
        self.result("built", blocks=12)
'''


def test_start_answers_as_soon_as_a_result_comes_and_it_goes_on(rig):
    r = rig({"hut": EARLY})
    r.start()
    fut = r.engine.submit("start", {"scenario": "hut", "wait_s": 20})
    r.run_for(0.4)
    assert not fut.done()
    r.run_for(0.3)
    reply = fut.result(timeout=0)
    assert reply["running"] is True and reply["result"] == {"summary": "built", "facts": {"blocks": 12}}
    assert "note" not in reply and r.records("scenario")              # it goes on


def test_a_scenario_started_by_another_is_owned_by_it(rig):
    r = _rig(rig)
    owner = r.op("start", scenario="rounds")["instance"]
    r.tick()
    (child,) = [rec for rec in r.records("scenario", "meteor")]
    assert child.owner == owner
    r.op("stop", instance=owner)
    assert not r.records("scenario")


def test_a_mark_takes_args(rig):
    r = _rig(rig)
    assert r.op("apply", who="Steve", mark="brand", args={"colour": "blue"})["ok"]
    assert "say brand blue" in says(r)
    assert "colour: not" not in r.op("apply", who="Alex", mark="brand", args={"shade": 1})["error"]


def test_a_phrase_bound_to_a_scenario_starts_it_at_the_caster(rig):
    r = _rig(rig)
    reply = r.op("bind", player="Steve", phrase="meteor", scenario="meteor",
                 args={"at": "caster", "count": 2, "after": 30})
    assert reply["ok"] and reply["entry"]["scenario"] == "meteor" and reply["entry"]["ability"] is None
    assert reply["entry"]["cooldown_s"] == 3.0 and reply["entry"]["args"]["at"] == "caster"
    cast = r.op("cast", player="Steve", phrase="Meteor!", cast_id="m1")
    assert cast["status"] == "fired" and cast["instance"].startswith("scenario:meteor#")
    assert "say at minecraft:overworld [10.0, 64.0, 20.0] 2" in says(r)[-1]
    assert r.op("cast", player="Steve", phrase="meteor", cast_id="m2")["status"] == "cooldown"


def test_caster_fills_only_a_player_or_place(rig):
    r = _rig(rig)
    reply = r.op("bind", player="Steve", phrase="meteor", scenario="meteor", args={"count": "caster"})
    assert reply["ok"] is False and "caster" in reply["error"]
    assert r.op("bind", player="Steve", phrase="x", scenario="meteor", args={"count": 99})["ok"] is False


ITEM_WITH_ARGS = '''
class Wand(Item):
    class Config(ItemConfig):
        summary = "A wand."
        base = "carrot_on_a_stick"
        name = "Wand"
        active_in = "mainhand"

    class Args(Args):
        power: int = Field(1, description="how strong")
'''


@pytest.mark.parametrize("source,words", [
    (ITEM_WITH_ARGS, "only a Scenario or a Mark takes args"),
    (METEOR.replace('count: int = Field(3, ge=1, le=20, description="how many rocks")', "count: int"), "count has no default"),
    (METEOR.replace(', description="how many rocks"', ""), "count has no description"),
    (METEOR.replace('Field("zombie", description', 'Field("dragonling", description'), "defaults do not fit"),
])
def test_bad_args_classes_are_refused_at_reload(rig, source, words):
    r = rig({"thing": source})
    errors = r.start()["errors"]
    assert errors and errors[0]["check"] in ("args", "base") and words in errors[0]["error"], errors


def test_mix_and_rules_are_read_as_written():
    assert argtypes.mix("70% stone 30% andesite") == [("minecraft:stone", 70.0), ("minecraft:andesite", 30.0)]
    rules = argtypes.rules("snow > air; solid > 70% lava 30% keep")
    assert rules[0].froms == ("minecraft:snow",) and rules[0].to == (("minecraft:air", 1.0),)
    assert rules[1].froms == argtypes.SOLID and rules[1].to == (("minecraft:lava", 70.0), ("keep", 30.0))
    assert argtypes.pick(rules[1].to, "a 1 2 3") == argtypes.pick(rules[1].to, "a 1 2 3")


SHAPES = '''
class Herd(Scenario):
    class Config(ScenarioConfig):
        summary = "Spawns a herd."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        mobs: list[EntityType] = Field(["cow"], min_length=1, max_length=20, description="what to spawn")
        name: str = Field("herd", max_length=32, pattern="^[a-z_]+$", description="its name")
'''


def test_the_schema_shows_list_items_lengths_and_patterns(rig):
    r = rig({"herd": SHAPES})
    r.start()
    (row,) = [c for c in r.op("classes")["classes"] if c["id"] == "herd"]
    mobs, name = row["args"]
    assert mobs["type"] == "list of entity type" and mobs["bounds"] == {"min_length": 1, "max_length": 20}
    assert name["type"] == "text" and name["bounds"] == {"max_length": 32, "pattern": "^[a-z_]+$"}
    assert r.op("meta", id="herd")["rows"][0]["args"] == row["args"]
    assert "mobs" in r.op("start", scenario="herd", args={"mobs": []})["error"]


def test_the_result_op_reads_a_result_after_its_wait(rig):
    r = _rig(rig)
    iid = r.op("start", scenario="meteor", args={"at": "0 64 0", "after": 1})["instance"]
    assert r.op("result", instance=iid) == {"ok": True, "instance": iid, "running": True, "result": None}
    r.run_for(1.2)
    got = r.op("result", instance=iid)
    assert got["ended"] == "ended" and got["result"] == {"summary": "landed", "facts": {"count": 3, "at": [0.0, 64.0, 0.0]}}
    assert r.op("result", instance="scenario:nope#9")["ok"] is False
    r.clock.t += 601
    assert "no result kept" in r.op("result", instance=iid)["error"]


STORM = '''
class Storm(Scenario):
    class Config(ScenarioConfig):
        summary = "A storm round its caster."
        duration_s = 1

    class Args(Args):
        who: Player = Field("caster", description="whose storm it is: bound as caster, so whoever says it")
        near: Place = Field("0 64 0", description="where it gathers")
        size: int = Field(4, ge=1, le=10, description="how far it reaches")

    def start(self, ctx):
        self.command("say storm %s %s %d" % (self.args.who, list(self.args.near.pos), self.args.size))
'''


def test_a_caster_arg_whose_default_is_caster_binds_and_fills_at_the_cast(rig):
    # A Player or Place arg given "caster" in a binding is not checked against who is online when it
    # is bound, its default "caster" included: it is filled with whoever speaks, at each cast.
    r = rig({"storm": STORM})
    assert r.start()["errors"] == []
    r.io.join("Steve", pos=(1.0, 64.0, 2.0))
    r.tick()
    reply = r.op("bind", player="Steve", phrase="storm", scenario="storm", args={"who": "caster", "near": "caster"})
    assert reply["ok"], reply
    cast = r.op("cast", player="Steve", phrase="storm", cast_id="s1")
    assert cast["status"] == "fired", cast
    assert "say storm Steve [1.0, 64.0, 2.0] 4" in says(r)[-1]
    # one that leaves the Player arg out gets its default, "caster", filled the same way
    assert r.op("bind", player="Steve", phrase="small storm", scenario="storm", args={"size": 2})["ok"]
    r.tick(40)
    assert r.op("cast", player="Steve", phrase="small storm", cast_id="s2")["status"] == "fired"
    assert "say storm Steve [0.0, 64.0, 0.0] 2" in says(r)[-1]
