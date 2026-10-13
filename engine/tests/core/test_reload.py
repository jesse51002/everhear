"""engine reload: every live instance switches to the new class at once, rebuilt from its state
(the game engine spec, "Live updates")."""

from vfg_engine.io import Command

GLOW = '''
class Glow(Mark):
    every = 0.5

    class Config(MarkConfig):
        summary = "Bigger, and says so."
        duration_s = 60
        damage = 0
        grow: float = Field(0.5, ge=0, le=4)

    class State(State):
        ticks: int = 0

    def start(self, ctx):
        self.modifier("scale", self.config.grow, "add_multiplied_total")
        self.after(30, "halfway")

    def halfway(self, ctx):
        self.command("say halfway")

    def tick(self, ctx):
        self.state.ticks += 1
        self.command("say %s %d" % ("{word}", self.state.ticks))
'''


def glow(word="old", grow="0.5", extra=""):
    return GLOW.replace("{word}", word).replace("Field(0.5", "Field(%s" % grow) + extra


def says(r):
    return [op.text for op in r.io.ops_of(Command) if op.text.startswith("say ")]


def _three(rig, **kw):
    r = rig({"glow": glow()}, **kw)
    assert r.start()["errors"] == []
    for n in ("Steve", "Alex", "Sam"):
        r.io.join(n)
    r.tick()
    for n in ("Steve", "Alex", "Sam"):
        assert r.op("apply", who=n, mark="glow")["ok"]
    r.run_for(1)
    return r


def test_every_live_instance_switches_and_keeps_its_state(rig):
    r = _three(rig)
    before = {rec.user: rec.state.ticks for rec in r.records("mark")}
    assert all(v >= 1 for v in before.values())
    r.write("glow", glow(word="new"))
    reply = r.op("reload")
    assert reply["ok"] and reply["switched"] == 3 and reply["stopped"] == []
    r.io.sent.clear()
    r.run_for(0.6)
    new = says(r)
    assert len(new) == 3 and all(s.startswith("say new ") for s in new)
    # the count carried on from each one's state
    for rec in r.records("mark"):
        assert rec.state.ticks > before[rec.user]


def test_a_config_change_restarts_with_the_new_values_keeping_state_and_end(rig):
    r = _three(rig)
    ends = {rec.user: rec.ends for rec in r.records("mark")}
    r.write("glow", glow(grow="1.0"))
    assert r.op("reload")["ok"]
    r.tick()
    for n in ("Steve", "Alex", "Sam"):
        assert r.mods(n)[("minecraft:scale", "everhear:mark/glow")] == (1.0, "add_multiplied_total")
    assert {rec.user: rec.ends for rec in r.records("mark")} == ends


def test_on_config_is_used_when_the_class_has_one(rig):
    r = _three(rig)
    r.write("glow", glow(grow="2.0", extra='''
    def on_config(self, ctx, old):
        self.command("say config was %g" % old.grow)
        self.modifier("scale", self.config.grow, "add_multiplied_total")
'''))
    assert r.op("reload")["ok"]
    r.tick()
    assert says(r).count("say config was 0.5") == 3
    assert r.mods("Steve")[("minecraft:scale", "everhear:mark/glow")] == (2.0, "add_multiplied_total")


def test_a_state_that_no_longer_fits_stops_that_instance_cleanly(rig):
    r = _three(rig)
    (steve,) = [rec for rec in r.records("mark") if rec.user == "Steve"]
    steve.state = steve.state.model_copy(update={"ticks": -5})
    r.write("glow", glow().replace("ticks: int = 0", "ticks: int = Field(0, ge=0)"))
    reply = r.op("reload")
    assert reply["switched"] == 2
    assert [s["instance"] for s in reply["stopped"]] == [steve.iid]
    assert "no longer fits" in reply["stopped"][0]["reason"]
    r.tick()
    assert ("minecraft:scale", "everhear:mark/glow") not in r.mods("Steve")       # cleaned up
    assert ("minecraft:scale", "everhear:mark/glow") in r.mods("Alex")


def test_a_field_added_to_the_state_takes_its_default(rig):
    r = _three(rig)
    r.write("glow", glow().replace("ticks: int = 0", "ticks: int = 0\n        colour: str = \"red\""))
    assert r.op("reload")["switched"] == 3
    assert all(rec.state.colour == "red" for rec in r.records("mark"))


def test_a_timer_naming_a_method_the_new_class_lacks_is_dropped_with_a_log_line(rig):
    r = _three(rig)
    r.write("glow", glow().replace("def halfway(self, ctx):\n        self.command(\"say halfway\")\n", ""))
    assert r.op("reload")["switched"] == 3
    assert all(not any(t.method == "halfway" for t in rec.timers) for rec in r.records("mark"))
    assert any("names 'halfway'" in m for m in r.lines())


def test_reload_imports_a_fresh_module_each_time(rig):
    r = _three(rig)
    first = r.rt.classes["glow"].module
    r.write("glow", glow(word="new"))
    r.op("reload")
    assert r.rt.classes["glow"].module != first


def test_a_class_whose_file_is_gone_is_unloaded_by_a_full_reload(rig):
    r = _three(rig)
    (r.dir / "glow.py").unlink()
    reply = r.op("reload")
    assert len(reply["stopped"]) == 3 and "glow" not in r.rt.classes
    r.tick()
    assert ("minecraft:scale", "everhear:mark/glow") not in r.mods("Steve")


def test_reloading_one_path(rig):
    r = _three(rig)
    path = r.write("glow", glow(word="one"))
    reply = r.op("reload", paths=[path])
    assert reply["ok"] and reply["loaded"] == ["glow"]
    assert r.op("reload", paths=[str(r.dir / "nothing.py")])["errors"][0]["check"] == "imports"


def test_a_reload_leaves_one_module_per_file(rig):
    import sys
    r = _three(rig)
    seen = [r.rt.classes["glow"].module]
    for word in ("a", "b", "c", "d"):
        r.write("glow", glow(word=word))
        assert r.op("reload")["ok"]
        seen.append(r.rt.classes["glow"].module)
    assert [m for m in seen if m in sys.modules] == [seen[-1]]
    assert r.rt.modules == {seen[-1]}


def test_a_module_a_class_still_runs_from_is_kept(rig):
    import sys
    two = glow() + '''

class Dim(Mark):
    class Config(MarkConfig):
        summary = "Smaller."
        duration_s = 60
        damage = 0
'''
    r = rig({"pair": two})
    assert r.start()["errors"] == []
    first = r.rt.classes["dim"].module
    r.write("pair", two.replace('summary = "Smaller."', 'summary = 5'))     # Dim fails, Glow passes
    reply = r.op("reload")
    assert reply["loaded"] == ["glow"] and reply["errors"][0]["class"] == "Dim"
    second = r.rt.classes["glow"].module
    assert r.rt.classes["dim"].module == first and first in sys.modules and second in sys.modules
    r.write("pair", two)
    assert r.op("reload")["ok"]
    assert first not in sys.modules and second not in sys.modules
    assert r.rt.modules == {r.rt.classes["dim"].module}


def test_close_takes_the_engines_modules_out(rig):
    import sys
    r = _three(rig)
    name = r.rt.classes["glow"].module
    r.engine.close()
    assert name not in sys.modules
