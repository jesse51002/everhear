"""The spec's FireStaff and Fireball load and run exactly as the spec writes them (the code blocks
in its "Ticks and subscriptions" and "Abilities and their config" sections), with only the import
line added; and engine/examples/ (the completed versions) deal what their damage formula says."""

import math
import os
import re

import pytest

from vfg_engine.io import AddModifier, Damage, GiveItem, RemoveModifier

SPEC = ("/var/home/jm/Documents/EverHear/Internal/workspace/pages/home/children/Specs/children/"
        "2026-10-12-Prototype_Game_Engine/content.yaml")

FIRE_STAFF = '''\
class FireStaff(Item):
    every = 0.5                          # tick while someone holds it

    class Config(ItemConfig):
        summary = "Right-click to cast Fireball. Holding it adds 20% speed."
        base = "carrot_on_a_stick"
        name = "Fire Staff"
        active_in = "mainhand"
        speed_bonus: float = Field(0.2, ge=0, le=1)

    def start(self, ctx):                # someone started holding it
        self.modifier("movement_speed", self.config.speed_bonus, "add_multiplied_total")
        self.listen(Use, "on_use")       # only this holder's uses

    def on_use(self, ctx, ev):
        ctx.user.cast("fireball")        # an ability, by name

    def stop(self, ctx, reason):         # let go, dropped, died, reloaded
        pass                             # its modifier and listens go by themselves
'''

FIREBALL = '''\
class Fireball(Ability):
    class Config(AbilityConfig):
        summary = "Burns for burn_s seconds, then explodes in a radius; both scale with power."
        burn_per_s_base: float = Field(1, ge=0, le=5)
        burn_s: float = Field(3, ge=0, le=10)
        explosion_base: float = Field(8, ge=0, le=30)
        radius: float = Field(3, ge=1, le=8)
        scale_per_point: float = 0.05
        charge_s = 0
        cooldown_s = 5

        def scale(self, ctx):
            return 1 + self.scale_per_point * ctx.user.power()

        def explosion(self, ctx):
            return self.explosion_base * self.scale(ctx)

        def damage(self, ctx):                 # required: the total, scale included
            return self.burn_per_s_base * self.scale(ctx) * self.burn_s + self.explosion(ctx)

        def duration_s(self, ctx):
            return self.burn_s + 1

    def on_cast(self, ctx, ev):
        self.explode(at=ctx.user.looking_at(), radius=self.config.radius,
                     damage=self.config.explosion(ctx))
'''


def _spec_python():
    with open(SPEC, encoding="utf-8") as f:
        text = f.read()
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    # the YAML block scalar indents every line by 8
    return [re.sub(r"(?m)^        ", "", b) for b in blocks]


@pytest.mark.skipif(not os.path.exists(SPEC), reason="the spec page is not on this machine")
def test_the_copies_here_are_the_spec_word_for_word():
    blocks = _spec_python()
    assert FIRE_STAFF in blocks
    assert FIREBALL in blocks


def test_the_spec_classes_load_as_written(rig):
    r = rig({"fire_staff": FIRE_STAFF, "fireball": FIREBALL})
    result = r.start()
    assert result["errors"] == []
    assert sorted(result["loaded"]) == ["fire_staff", "fireball"]
    cfg = r.rt.classes["fireball"].config
    assert (cfg.charge_s, cfg.cooldown_s) == (0.0, 5.0)        # unannotated, typed by the base
    assert r.rt.classes["fire_staff"].config.summary.startswith("Right-click")
    assert r.rt.classes["fireball"].damage == {0: 11.0, 10: 16.5, 20: 22.0}


def test_the_spec_classes_run_as_written(rig):
    r = rig({"fire_staff": FIRE_STAFF, "fireball": FIREBALL})
    r.start()
    r.io.join("Steve")
    r.io.join("Alex", pos=(0.0, 64.0, 6.0))
    assert r.op("give", player="Steve", item="fire_staff")["ok"]
    give = r.io.ops_of(GiveItem)[-1]
    assert (give.base, give.item_id, give.name, give.count) == ("minecraft:carrot_on_a_stick", "fire_staff", "Fire Staff", 1)
    r.tick()
    # holding it: the item's own modifier, by its id
    assert r.mods("Steve")[("minecraft:movement_speed", "everhear:item/fire_staff")] == (0.2, "add_multiplied_total")
    r.io.look_at("Steve", (0.0, 64.0, 6.0))
    r.io.use("Steve")
    r.tick()
    boom = [d for d in r.io.ops_of(Damage) if d.damage_type == "minecraft:explosion"]
    assert [(d.target, d.by) for d in boom] == [("Alex", "Steve")]
    assert math.isclose(boom[0].amount, 8 * 1.05)
    # the cooldown is the engine's: a second use within 5 s does nothing
    r.io.use("Steve")
    r.tick()
    assert len([d for d in r.io.ops_of(Damage) if d.damage_type == "minecraft:explosion"]) == 1
    # let go: the instance ends and its modifier goes
    r.io.unhold("Steve")
    r.tick(2)
    assert ("minecraft:movement_speed", "everhear:item/fire_staff") not in r.mods("Steve")
    assert any(isinstance(op, RemoveModifier) for op in r.io.sent)


def test_the_completed_examples_deal_their_formula(rig):
    r = rig(examples=True)
    assert r.start()["errors"] == []
    r.io.join("Steve")
    r.io.join("Alex", pos=(0.0, 64.0, 6.0))
    r.io.hold("Steve", "fire_staff")
    r.tick()
    r.io.look_at("Steve", (0.0, 64.0, 6.0))
    r.io.use("Steve")
    r.run_for(5)
    dealt = sum(d.amount for d in r.io.ops_of(Damage) if d.target == "Alex")
    declared = r.rt.classes["fireball"].config.damage(_Power1())
    assert math.isclose(dealt, declared)
    assert math.isclose(r.io.players["Alex"].health, 20 - declared)
    assert not any("damage drift" in m for m in r.lines())
    assert isinstance(r.io.ops_of(AddModifier)[0], AddModifier)


class _Power1:
    class user:
        @staticmethod
        def power():
            return 1
