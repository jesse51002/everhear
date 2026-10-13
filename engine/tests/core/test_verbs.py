"""The new self.* and ctx.world reads (DESIGN2 §6, "Also needed"), each wired to its Io op or read,
on FakeIo."""

import pytest

from vfg_engine.io import (Bossbar, Command, EntityData, GiveEffect, GiveItem, Ignite,
                           Motion, Particle, Remove, Sound, Spawnpoint, StopSound, Summon, Teleport)

TOOLS = '''
class Tools(Scenario):
    class Config(ScenarioConfig):
        summary = "Does one thing it is told."
        duration_s = UNTIL_STOPPED

    class Args(Args):
        what: str = Field("", description="what to do")
        who: Player = Field("", description="a player")
        mob: str = Field("", description="an entity's uuid")

    def start(self, ctx):
        getattr(self, "do_" + self.args.what)(ctx)

    def do_spawn(self, ctx):
        self.spawn("minecraft:zombie", (1, 64, 1), nbt="{IsBaby:1b}")
        self.spawn("minecraft:armor_stand", (2, 64, 2), keep=True)

    def do_move(self, ctx):
        self.teleport(self.args.who, "10 70 10 nether", yaw=90)
        self.motion(self.args.mob, (0, 1.5, 0))
        self.entity_data(self.args.mob, "{NoAI:1b}")
        self.ignite(self.args.who, 4)
        self.ignite(self.args.mob, 8)

    def do_modifier(self, ctx):
        self.modifier("movement_speed", -0.5, "add_multiplied_total", on=self.args.mob)

    def do_show(self, ctx):
        self.bossbar("boss", name="The Boss", color="red", value=50, max=100, players="@a")
        self.sound("minecraft:entity.wither.spawn", at=(0, 64, 0), to=[self.args.who])
        self.particle("minecraft:end_rod", (0, 64, 0), force=True, to=[self.args.who], dim="minecraft:the_end")
        self.stop_sound("minecraft:music.dragon", to=[self.args.who])
        self.spawnpoint(self.args.who, "0 80 0")

    def do_kit(self, ctx):
        a = self.spawn("minecraft:zombie", (1, 64, 1))
        b = self.spawn("minecraft:zombie", (2, 64, 2))
        self.remove(a)
        self.state.kit["left"] = self.spawned
        self.blocks.setblock((0, 64, 0), 'minecraft:oak_sign[rotation=8]{front_text:{messages:["hi", "", "", ""]}}')
        self.blocks.setblock((0, 65, 0), "minecraft:chest{Items:[%s]}" % self.item_stack("bow"))
        self.after(0.1, "kit_step", "noop")

    def do_ask(self, ctx):
        self.ask("locate structure minecraft:village_plains", "located")

    def located(self, ctx, reply):
        self.result("located", reply=reply)
        self.end()

    def do_read(self, ctx):
        w = ctx.world
        look = w.ray((0.5, 70.5, 0.5), (0, -1, 0), 20)
        self.result("read", probe=w.probe([(0, 63, 0), (0, 64, 0), (0, 70, 0)], "solid"),
                    open=w.probe([(0, 64, 0)], "open"), ground=w.ground_y(0, 0), ray=[look.kind, look.pos[1]],
                    near=[e.type for e in w.entities((0, 64, 0), 100)], loaded=w.loaded(0, 0), mspt=w.mspt)
        self.end()


class Frozen(Mark):
    class Config(MarkConfig):
        summary = "Slow, and glowing."
        duration_s = 20
        damage = 0

    def start(self, ctx):
        self.modifier("movement_speed", -0.9, "add_multiplied_total")
        self.keep_potion("night_vision", seconds=12)


class Charge(Ability):
    class Config(AbilityConfig):
        summary = "A charged beam."
        scale_per_point = 0
        charge_s = 1
        cooldown_s = 0
        duration_s = 1
        damage = 0

    def on_charge(self, ctx, ev):
        self.command("say charging %.1f" % ev.progress)

    def on_cast(self, ctx, ev):
        self.command("say fired")


class Bow(Item):
    class Config(ItemConfig):
        summary = "Its arrows explode where they land."
        base = "warped_fungus_on_a_stick"
        name = "Boom Bow"
        active_in = "mainhand"
        model = "minecraft:bow"
        color = "gold"
        lore = ["Arrows explode", "where they land"]
        glint = True
        enchantments = {"power": 5}
        unbreakable = True

    def start(self, ctx):
        self.listen(Land, "on_land")

    def on_land(self, ctx, ev):
        self.command("say boom at %s from %s" % (list(ev.pos), ev.item))


class Brute(Actor):
    class Config(ActorConfig):
        summary = "A glowing brute that keeps its sword."
        entity_type = "minecraft:zombie"
        health = 40
        size = 1.2
        damage = 5
        name = "Brute"
        name_visible = True
        glowing = True
        equipment = {"mainhand": "minecraft:iron_sword"}
        nbt = "{IsBaby:0b}"


class Ears(Ability):
    class Config(AbilityConfig):
        summary = "Says what you do."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 0

    def start(self, ctx):
        self.listen(Mine, "on_mine", blocks=["diamond_ore", "minecraft:sponge"])
        self.listen(Death, "on_death")
        self.listen(Hit, "on_hit")
        self.listen(Land, "on_land")

    def on_mine(self, ctx, ev):
        self.command("say mined %s at %s" % (ev.block, list(ev.pos)))

    def on_death(self, ctx, ev):
        self.command("say died at %s in %s" % (list(ev.pos), ev.dim))

    def on_hit(self, ctx, ev):
        self.command("say hit ranged=%s" % ev.ranged)

    def on_land(self, ctx, ev):
        self.command("say land %s %s" % (ev.hit, ev.item))
'''


