"""Items: one instance per holder; a give built from the class; the wrong base refused (the game
engine spec, "Items")."""

from vfg_engine.io import GiveItem, TakeItem

ID = ("minecraft:movement_speed", "everhear:item/fire_staff")

RING = '''
class Ring(Item):
    class Config(ItemConfig):
        summary = "Worn anywhere on you: a little armour."
        base = "warped_fungus_on_a_stick"
        name = "Ring"
        active_in = "inventory"

    def start(self, ctx):
        self.modifier("armor", 2, "add_value")
'''


def test_a_give_is_built_from_the_class(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.tick()
    reply = r.op("give", player="Steve", item="fire_staff")
    assert reply == {"ok": True, "item": "fire_staff", "base": "minecraft:carrot_on_a_stick"}
    (give,) = r.io.ops_of(GiveItem)
    assert give == GiveItem(player="Steve", base="minecraft:carrot_on_a_stick", item_id="fire_staff",
                            name="Fire Staff", components={}, count=1)


def test_a_give_on_the_wrong_base_is_refused_naming_the_class_base(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.tick()
    reply = r.op("give", player="Steve", item="fire_staff", base="stick")
    assert reply["ok"] is False
    assert "minecraft:carrot_on_a_stick" in reply["error"] and "minecraft:stick" in reply["error"]
    assert not r.io.ops_of(GiveItem)


def test_a_give_to_nobody_says_what_minecraft_said(rig):
    r = rig(examples=True)
    r.start()
    reply = r.op("give", player="Nobody", item="fire_staff")
    assert reply == {"ok": False, "error": "no player"}


def test_each_holder_has_their_own_instance_and_a_throw_moves_it(rig):
    r = rig(examples=True)
    r.start()
    for n in ("Steve", "Alex", "Sam"):
        r.io.join(n)
    r.io.hold("Steve", "fire_staff")
    r.io.hold("Alex", "fire_staff")
    r.tick()
    holders = sorted(rec.user for rec in r.records("item"))
    assert holders == ["Alex", "Steve"]
    assert ID in r.mods("Steve") and ID in r.mods("Alex") and ID not in r.mods("Sam")
    steve_iid = next(rec.iid for rec in r.records("item") if rec.user == "Steve")

    r.io.throw("Steve", "Sam")
    r.tick()
    assert sorted(rec.user for rec in r.records("item")) == ["Alex", "Sam"]
    assert steve_iid not in r.rt.records
    r.tick()
    assert ID not in r.mods("Steve") and ID in r.mods("Sam")
    assert any("unheld" in m and steve_iid in m for m in r.lines())


def test_each_holder_casts_with_their_own_cooldown(rig):
    from vfg_engine.io import Damage
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.join("Alex", pos=(10.0, 64.0, 0.0))
    r.io.hold("Steve", "fire_staff")
    r.io.hold("Alex", "fire_staff")
    r.tick()
    r.io.look_at("Steve", (0.0, 64.0, 50.0))
    r.io.look_at("Alex", (10.0, 64.0, 50.0))
    r.io.use("Steve")
    r.io.use("Alex")
    r.tick()
    assert sorted(rec.user for rec in r.records("ability", "fireball")) == ["Alex", "Steve"]
    assert all(rec.runs for rec in r.records("ability", "fireball"))
    assert not r.io.ops_of(Damage)


def test_where_an_item_counts(rig):
    r = rig({"ring": RING}, examples=True)
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "fire_staff", slot="offhand")      # the staff counts in the main hand only
    r.io.players["Steve"].inventory.append("ring")        # the ring counts anywhere
    r.tick(2)
    assert [rec.cid for rec in r.records("item")] == ["ring"]
    assert r.mods("Steve")[("minecraft:armor", "everhear:item/ring")] == (2.0, "add_value")
    r.io.players["Steve"].inventory.clear()
    r.tick(2)
    assert not r.records("item") and ("minecraft:armor", "everhear:item/ring") not in r.mods("Steve")


def test_a_use_of_another_item_is_not_this_ones(rig):
    r = rig({"ring": RING}, examples=True)
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "ring")
    r.io.players["Steve"].slots["offhand"] = "fire_staff"
    r.tick()
    r.io.use("Steve")                                     # a use of the ring
    r.tick()
    assert not r.records("ability")                       # the staff (not held where it counts) did nothing


def test_take(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "fire_staff")
    r.tick()
    assert r.op("take", player="Steve", item="fire_staff")["ok"]
    assert r.io.ops_of(TakeItem) and "mainhand" not in r.io.players["Steve"].slots
    r.tick()
    assert not r.records("item")


