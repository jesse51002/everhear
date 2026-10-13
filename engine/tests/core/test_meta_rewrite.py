"""`engine meta` (required fields, damage at power 0/10/20, the class's own fields), and the
brain's Config rewrite: find, splice, the static checks, then the engine's reload checks
(the game engine spec, "Abilities and their config" and "One version for everyone")."""

import os

from vfg_engine import rewrite
from vfg_engine.io import Damage

from conftest import EXAMPLES

FIREBALL = os.path.join(EXAMPLES, "fireball.py")


def test_meta_rows(rig):
    r = rig(examples=True)
    r.start()
    rows = {row["id"]: row for row in r.op("meta")["rows"]}
    fb = rows["fireball"]
    assert list(fb) == ["id", "kind", "class", "summary", "required", "damage", "fields"]
    assert fb["required"]["damage"] == 11.55 and fb["required"]["duration_s"] == 4.0
    assert fb["required"]["charge_s"] == 0.0 and fb["required"]["cooldown_s"] == 5.0
    assert fb["damage"] == {"0": 11.0, "10": 16.5, "20": 22.0}
    assert fb["fields"] == {"burn_per_s_base": 1.0, "burn_s": 3.0, "explosion_base": 8.0, "radius": 3.0}
    assert rows["fire_staff"]["damage"] is None and rows["fire_staff"]["fields"] == {"speed_bonus": 0.2}
    assert rows["giant"]["required"]["on_death"] == "end"
    one = r.op("meta", id="giant")["rows"]
    assert [x["id"] for x in one] == ["giant"]
    assert r.op("meta", id="nope")["ok"] is False


def test_find_config_takes_the_class_as_written():
    source = open(FIREBALL).read()
    start, end = rewrite.find_config(source, "Fireball")
    lines = source.splitlines()
    assert lines[start - 1].strip() == "class Config(AbilityConfig):"
    assert lines[end - 1].strip() == "return self.burn_s + 1"


def test_splice_puts_the_new_config_in_exactly_that_place():
    source = open(FIREBALL).read()
    start, end = rewrite.find_config(source, "Fireball")
    old = "\n".join(source.splitlines()[start - 1:end])
    new_config = old.replace("Field(8, ge=0, le=30)", "Field(12, ge=0, le=30)  # brain: stronger")
    out = rewrite.splice(source, "Fireball", new_config)
    assert out == source.replace("Field(8, ge=0, le=30)", "Field(12, ge=0, le=30)  # brain: stronger")
    assert rewrite.static_checks(source, out, "Fireball") == []
    # dedented as the brain might send it: put back at the right indent
    import textwrap
    assert rewrite.splice(source, "Fireball", textwrap.dedent(new_config)) == out


def test_static_checks_refuse_a_dropped_field_and_a_change_outside():
    source = open(FIREBALL).read()
    start, end = rewrite.find_config(source, "Fireball")
    old = "\n".join(source.splitlines()[start - 1:end])
    dropped = rewrite.splice(source, "Fireball", old.replace("        radius: float = Field(3, ge=1, le=8)\n", ""))
    assert any("dropped radius" in p for p in rewrite.static_checks(source, dropped, "Fireball"))
    outside = source.replace("self.end()", "pass")
    assert any("outside" in p for p in rewrite.static_checks(source, outside, "Fireball"))
    assert any("parse" in p for p in rewrite.static_checks(source, source + "\n  def (", "Fireball"))


def test_a_rewrite_passes_the_reload_and_every_running_fireball_switches(rig, tmp_path):
    r = rig()
    source = open(FIREBALL).read()
    r.write("fireball", source, header=False)
    r.write("fire_staff", open(os.path.join(EXAMPLES, "fire_staff.py")).read(), header=False)
    r.start()
    for n, x in (("Steve", 0.0), ("Alex", 10.0)):
        r.io.join(n, pos=(x, 64.0, 0.0))
        r.op("grant", who=n, ability="fireball")
    config = r.op("config_get", **{"class": "fireball"})
    assert config["source"].startswith("    class Config(AbilityConfig):")
    new = rewrite.splice(source, "Fireball", config["source"].replace("Field(8, ge=0, le=30)", "Field(12, ge=0, le=30)"))
    assert rewrite.static_checks(source, new, "Fireball") == []
    (r.dir / "fireball.py").write_text(new)
    reply = r.op("reload")
    assert reply["ok"] and reply["switched"] == 2
    assert r.op("meta", id="fireball")["rows"][0]["fields"]["explosion_base"] == 12.0
    # out of bounds: refused by the engine, the old numbers stay
    bad = rewrite.splice(source, "Fireball", config["source"].replace("Field(8, ge=0, le=30)", "Field(80, ge=0, le=30)"))
    assert rewrite.static_checks(source, bad, "Fireball") == []          # the text is fine ...
    (r.dir / "fireball.py").write_text(bad)
    reply = r.op("reload")
    assert reply["ok"] is False and reply["errors"][0]["check"] == "bounds"   # ... the bounds are not
    assert r.rt.classes["fireball"].config.explosion_base == 12.0
    r.io.join("Sam", pos=(0.0, 64.0, 6.0))
    r.io.look_at("Steve", (0.0, 64.0, 6.0))
    r.tick()
    r.op("cast", player="Steve", phrase="x")      # nothing bound: unknown, nothing cast
    assert not r.io.ops_of(Damage)