@pytest.fixture
def r(rig):
    r = rig({"tools": TOOLS})
    assert r.start()["errors"] == []
    r.io.join("Steve")
    r.tick()
    return r


def do(r, what, wait=False, **args):
    """Start the tools scenario; with wait, run until it ends and give its answer."""
    if not wait:
        return r.op("start", scenario="tools", args=dict(what=what, **args))
    fut = r.engine.submit("start", {"scenario": "tools", "args": dict(what=what, **args), "wait_s": 5})
    for _ in range(50):
        r.tick()
        if fut.done():
            return fut.result()
    raise AssertionError("it did not end")


def says(r):
    return [op.text for op in r.io.ops_of(Command) if op.text.startswith("say ")]


def test_spawn_with_nbt_and_keep(r):
    iid = do(r, "spawn")["instance"]
    zombie, stand = r.io.ops_of(Summon)
    assert zombie.nbt == "{IsBaby:1b}" and "everhear.spawn" in zombie.tags
    assert "everhear.kept" in stand.tags
    r.op("stop", instance=iid)
    r.tick()
    assert {op.uuid for op in r.io.ops_of(Remove)} == {zombie.uuid, stand.uuid}   # a stop takes both
    # ended by itself, the kept one stays (and the sweep leaves it)
    iid = do(r, "spawn")["instance"]
    zombie2, stand2 = r.io.ops_of(Summon)[2:]
    r.rt.end_record(r.rt.records[iid], "ended")
    r.run_for(6)
    assert stand2.uuid in r.io.by_uuid and zombie2.uuid not in r.io.by_uuid


def test_teleport_motion_data_and_fire(r):
    mob = r.io.add_entity(pos=(3.0, 64.0, 3.0))
    do(r, "move", who="Steve", mob=mob)
    (tp,) = r.io.ops_of(Teleport)
    assert (tp.target, tp.dim, tp.pos, tp.yaw) == ("Steve", "minecraft:the_nether", (10.0, 70.0, 10.0), 90.0)
    assert r.io.ops_of(Motion)[0] == Motion(mob, (0.0, 1.5, 0.0)) and r.io.by_uuid[mob].motion == (0.0, 1.5, 0.0)
    assert r.io.ops_of(EntityData)[0].snbt == "{NoAI:1b}"
    assert r.io.fire == {"Steve": 4.0, mob: 8.0} and len(r.io.ops_of(Ignite)) == 2


def test_a_modifier_on_a_world_mob_is_owned_and_removed(r):
    mob = r.io.add_entity(pos=(3.0, 64.0, 3.0))
    iid = r.op("start", scenario="tools", args={"what": "modifier", "mob": mob})["instance"]
    r.tick(2)
    (key,) = r.io.by_uuid[mob].modifiers
    assert key[0] == "minecraft:movement_speed" and key[1].startswith("everhear:scenario/tools/i")
    r.op("stop", instance=iid)
    r.tick(2)
    assert r.io.by_uuid[mob].modifiers == {}


