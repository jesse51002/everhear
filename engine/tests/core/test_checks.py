"""Every reload check refuses what it should, names the check and the reason, and leaves the old
class running (the game engine spec, "Live updates", step 2, and the config table)."""

import pytest

GOOD = '''
class Zap(Ability):
    class Config(AbilityConfig):
        summary = "A zap."
        hit_base: float = Field(4, ge=0, le=20)
        scale_per_point: float = 0.1
        charge_s = 0
        cooldown_s = 1
        duration_s = 1

        def damage(self, ctx):
            return self.hit_base * (1 + self.scale_per_point * ctx.user.power())

    def start(self, ctx):
        self.every(1, "pulse")

    def pulse(self, ctx):
        pass

    def on_cast(self, ctx, ev):
        pass
'''

BAD = {
    "imports": [
        ("syntax", GOOD + "\n    def broken(:\n"),
        ("raises at import", "raise RuntimeError('nope')\n" + GOOD),
        ("imports something missing", "import no_such_module_here\n" + GOOD),
    ],
    "base": [
        ("no config of its own", '''
class Zap(Ability):
    def on_cast(self, ctx, ev):
        pass
'''),
        ("config on the wrong base", GOOD.replace("class Config(AbilityConfig):", "class Config(MarkConfig):")),
    ],
    "code": [
        ("while True", GOOD.replace("    def pulse(self, ctx):\n        pass",
                                    "    def pulse(self, ctx):\n        while True:\n            pass")),
        ("sleep", GOOD.replace("    def pulse(self, ctx):\n        pass",
                               "    def pulse(self, ctx):\n        import time\n        time.sleep(1)")),
        ("its own __init__", GOOD + "\n    def __init__(self):\n        pass\n"),
    ],
    "config": [
        ("cooldown_s missing", GOOD.replace("        cooldown_s = 1\n", "")),
        ("damage missing", GOOD.replace("        def damage(self, ctx):\n            return self.hit_base * (1 + self.scale_per_point * ctx.user.power())\n", "")),
        ("summary missing", GOOD.replace('        summary = "A zap."\n', "")),
    ],
    "bounds": [
        ("a field out of its bounds", GOOD.replace("Field(4, ge=0, le=20)", "Field(40, ge=0, le=20)")),
        ("a negative charge", GOOD.replace("charge_s = 0", "charge_s = -1")),
        ("a negative duration", GOOD.replace("duration_s = 1", "duration_s = -2")),
    ],
    "damage": [
        ("not a number", GOOD.replace("return self.hit_base * (1 + self.scale_per_point * ctx.user.power())",
                                      "return 'lots'")),
        ("does not scale", GOOD.replace("return self.hit_base * (1 + self.scale_per_point * ctx.user.power())",
                                        "return self.hit_base")),
        ("raises", GOOD.replace("return self.hit_base * (1 + self.scale_per_point * ctx.user.power())",
                                "return 1 / 0")),
    ],
}


def _loaded(rig):
    r = rig({"zap": GOOD})
    assert r.start()["errors"] == []
    r.io.join("Steve")
    assert r.op("grant", who="Steve", ability="zap")["ok"]
    old_cls = r.rt.classes["zap"].cls
    (rec,) = r.records("ability", "zap")
    return r, old_cls, rec


@pytest.mark.parametrize("check,what,source", [(c, w, s) for c, cases in BAD.items() for w, s in cases])
def test_a_bad_class_is_refused_and_the_old_one_keeps_running(rig, check, what, source):
    r, old_cls, rec = _loaded(rig)
    r.write("zap", source)
    reply = r.op("reload")
    assert reply["ok"] is False, what
    assert [e["check"] for e in reply["errors"]] == [check], (what, reply["errors"])
    assert reply["errors"][0]["error"]
    assert r.rt.classes["zap"].cls is old_cls, what
    assert rec.iid in r.rt.records and type(r.rt.objects[rec.iid]) is old_cls
    r.run_for(2)                               # and it still runs: its timer fires
    assert rec.iid in r.rt.records


