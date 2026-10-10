"""Every action over a library script takes the values the script took before it was ported,
and every preset's values class the values its VALUES held: the same names, the same
defaults, the same limits, compatible types and the same live-ness. What they took is the
snapshot in fixtures/, read from each file's literal VALUES before the port (ast, no import):

    fixtures/values_before.json         {script: {value: {type, default, min, max, of, live}}}
    fixtures/preset_values_before.json  {preset: {value: {...}}}

    uv run --no-project --with pytest --with pydantic pytest actions/tests/test_values_parity.py

A script with no action yet is skipped (listed in one log line); every preset has its class.
The defaults are compared after the action's `defaults` and before its `fixed` (a value it
fixes at the script's own default counts), for each action that is not a variant of another;
a variant fixes what it changes.

To take the snapshot again (it keeps a script whose VALUES is gone, as it was):
    uv run --no-project --with pytest python actions/tests/test_values_parity.py --write
"""
from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCRIPTS_FILE = FIXTURES / "values_before.json"
PRESETS_FILE = FIXTURES / "preset_values_before.json"
log = logging.getLogger("parity")

# A VALUES type, and the kinds (actions/_types.py's kind()) its field may have now.
COMPATIBLE = {
    "int": {"int"}, "number": {"number"}, "bool": {"bool"}, "enum": {"enum"}, "str": {"str", "place"},
    "player": {"players", "player"}, "mob": {"mob"}, "block": {"block"}, "item": {"item"},
    "particle": {"particle"}, "sound": {"sound"},
}
# Values the spec gives a script's action that its VALUES did not have: {script: {value: why}}.
ADDED = {
    "horde_boss": {"at": "Library_Actions: horde_boss has no place and gets one"},
    # c2 (weapons): weapon_chest ice_staff lightning_staff boom_trident wither_wand meteor_staff sniper_rifle
    # gravity_staff boom_bow zeus gun_chests projectile_shield tether
    "boom_trident": {"item": "Library_Actions gaps: spear_bomb gives a trident of its own name"},
    # end c2
}


# ------------------------------------------------------------------ the snapshot

def literal_values(path: Path) -> dict | None:
    """A file's top-level VALUES literal, read without running it; None without one."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VALUES" for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def entries(values: dict) -> dict:
    return {key: {"type": e["type"], "default": e["default"], "min": e.get("min"), "max": e.get("max"),
                  "of": e.get("of"), "live": bool(e.get("live", False))} for key, e in values.items()}


def snapshot(write: bool = False) -> tuple[dict, dict]:
    """(scripts, presets) as they are now, over the fixtures' entries for files that no longer
    have VALUES; written to the fixtures when `write`."""
    out = []
    for path, files in ((SCRIPTS_FILE, sorted(REPO.glob("scripts/[!_]*/main.py"))),
                        (PRESETS_FILE, sorted(REPO.glob("scripts/_presets/*.py")))):
        old = json.loads(path.read_text()) if path.exists() else {}
        now = dict(old)
        for f in files:
            name = f.parent.name if f.name == "main.py" else f.stem
            values = literal_values(f)
            if values is not None:
                now[name] = entries(values)
        now = dict(sorted(now.items()))
        if write:
            path.parent.mkdir(exist_ok=True)
            path.write_text(dumps(now), encoding="utf-8")
        out.append(now)
    return out[0], out[1]


def dumps(snap: dict) -> str:
    """JSON with one line per value, so a diff of the snapshot reads value by value."""
    one = lambda x: json.dumps(x, ensure_ascii=False)  # noqa: E731
    blocks = []
    for name, values in snap.items():
        lines = ["  %s: %s" % (one(k), one(e)) for k, e in values.items()]
        blocks.append(" %s: {\n%s\n }" % (one(name), ",\n".join(lines)) if lines else " %s: {}" % one(name))
    return "{\n" + ",\n".join(blocks) + "\n}\n"


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

SCRIPTS = json.loads(SCRIPTS_FILE.read_text()) if SCRIPTS_FILE.exists() else {}
PRESETS = json.loads(PRESETS_FILE.read_text()) if PRESETS_FILE.exists() else {}


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


def test_the_snapshot_holds_every_library_script_and_preset():
    scripts = {p.parent.name for p in REPO.glob("scripts/[!_]*/main.py")}
    assert set(SCRIPTS) >= {s for s in scripts if literal_values(REPO / "scripts" / s / "main.py") is not None}
    assert len(SCRIPTS) >= 54 and len(PRESETS) >= 29
    for values in (*SCRIPTS.values(), *PRESETS.values()):
        for e in values.values():
            assert set(e) == {"type", "default", "min", "max", "of", "live"}
    # Taken from the files as they are, while they still have VALUES.
    now_scripts, now_presets = snapshot()
    assert now_scripts == SCRIPTS and now_presets == PRESETS


def test_every_action_over_a_library_script_takes_its_values(catalog):
    T = actions_lib()._types()
    actions_over = over(catalog)
    waiting = sorted(s for s in SCRIPTS if s not in actions_over)
    log.warning("parity: %d of %d scripts have no action yet: %s", len(waiting), len(SCRIPTS), ", ".join(waiting))
    assert "railgun" in actions_over
    failed = {}
    for script, names in sorted(actions_over.items()):
        if script not in SCRIPTS:
            continue  # a new script, with no VALUES before
        for name in names:
            cls = catalog.get(name)
            variant = any(k is not cls and k in {catalog.get(n) for n in catalog.names()} for k in cls.__mro__)
            defaults = dict(cls.__vfg__["unfixed"])
            for k, x in cls.fixed.items():   # fixed at the script's own default: wither_wand's summon_mob=wither
                if k in SCRIPTS[script] and x == SCRIPTS[script][k]["default"]:
                    defaults[k] = x
            bad = problems(cls.model_fields, defaults, SCRIPTS[script], T,
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


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: uv run --no-project --with pytest python actions/tests/test_values_parity.py --write")
    s, p = snapshot(write=True)
    print("wrote %d scripts to %s and %d presets to %s" % (len(s), SCRIPTS_FILE, len(p), PRESETS_FILE))
