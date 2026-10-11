"""scripts/_lib/places.py: pure, so it runs here against FakeWorld.respond and the
storage replies recorded from a real 26.3 server."""
from __future__ import annotations

import pytest

import mc_26_3 as R
from conftest import FakeWorld, load_places

places = load_places()


def test_save_list_get_forget_against_the_fake():
    w = FakeWorld()
    rcon = w.respond
    assert places.all(rcon) == {}
    assert places.save(rcon, "castle", (197.9, 68.0, 251.2), "overworld", "castle_steve") == \
        {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "castle_steve"}
    assert w.sent[-1] == 'data modify storage everhear:places castle set value ' \
                         '{pos:[197,68,251],dim:"minecraft:overworld",by:"castle_steve"}'
    # Saving the same place again is "Nothing changed", and still a save.
    assert places.save(rcon, "castle", (197, 68, 251), "minecraft:overworld", "castle_steve")["pos"] == [197, 68, 251]
    places.save(rcon, "home", (-368.5, 70, 912), "overworld")
    places.save(rcon, "1st_base", (1, -5, 2), "nether", "base_alex")
    assert places.get(rcon, "home") == {"pos": [-369, 70, 912], "dim": "minecraft:overworld", "by": ""}
    assert places.get(rcon, "nope") is None
    assert places.all(rcon) == {
        "castle": {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "castle_steve"},
        "home": {"pos": [-369, 70, 912], "dim": "minecraft:overworld", "by": ""},
        "1st_base": {"pos": [1, -5, 2], "dim": "minecraft:the_nether", "by": "base_alex"},
    }
    # Overwrite moves it.
    places.save(rcon, "castle", (198, 68, 251), by="castle_alex")
    assert places.get(rcon, "castle")["pos"] == [198, 68, 251]
    assert places.forget(rcon, "home") is True
    assert places.forget(rcon, "home") is False
    assert set(places.all(rcon)) == {"castle", "1st_base"}


def test_all_reads_the_real_storage_reply():
    got = places.all(lambda c: R.STORAGE_THREE)
    assert got == {
        "123": {"pos": [1, 2, 3], "dim": "minecraft:overworld", "by": ""},
        "castle": {"pos": [198, 68, 251], "dim": "minecraft:overworld", "by": "castle_alex"},
        "1st_base": {"pos": [1, -5, 2], "dim": "minecraft:the_nether", "by": "base_alex"},
    }
    assert places.all(lambda c: R.STORAGE_EMPTY) == {}
    assert places.get(lambda c: R.STORAGE_ONE, "castle") == \
        {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "castle_steve"}
    assert places.get(lambda c: R.NO_ELEMENTS % "castle", "castle") is None
    assert places.forget(lambda c: R.STORAGE_MODIFIED, "castle") is True
    assert places.forget(lambda c: R.NOTHING_CHANGED, "castle") is False


def test_line():
    assert places.line("castle", {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "castle_steve"}) == \
        "castle 197 68 251 overworld (castle_steve)"
    assert places.line("home", {"pos": [-368, 70, 912], "dim": "minecraft:overworld", "by": ""}) == \
        "home -368 70 912 overworld"
    assert places.line("base", {"pos": [1, -5, 2], "dim": "minecraft:the_nether", "by": "base_alex"}) == \
        "base 1 -5 2 the_nether (base_alex)"


def test_refusals_and_failures():
    w = FakeWorld()
    for bad in ("Castle", "my castle", "", "a" * 33, "x;op", None):
        with pytest.raises(ValueError):
            places.save(w.respond, bad, (0, 0, 0))
        with pytest.raises(ValueError):
            places.get(w.respond, bad)
    with pytest.raises(ValueError):
        places.save(w.respond, "home", (0, 0))
    with pytest.raises(ValueError):
        places.save(w.respond, "home", (0, 0, 0), by='castle_steve"}')
    with pytest.raises(ValueError):
        places.save(w.respond, "home", (0, 0, 0), dim="the moon")
    assert w.sent == []
    # mc.rcon answers "" when RCON fails: that is an error, never "no places".
    for reply in ("", "Unknown or incomplete command. See below for error<--[HERE]"):
        with pytest.raises(places.PlaceError):
            places.all(lambda c: reply)
        with pytest.raises(places.PlaceError):
            places.get(lambda c: reply, "castle")
        with pytest.raises(places.PlaceError):
            places.save(lambda c: reply, "castle", (0, 0, 0))
        with pytest.raises(places.PlaceError):
            places.forget(lambda c: reply, "castle")


def test_norm_dim():
    assert places.norm_dim("overworld") == "minecraft:overworld"
    assert places.norm_dim("nether") == "minecraft:the_nether"
    assert places.norm_dim("the_end") == "minecraft:the_end"
    assert places.norm_dim("minecraft:the_nether") == "minecraft:the_nether"
    assert places.norm_dim("mypack:sky") == "mypack:sky"
    assert places.norm_dim("") == "minecraft:overworld"


@pytest.mark.parametrize("text,want", [
    (R.RESPAWN_SNBT, {"pos": [1, 2, 3], "forced": 0, "pitch": 0.0, "dimension": "minecraft:overworld", "yaw": 0.0}),
    (R.ATTRIBUTES_SNBT, [{"id": "minecraft:movement_speed", "base": 0.23000000417232513},
                         {"id": "minecraft:scale", "base": 2.0}]),
    (R.STRING_SNBT, "minecraft:overworld"),
    (R.GLOWING_SNBT, 1),
    (R.TAGS_SNBT, ["vfg_summon_zombies_steve_new", "vfg", "vfg_summon_zombies_steve"]),
    (R.POS, [0.5, 100.0, 0.5]),
    (R.POS_AFTER_SPREAD, [-1408.5, 68.0, 1410.5]),
    ("{}", {}),
    ("[]", []),
    ("[B; 1b, 0b]", [1, 0]),
    ("[L; -5L, 7L]", [-5, 7]),
    ("{'a b': 'it''s'}".replace("''", "\\'"), {"a b": "it's"}),
    ('{x: "say \\"hi\\"\\\\", y: 1.0E-5d, z: -3s, w: 2.5f}', {"x": 'say "hi"\\', "y": 1e-05, "z": -3, "w": 2.5}),
    ('"caf\\u00e9"', "café"),
])
def test_snbt(text, want):
    assert places.snbt(text) == want


@pytest.mark.parametrize("text", ["", "{", "{a 1}", "[1, 2", '"open', "{a: 1} extra", "{a: 1,}x"])
def test_snbt_refuses_garbage(text):
    with pytest.raises(ValueError):
        places.snbt(text)


def test_an_entry_written_by_hand_never_reaches_a_command():
    """A place whose dimension is not an id (someone ran data modify by hand) is skipped."""
    reply = R.STORAGE % '{bad: {pos: [1, 2, 3], by: "", dim: "minecraft:overworld run fill ~ ~ ~ ~9 ~9 ~9 tnt"}, ' \
                        'ok: {pos: [4, 5, 6], by: "x\\"y", dim: "minecraft:overworld"}}'
    assert places.all(lambda c: reply) == {"ok": {"pos": [4, 5, 6], "dim": "minecraft:overworld", "by": ""}}
