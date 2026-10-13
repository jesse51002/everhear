"""Desired against actual (the game engine spec, "The datapack" and "Potions and marks"): a failed
op is put right next tick, drift is found by reading back, an id is removed before it is added,
and nothing the engine did not add is ever removed."""

from vfg_engine.io import AddModifier, RemoveModifier

GIANT = ("minecraft:scale", "everhear:mark/giant")


def _giant(rig, **kw):
    r = rig(examples=True, **kw)
    r.start()
    r.io.join("Steve")
    r.tick()
    return r


def test_a_failed_add_is_tried_again_next_tick(rig):
    r = _giant(rig)
    r.io.fail_next(AddModifier, "rcon hiccup")
    r.op("apply", who="Steve", mark="giant")
    assert GIANT not in r.mods("Steve")
    r.tick()
    assert r.mods("Steve")[GIANT] == (1.0, "add_multiplied_total")


def test_a_failed_remove_is_tried_again_next_tick(rig):
    r = _giant(rig)
    r.op("apply", who="Steve", mark="giant")
    r.io.fail_next(RemoveModifier, "rcon hiccup")
    r.op("clear", who="Steve", mark="giant")
    assert GIANT in r.mods("Steve")
    r.tick()
    assert GIANT not in r.mods("Steve")


def test_drift_is_put_right_by_reading_back(rig):
    r = _giant(rig, check_every_s=1.0)
    r.op("apply", who="Steve", mark="giant")
    del r.io.players["Steve"].modifiers[GIANT]                    # someone ran /attribute ... remove
    r.run_for(1.2)
    assert r.mods("Steve")[GIANT] == (1.0, "add_multiplied_total")
    r.io.players["Steve"].modifiers[GIANT] = (5.0, "add_multiplied_total")   # or changed it
    r.run_for(1.2)
    assert r.mods("Steve")[GIANT] == (1.0, "add_multiplied_total")


def test_an_id_already_there_is_removed_then_added(rig):
    r = _giant(rig)
    r.io.players["Steve"].modifiers[GIANT] = (7.0, "add_multiplied_total")   # left by a crash
    r.op("apply", who="Steve", mark="giant")
    r.tick(2)
    assert r.mods("Steve")[GIANT] == (1.0, "add_multiplied_total")
    ops = [op for op in r.io.sent if isinstance(op, (AddModifier, RemoveModifier)) and op.id == GIANT[1]]
    kinds = [type(op).__name__ for op in ops]
    assert kinds.index("RemoveModifier") < len(kinds) - 1 and kinds[-1] == "AddModifier"


def test_a_changed_amount_is_removed_before_it_is_added_in_one_batch(rig):
    staff = '''
class Boots(Item):
    class Config(ItemConfig):
        summary = "Faster while sneaking."
        base = "carrot_on_a_stick"
        name = "Boots"
        active_in = "mainhand"

    def start(self, ctx):
        self.listen(Sneak, "on_sneak")
        self.modifier("movement_speed", 0.1, "add_multiplied_total")

    def on_sneak(self, ctx, ev):
        self.modifier("movement_speed", 0.3, "add_multiplied_total")
'''
    r = rig({"boots": staff})
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "boots")
    r.tick()
    r.io.sneak("Steve")
    r.tick()
    batch = [op for op in r.io.batches[-1] if isinstance(op, (AddModifier, RemoveModifier))]
    assert [type(op).__name__ for op in batch] == ["RemoveModifier", "AddModifier"]
    assert r.mods("Steve")[("minecraft:movement_speed", "everhear:item/boots")] == (0.3, "add_multiplied_total")


def test_nothing_the_engine_did_not_add_is_removed(rig):
    r = _giant(rig, check_every_s=0.5)
    theirs = {("minecraft:movement_speed", "minecraft:effect.speed"): (0.4, "add_multiplied_total"),
              ("minecraft:scale", "everhear:a"): (1.0, "add_multiplied_total")}
    r.io.players["Steve"].modifiers.update(theirs)
    r.op("apply", who="Steve", mark="giant")
    r.op("clear", who="Steve", mark="giant")
    r.run_for(2)
    for key, value in theirs.items():
        assert r.mods("Steve")[key] == value
    removed = {op.id for op in r.io.ops_of(RemoveModifier)}
    assert removed == {GIANT[1]}


def test_no_base_is_ever_set_and_no_potion_cleared(rig):
    from vfg_engine.io import Command
    r = _giant(rig)
    r.op("apply", who="Steve", mark="giant")
    r.op("apply", who="Steve", mark="shrunk")
    r.run_for(12)
    texts = [op.text for op in r.io.ops_of(Command)]
    assert not [t for t in texts if "base set" in t or "effect clear" in t]
