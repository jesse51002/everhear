"""The record of the port: every action over a library script takes the values the script
took before it was ported, and every preset's values class the values the preset took: the
same names, the same defaults, the same limits, compatible types and the same live-ness.
What they took is in fixtures/, read from each file's literal VALUES dict before the port
removed them (2026-10-10), and kept as it was:

    fixtures/values_before.json         {script: {value: {type, default, min, max, of, live}}}
    fixtures/preset_values_before.json  {preset: {value: {...}}}

    uv run --no-project --with pytest --with pydantic pytest actions/tests/test_values_parity.py

The defaults are compared after the action's `defaults` and before its `fixed` (a value it
fixes at the script's own default counts), for each action that is not a variant of another;
a variant fixes what it changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPTS_FILE = FIXTURES / "values_before.json"
PRESETS_FILE = FIXTURES / "preset_values_before.json"

# A type in the record, and the kinds (actions/_types.py's kind()) its field may have now.
COMPATIBLE = {
    "int": {"int"}, "number": {"number"}, "bool": {"bool"}, "enum": {"enum"},
    "str": {"str", "place", "block mix", "item name", "item or empty", "replace rule"},
    "player": {"players", "player"}, "mob": {"mob"}, "block": {"block"}, "item": {"item"},
    "particle": {"particle"}, "sound": {"sound"},
}
# A list type of its own, as `list of <its item's kind>`.
LISTS = {"replace rules": "list of replace rule"}
# Values the spec gives a script's action that the script did not take: {script: {value: why}}.
ADDED = {
    "horde_boss": {"at": "Library_Actions: horde_boss has no place and gets one"},
    # c2 (weapons): weapon_chest ice_staff lightning_staff boom_trident wither_wand meteor_staff sniper_rifle
    # gravity_staff boom_bow zeus gun_chests projectile_shield tether
    "boom_trident": {"item": "Library_Actions gaps: spear_bomb gives a trident of its own name"},
    # end c2
    # c3 (bosses, mobs, rules): ice_queen dragon_boss horde_boss god_visits zoo horde horror_mode herobrine
    # walking_dead sneaky_creepers demon_lord lucky_blocks attribute_guard anti_camp grave_marks arena
    "ice_queen": {"boss_minion": "the boss preset's; her abilities replace its minions, so it does nothing",
                  "boss_minions": "the boss preset's; her abilities replace its minions, so it does nothing"},
    # end c3
    # 2026-10-12: push throws a player by the launch preset, so a script holding push holds launch too
    **{s: {k: "the launch preset's: push throws a player by it"
           for k in ("launch_way", "launch_strength", "launch_landing", "launch_method")}
       for s in ("barrier_shield", "gravity_staff", "projectile_shield", "weapon_chest")},
}
# Values renamed as their script was ported, where the old name cannot be a field: {script: {old: new}}.
RENAMED = {
    "god_visits": {"takes": "take_items"},   # c3: `takes` is every action's ClassVar, the fields the planner fills
}


# ------------------------------------------------------------------ comparing

def problems(fields: dict, defaults: dict, values: dict, T, added: dict = {}, check_defaults: bool = True) -> list[str]:
    """What differs between fields ({name: FieldInfo}, with `defaults` {name: default}) and the
    snapshot's values ({name: entry})."""
    out = []
    for key in values:
        if key not in fields:
            out.append("no field for %s" % key)
    for key in fields:
        if key not in values and key not in added:
            out.append("%s is not one of the values it had" % key)
    for key, e in values.items():
        f = fields.get(key)
        if f is None:
            continue
        kind = T.kind(f.annotation, f.metadata)
        kind = LISTS.get(kind, kind)
        was_coords = e["type"] == "list" and e["of"] in ("number", "int") and e["default"] == []
        if e["type"] == "list":
            item = kind[len("list of "):] if kind.startswith("list of ") else None
            ok = (kind == "place" and was_coords) or (item is not None and (
                item == "enum" if isinstance(e["of"], list) else item in COMPATIBLE.get(e["of"], {e["of"]})))
        else:
            ok = kind in COMPATIBLE[e["type"]]
        if not ok:
            out.append("%s is %s; it was %s%s" % (key, kind, e["type"], " of %s" % e["of"] if e["type"] == "list" else ""))
        if isinstance(e["of"], list) and set(T.choices(f.annotation, f.metadata) or []) != set(e["of"]):
            out.append("%s: its choices %s; they were %s" % (key, T.choices(f.annotation, f.metadata), e["of"]))
        lo, hi = T.limits(f.annotation, f.metadata)
        if (lo, hi) != (e["min"], e["max"]):
            out.append("%s: limits %s..%s; they were %s..%s" % (key, lo, hi, e["min"], e["max"]))
        if T.is_live(f.metadata) != e["live"]:
            out.append("%s: %s; it was %s" % (key, *("live" if x else "not live" for x in (T.is_live(f.metadata), e["live"]))))
        if check_defaults:
            have, want = defaults[key], e["default"]
            if not (have == want or (kind == "place" and was_coords and have in ("", None))):
                out.append("%s: default %r; it was %r" % (key, have, want))
    return out