def test_leaving_ends_the_holders_instance_and_coming_back_makes_a_new_one(rig):
    r = rig(examples=True)
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "fire_staff")
    r.tick()
    r.io.leave("Steve")
    r.tick()
    assert not r.records("item")
    r.io.join("Steve")
    r.tick(2)
    assert [rec.user for rec in r.records("item")] == ["Steve"]
    assert ID in r.mods("Steve")


CHARM = '''
class Charm(Item):
    class Config(ItemConfig):
        summary = "Anywhere on you: says when you hit something."
        base = "warped_fungus_on_a_stick"
        name = "Charm"
        active_in = "inventory"

    def start(self, ctx):
        self.listen(Hit, "on_hit")

    def on_hit(self, ctx, ev):
        self.command("say charm heard a hit with %s" % ev.item)
'''

FAULTY = '''
class Faulty(Item):
    class Config(ItemConfig):
        summary = "Breaks when held."
        base = "carrot_on_a_stick"
        name = "Faulty"
        active_in = "mainhand"

    def start(self, ctx):
        raise RuntimeError("broken start")
'''


def test_an_item_hears_its_holders_hits_whatever_is_in_hand(rig):
    from vfg_engine.io import Command
    r = rig({"charm": CHARM}, examples=True)
    r.start()
    r.io.join("Steve")
    r.io.players["Steve"].inventory.append("charm")
    r.io.hold("Steve", "fire_staff")
    r.tick()
    r.io.hit("Steve", "a-zombie")
    r.tick()
    assert "say charm heard a hit with fire_staff" in [op.text for op in r.io.ops_of(Command)]


def test_a_broken_item_is_not_made_again_until_it_is_let_go(rig):
    r = rig({"faulty": FAULTY})
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "faulty")
    r.tick(5)
    assert sum("start failed" in m for m in r.lines()) == 1
    assert not r.records("item")
    r.io.unhold("Steve")
    r.tick()
    r.io.hold("Steve", "faulty")
    r.tick()
    assert sum("start failed" in m for m in r.lines()) == 2


FRAGILE = '''
_made = []


def _count():
    _made.append(1)
    if len(_made) > 1:              # the load check's State() works; the next ones fail
        raise RuntimeError("no more states")
    return 0


class Fragile(Item):
    class Config(ItemConfig):
        summary = "Its State fails when it is made in the game."
        base = "carrot_on_a_stick"
        name = "Fragile"
        active_in = "mainhand"

    class State(State):
        n: int = Field(default_factory=_count)
'''


def test_a_state_that_fails_to_be_made_refuses_only_that_instance(rig):
    r = rig({"fragile": FRAGILE}, examples=True)
    assert r.start()["errors"] == []
    r.io.join("Steve")
    r.io.join("Alex")
    r.io.hold("Steve", "fragile")
    r.io.hold("Alex", "fire_staff")
    r.tick(5)                                            # the tick never dies
    assert [rec.cid for rec in r.records("item")] == ["fire_staff"]
    assert "fragile|Steve" in r.rt.broken
    assert sum("not made" in m for m in r.lines()) == 1  # not tried again every tick
    assert r.op("status")["ok"]


WATCHER = '''
class Watcher(Item):
    class Config(ItemConfig):
        summary = "Says when it is taken out."
        base = "carrot_on_a_stick"
        name = "Watcher"
        active_in = "inventory"

    def start(self, ctx):
        self.command("say start")
        self.listen(Held, "on_held")

    def on_held(self, ctx, ev):
        self.command("say held in %s" % ev.slot)
'''


def test_no_held_events_on_a_join_or_a_restart(rig):
    from vfg_engine.engine import Engine
    from vfg_engine.io import Command
    r = rig({"watcher": WATCHER})
    r.start()
    r.io.join("Steve")
    r.io.hold("Steve", "watcher")              # holding it already as they join
    r.tick(2)
    r.io.hold("Steve", "watcher", slot="offhand")   # a real change: held in the off hand
    r.tick()
    said = [op.text for op in r.io.ops_of(Command)]
    assert said == ["say start", "say held in offhand"]
    r.run_for(1.2)
    r.engine.close()
    r.io.closed = False
    r.io.sent.clear()
    r.engine = Engine(r.io, r.settings, clock=r.clock)
    r.engine.start()
    r.tick(2)
    assert [op.text for op in r.io.ops_of(Command)] == []      # restored, nothing came into a hand