# ---- a rewrite changes values, never code (it may come from any player, by voice) ---------------

def _fireball_config():
    source = open(FIREBALL).read()
    start, end = rewrite.find_config(source, "Fireball")
    return source, "\n".join(source.splitlines()[start - 1:end])


def _check(change):
    source, config = _fireball_config()
    new = rewrite.splice(source, "Fireball", change(config))
    return rewrite.static_checks(source, new, "Fireball")


def test_code_in_a_formula_body_is_refused():
    problems = _check(lambda c: c.replace("return self.burn_s + 1",
                                          "__import__('os').system('true'); return self.burn_s + 1"))
    assert problems and "formula duration_s changed" in problems[0]


def test_a_call_in_a_value_is_refused():
    assert any("explosion_base" in p and "not a plain value" in p for p in
               _check(lambda c: c.replace("Field(8, ge=0, le=30)", "Field(__import__('os').getpid(), ge=0, le=30)")))
    assert any("charge_s" in p for p in _check(lambda c: c.replace("charge_s = 0", "charge_s = int('0')")))
    assert any("radius" in p for p in
               _check(lambda c: c.replace("Field(3, ge=1, le=8)", "Field(3, ge=1, le=__import__('os').getpid())")))
    assert any("default_factory" not in p and "burn_s" in p for p in
               _check(lambda c: c.replace("Field(3, ge=0, le=10)", "Field(default_factory=print)")))


def test_an_import_or_a_statement_in_the_class_body_is_refused():
    problems = _check(lambda c: c.replace("        charge_s = 0", "        import os\n        charge_s = 0"))
    assert problems and "Import" in problems[0]
    assert any("Expr" in p for p in _check(lambda c: c.replace("        charge_s = 0",
                                                                "        print('hi')\n        charge_s = 0")))


def test_a_decorator_is_refused():
    assert any("decorators" in p for p in _check(lambda c: "    @print\n" + c))
    assert any("formula scale changed" in p for p in
               _check(lambda c: c.replace("        def scale(self, ctx):", "        @staticmethod\n        def scale(self, ctx):")))


def test_a_changed_type_is_refused():
    assert any("type of radius" in p for p in
               _check(lambda c: c.replace("radius: float =", "radius: __import__('os').getpid() =")))


def test_values_comments_and_layout_may_change():
    def change(c):
        c = c.replace("Field(8, ge=0, le=30)", "Field(\n            12.5, ge=0, le=30,   # the brain: stronger\n        )")
        c = c.replace("charge_s = 0", "charge_s = 0.5  # a short charge")
        return c.replace("scale_per_point: float = 0.05", "scale_per_point: float = -0.0")
    assert _check(change) == []


def test_undo_of_a_kept_version_passes():
    source, config = _fireball_config()
    new = rewrite.splice(source, "Fireball", config.replace("Field(8, ge=0, le=30)", "Field(12, ge=0, le=30)"))
    assert rewrite.static_checks(source, new, "Fireball") == []
    assert rewrite.static_checks(new, source, "Fireball") == []      # putting the old one back
    for name, cls in (("giant.py", "Giant"), ("shrunk.py", "Shrunk"), ("fire_staff.py", "FireStaff")):
        src = open(os.path.join(EXAMPLES, name)).read()
        assert rewrite.static_checks(src, src, cls) == []


def test_until_stopped_is_a_value():
    source = open(os.path.join(EXAMPLES, "giant.py")).read()
    new = source.replace("duration_s = 30", "duration_s = UNTIL_STOPPED")
    assert rewrite.static_checks(source, new, "Giant") == []