# ------------------------------------------------------------------ the tests

SCRIPTS = json.loads(SCRIPTS_FILE.read_text())
PRESETS = json.loads(PRESETS_FILE.read_text())


def actions_lib():
    """lib/py/vfg_actions.py (it needs pydantic, which --write does not)."""
    if str(REPO / "lib" / "py") not in sys.path:
        sys.path.insert(0, str(REPO / "lib" / "py"))
    import vfg_actions
    return vfg_actions


@pytest.fixture(scope="module")
def catalog():
    return actions_lib().Catalog(REPO, None)


def over(catalog) -> dict[str, list[str]]:
    """{script: [the actions over it]}, for every script in the snapshot."""
    out: dict[str, list[str]] = {}
    for name in catalog.names():
        out.setdefault(catalog.get(name).script, []).append(name)
    return out


def test_the_record_holds_every_library_script_and_preset_it_had():
    assert len(SCRIPTS) >= 54 and len(PRESETS) >= 29
    for values in (*SCRIPTS.values(), *PRESETS.values()):
        for e in values.values():
            assert set(e) == {"type", "default", "min", "max", "of", "live"}
    assert set(SCRIPTS) <= {p.parent.name for p in REPO.glob("scripts/[!_]*/main.py")}
    assert set(PRESETS) <= {p.stem for p in REPO.glob("scripts/_presets/*.py")}


def test_every_action_over_a_library_script_takes_its_values(catalog):
    T = actions_lib()._types()
    actions_over = over(catalog)
    assert sorted(s for s in SCRIPTS if s not in actions_over) == []   # every script has its action
    failed = {}
    for script, names in sorted(actions_over.items()):
        if script not in SCRIPTS:
            continue  # a new script, not in the record
        for name in names:
            cls = catalog.get(name)
            variant = any(k is not cls and k in {catalog.get(n) for n in catalog.names()} for k in cls.__mro__)
            before = {RENAMED.get(script, {}).get(k, k): e for k, e in SCRIPTS[script].items()}
            defaults = dict(cls.__vfg__["unfixed"])
            for k, x in cls.fixed.items():   # fixed at the script's own default: wither_wand's summon_mob=wither
                if k in before and x == before[k]["default"]:
                    defaults[k] = x
            bad = problems(cls.model_fields, defaults, before, T,
                           ADDED.get(script, {}), check_defaults=not variant)
            if bad:
                failed[name] = bad
    assert failed == {}


def test_every_preset_class_takes_its_values():
    T = actions_lib()._types()
    have = {}
    for name in PRESETS:
        try:
            have[name] = T.preset(name)
        except ImportError:
            continue
    assert sorted(set(PRESETS) - set(have)) == []
    failed = {}
    for name, cls in have.items():
        defaults = {k: f.get_default(call_default_factory=True) for k, f in cls.model_fields.items()}
        bad = problems(cls.model_fields, defaults, PRESETS[name], T)
        for k, f in cls.model_fields.items():
            text = (f.description or "").strip()
            if not text:
                bad.append("%s has no description" % k)
            elif "\n" in text:
                bad.append("%s's description is more than one line (describe() gives each field one)" % k)
            why = T.type_problem(f.annotation, f.metadata)
            if why:
                bad.append("%s: %s" % (k, why))
        try:  # every default is one its own field takes, as vfg action check asks of an action's
            cls.model_validate(defaults, context={"notes": [], "clamp": False})
        except ValueError as e:
            bad.append("a default its field refuses: %s" % e)
        if bad:
            failed[name] = bad
    assert failed == {}


def test_problems_says_what_differs():
    T = actions_lib()._types()
    from actions._types import Field, Literal, Live, Place, Players, Values

    class V(Values):
        player: Players = "@a"
        """who"""
        n: Live[int] = Field(3, ge=1, le=9)
        """n"""
        mode: Literal["a", "b"] = "a"
        """mode"""
        at: Place = ""
        """where"""
        extra: str = ""
        """extra"""

    defaults = {k: f.default for k, f in V.model_fields.items()}
    values = {"player": {"type": "player", "default": "@a", "min": None, "max": None, "of": None, "live": False},
              "n": {"type": "int", "default": 2, "min": 0, "max": 9, "of": None, "live": False},
              "mode": {"type": "enum", "default": "a", "min": None, "max": None, "of": ["a", "b", "c"], "live": False},
              "at": {"type": "list", "default": [], "min": None, "max": None, "of": "number", "live": False},
              "gone": {"type": "bool", "default": True, "min": None, "max": None, "of": None, "live": False}}
    assert problems(V.model_fields, defaults, values, T) == [
        "no field for gone", "extra is not one of the values it had", "n: limits 1..9; they were 0..9",
        "n: live; it was not live", "n: default 3; it was 2", "mode: its choices ['a', 'b']; they were ['a', 'b', 'c']"]
    assert problems(V.model_fields, defaults, values, T, {"extra": "new"}, check_defaults=False)[:2] == \
        ["no field for gone", "n: limits 1..9; they were 0..9"]