def test_boss_bar_sounds_particles_and_spawnpoint(r):
    iid = do(r, "show", who="Steve")["instance"]
    bar = r.io.ops_of(Bossbar)[0]
    assert bar.id.startswith("everhear:tools_") and bar.players == ("Steve",) and bar.value == 50
    assert r.io.ops_of(Sound)[0].to == ("Steve",)
    p = r.io.ops_of(Particle)[0]
    assert p.force and p.to == ("Steve",) and p.dim == "minecraft:the_end"
    assert r.io.ops_of(StopSound)[0] == StopSound(("Steve",), "minecraft:music.dragon")
    assert r.io.spawnpoints["Steve"] == ("minecraft:overworld", (0.0, 80.0, 0.0))
    assert r.io.ops_of(Spawnpoint)
    r.op("stop", instance=iid)
    r.tick()
    assert r.io.bossbars == {}                          # its boss bar went with it


def test_a_slow_command_answers_next_tick(r):
    r.io.query_reply = lambda c: "The nearest minecraft:village_plains is at [100, ~, 200]"
    fut = r.engine.submit("start", {"scenario": "tools", "args": {"what": "ask"}, "wait_s": 2})
    r.run_for(0.5)
    reply = fut.result(timeout=0)
    assert reply["result"]["facts"]["reply"].startswith("The nearest") and r.io.queries == [
        "locate structure minecraft:village_plains"]


def test_world_reads(r):
    r.io.add_entity(pos=(5.0, 64.0, 5.0), type="minecraft:cow")
    facts = do(r, "read", wait=True)["result"]["facts"]
    assert facts["probe"] == [True, False, False] and facts["open"] == [True]
    assert facts["ground"] == 64 and facts["ray"] == ["block", pytest.approx(64.0, abs=0.11)]
    assert facts["near"] == ["minecraft:cow"] and facts["loaded"] is True and facts["mspt"] == 20.0
    reads = r.op("status")["reads"]
    assert {"probe", "ground_y", "ray", "entities", "loaded"} <= set(reads)


def test_marks_on_any_mob_end_when_it_is_gone(r):
    mob = r.io.add_entity(pos=(3.0, 64.0, 3.0))
    assert r.op("apply", who=mob, mark="frozen")["ok"]
    r.tick(2)
    assert ("minecraft:movement_speed", "everhear:mark/frozen") in r.io.by_uuid[mob].modifiers
    r.io.kill_entity(mob)
    r.tick(2)
    assert not r.records("mark")


def test_keep_potion_for_longer_ones(r):
    r.op("apply", who="Steve", mark="frozen")
    r.run_for(3)
    gives = [op for op in r.io.ops_of(GiveEffect) if op.effect == "minecraft:night_vision"]
    assert gives[0].seconds == 12 and 3 <= len(gives) <= 4          # every second, so it never flickers


def test_on_charge_runs_while_the_charge_counts_down(r):
    r.op("grant", who="Steve", ability="charge")
    r.op("bind", player="Steve", phrase="beam", ability="charge")
    assert r.op("cast", player="Steve", phrase="beam")["status"] == "charging"
    r.run_for(1.2)
    said = says(r)
    assert said[0].startswith("say charging 0.") and said[-1] == "say fired" and len(said) >= 8


def test_an_items_look_is_given_as_components(r):
    r.op("give", player="Steve", item="bow")
    (give,) = r.io.ops_of(GiveItem)
    assert give.name is None
    assert give.components == {
        "minecraft:item_model": '"minecraft:bow"',
        "minecraft:item_name": '{text:"Boom Bow",color:"gold",italic:false}',
        "minecraft:lore": '[{text:"Arrows explode",color:"gray",italic:false},{text:"where they land",color:"gray",italic:false}]',
        "minecraft:enchantment_glint_override": "true",
        "minecraft:enchantments": '{"minecraft:power":5}',
        "minecraft:unbreakable": "{}"}


def test_actor_extras(r):
    r.op("spawn", actor="brute", at=[0, 64, 0])
    (s,) = r.io.ops_of(Summon)
    assert "drop_chances:{mainhand:0.0f}" in s.nbt and "Glowing:1b" in s.nbt and "CustomNameVisible:1b" in s.nbt
    assert s.nbt.endswith("IsBaby:0b}")


def test_a_bows_land_is_heard_after_it_is_let_go(r):
    r.io.hold("Steve", "bow")
    r.tick()
    r.io.unhold("Steve")                           # switched away as the arrow flies
    r.tick()
    assert r.records("item", "bow")                # lingering
    r.io.land("Steve", item="bow", pos=(4, 64, 4))
    r.tick()
    assert says(r) == ["say boom at [4, 64, 4] from bow"]
    r.run_for(16)
    assert not r.records("item", "bow")            # then it ends