def test_a_missing_required_field_is_named(rig):
    r, _, _ = _loaded(rig)
    r.write("zap", GOOD.replace("        cooldown_s = 1\n", "").replace("        charge_s = 0\n", ""))
    err = r.op("reload")["errors"][0]
    assert err["check"] == "config"
    assert "charge_s" in err["error"] and "cooldown_s" in err["error"]


def test_zero_is_a_value(rig):
    r = rig({"zap": GOOD.replace("cooldown_s = 1", "cooldown_s = 0").replace("hit_base: float = Field(4", "hit_base: float = Field(0")
             .replace("scale_per_point: float = 0.1", "scale_per_point: float = 0")})
    assert r.start()["errors"] == []


ITEM = '''
class Wand(Item):
    class Config(ItemConfig):
        summary = "A wand."
        base = "%s"
        name = "Wand"
        active_in = "mainhand"

    def start(self, ctx):
        self.listen(%s, "on_it")

    def on_it(self, ctx, ev):
        pass
'''


def test_an_item_whose_base_cannot_report_its_trigger_does_not_load(rig):
    r = rig({"wand": ITEM % ("carrot_on_a_stick", "Use")})
    assert r.start()["errors"] == []
    old = r.rt.classes["wand"].cls
    r.write("wand", ITEM % ("stick", "Use"))
    err = r.op("reload")["errors"][0]
    assert err["check"] == "triggers" and "minecraft:stick" in err["error"]
    assert r.rt.classes["wand"].cls is old


def test_an_item_on_a_base_that_reports_its_trigger_loads(rig):
    r = rig({"wand": ITEM % ("warped_fungus_on_a_stick", "Hit")})
    assert r.start()["errors"] == []


ACTOR = '''
class Brute(Actor):
    class Config(ActorConfig):
        summary = "A brute."
        entity_type = "%s"
        health = 40
        size = 1.5
        damage = 6
'''


def test_an_actor_on_an_entity_26_3_lacks_does_not_load(rig):
    r = rig({"brute": ACTOR % "minecraft:wither_skeleton"})
    assert r.start()["errors"] == []
    r.write("brute", ACTOR % "minecraft:dragonling")
    err = r.op("reload")["errors"][0]
    assert err["check"] == "config" and "dragonling" in err["error"]


def test_two_files_cannot_take_one_id(rig):
    r = rig({"zap": GOOD})
    assert r.start()["errors"] == []
    r.write("zap_again", GOOD)
    err = r.op("reload")["errors"][0]
    assert err["check"] == "base" and "already taken" in err["error"]


def test_a_slow_import_is_cut(rig):
    r = rig({"zap": GOOD})
    r.start()
    r.write("zap", "import time\n_t = time.perf_counter()\nwhile time.perf_counter() - _t < 5: pass\n" + GOOD)
    err = r.op("reload")["errors"][0]
    assert err["check"] == "imports" and "cut" in err["error"]


def test_a_state_that_cannot_be_made_from_its_defaults_is_refused(rig):
    r = rig({"zap": GOOD})
    r.start()
    old = r.rt.classes["zap"].cls
    r.write("zap", GOOD.replace("class Zap(Ability):", "class Zap(Ability):\n    class State(State):\n        target: str\n"))
    err = r.op("reload")["errors"][0]
    assert err["check"] == "state" and "target" in err["error"] and "default" in err["error"]
    assert r.rt.classes["zap"].cls is old


@pytest.mark.parametrize("cid", ["9lives", "_hidden"])
def test_an_id_must_start_with_a_letter(rig, cid):
    r = rig({"zap": GOOD.replace("class Zap(Ability):", "class Zap(Ability):\n    id = %r\n" % cid)})
    err = r.start()["errors"][0]
    assert err["check"] == "base" and "start with a letter" in err["error"]