def test_mine_death_place_and_ranged_hits(r):
    r.op("grant", who="Steve", ability="ears")
    r.tick()
    assert r.io.mine("Steve", "minecraft:diamond_ore", (1, 12, 1))
    r.io.shoot("Steve", "some-mob")
    r.io.land("Steve", item=None, pos=(2, 64, 2))
    r.tick()
    r.io.kill("Steve")
    r.tick()
    assert says(r) == ["say mined minecraft:diamond_ore at [1, 12, 1]", "say hit ranged=True",
                       "say land block None", "say died at [0.0, 64.0, 0.0] in minecraft:overworld"]


def test_snapshot_additions_reach_ctx(r):
    p = r.io.players["Steve"]
    p.on_ground, p.sprinting = False, True
    r.tick()
    me = r.rt.players["Steve"]
    assert me.on_ground is False and me.sprinting is True


def test_what_the_kit_needs_of_the_core(r, monkeypatch):
    import vfg_engine.kit as kit
    seen = []
    monkeypatch.setattr(kit, "kit_step", lambda self, ctx, name, *args: seen.append(name))
    from vfg_engine.io import SetBlock
    iid = do(r, "kit")["instance"]
    rec = r.rt.records[iid]
    a, b = [op.uuid for op in r.io.ops_of(Summon)]
    r.run_for(0.3)
    assert rec.state.kit["left"] == [b] and r.io.ops_of(Remove)[0].uuid == a
    sign, chest = r.io.ops_of(SetBlock)
    assert sign.block.endswith('{front_text:{messages:["hi", "", "", ""]}}')
    assert 'components:{"minecraft:custom_data":{everhear:{item:"bow"}}' in chest.block
    assert seen == ["noop"]
    with pytest.raises(ValueError):
        r.rt.objects[iid].remove("not-mine")


MINER = '''
class Miner(Ability):
    class Config(AbilityConfig):
        summary = "Says the sponges you break."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 0

    def start(self, ctx):
        self.listen(Mine, "on_sponge", blocks=["sponge"])

    def on_sponge(self, ctx, ev):
        self.command("say sponge at %s" % list(ev.pos))


class Picky(Ability):
    class Config(AbilityConfig):
        summary = "Watches a block that is not one."
        scale_per_point = 0
        charge_s = 0
        cooldown_s = 0
        duration_s = UNTIL_STOPPED
        damage = 0

    def start(self, ctx):
        self.listen(Mine, "on_it", blocks=["unobtainium"])

    def on_it(self, ctx, ev):
        pass
'''


def test_mine_listens_tell_the_io_what_to_watch(rig):
    r = rig({"miner": MINER})
    r.start()
    r.io.join("Steve")
    r.tick()
    assert r.io.watched_mined == []                        # told at start: nothing yet
    r.op("grant", who="Steve", ability="miner")
    r.tick()
    assert r.io.watched_mined == ["minecraft:sponge"]
    assert not r.io.mine("Steve", "minecraft:stone", (0, 60, 0))    # not watched: no event
    assert r.io.mine("Steve", "minecraft:sponge", (3, 60, 3))
    r.tick()
    assert [op.text for op in r.io.ops_of(Command)] == ["say sponge at [3, 60, 3]"]
    calls = r.io.watch_calls
    r.tick(3)
    assert r.io.watch_calls == calls                       # unchanged: not sent again every tick
    r.op("revoke", who="Steve", ability="miner")
    r.tick()
    assert r.io.watched_mined == []                        # its listen went with it
    r.op("grant", who="Steve", ability="picky")
    r.tick()
    assert any("unobtainium is not a block" in m for m in r.lines())   # refused at the listen
    assert not r.records("ability", "picky")


def test_a_land_event_carries_what_it_hit(rig):
    from vfg_engine.events import from_raw
    from vfg_engine.io import RawEvent
    ev = from_raw(RawEvent(1, "land", "Steve", {"item": "bow", "projectile": "u", "pos": (1.5, 64.0, 2.5),
                                               "dim": "minecraft:the_nether", "hit": "block",
                                               "block": "minecraft:netherrack"}, 10))
    assert (ev.hit, ev.block, ev.dim, ev.pos) == ("block", "minecraft:netherrack", "minecraft:the_nether",
                                                  (1.5, 64.0, 2.5))
