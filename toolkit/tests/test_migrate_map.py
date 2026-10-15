"""lib/migrate_map.py's table, which `vfg engine migrate` moves the old incantations by: every action
a binding could name has a row; each row names a class of its kind (an ability, or a scenario whose
Args take what the row sets), or refuses in a sentence that says what to do instead; and translate()
moves a binding as the row says. The classes are read with ast, not imported (no pydantic here).

    python3 -m unittest toolkit/tests/test_migrate_map.py

The old library's actions are read from git (e67505d:actions/, where they last were). made/main is
untracked and kept only in the main checkout, External/made/main: its actions and its content/ are
read from there when it exists, and the checks that need them are skipped, saying so, when not.
"""
import ast
import os
import re
import subprocess
import sys
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
TOOLKIT = os.path.dirname(TESTS)
REPO = os.path.dirname(TOOLKIT)
sys.path.insert(0, os.path.join(TOOLKIT, "lib"))
import migrate_map  # noqa: E402
from migrate_map import MAP, Refused, translate  # noqa: E402

LIBRARY = os.path.join(REPO, "engine", "library")
OLD = "e67505d"            # the commit whose actions/ the old store's bindings named
# Old actions no binding could name: the base, the old ops on copies and incantations, and the engine's own.
NOT_BINDABLE = {"_types", "__init__", "edit", "stop", "incantation_create", "incantation_edit", "incantation_remove"}
# made/main: in this checkout (External), or in the main checkout beside a worktree (.worktrees/<name>).
MADE = next((d for d in (os.path.join(REPO, "made", "main"),
                         os.path.join(os.path.dirname(os.path.dirname(REPO)), "External", "made", "main"))
             if os.path.isdir(d)), None)
# made/main's actions on 2026-10-13 (inventory G4), which rows of the table are made/main's when its folder
# is not here to say.
MADE_ACTIONS = {"blizzard_freeze", "blizzard_styrka", "destroy_everything", "fire_aura_krown", "hot_fireball",
                "ice_beam_cast", "speed_dash"}
KINDS = {"Ability": "ability", "Scenario": "scenario", "Item": "item", "Mark": "mark", "Actor": "actor"}
ROW_KEYS = {"ability", "scenario", "args", "values", "refuse", "note"}
ID = re.compile(r"^[a-z][a-z0-9_]*$")


def made_actions():
    """made/main's action names from its folder; None when the folder is not here."""
    folder = os.path.join(MADE, "actions") if MADE else None
    if not folder or not os.path.isdir(folder):
        return None
    return {f[:-3] for f in os.listdir(folder) if f.endswith(".py") and not f.startswith("_")}


def made_content():
    """made/main/content/, where made/main's own classes are written; None until it exists."""
    folder = os.path.join(MADE, "content") if MADE else None
    return folder if folder and os.path.isdir(folder) else None


def old_actions():
    """The old library's actions a binding could name, from git; the calling test is skipped when git
    cannot show them here."""
    try:
        r = subprocess.run(["git", "-C", REPO, "show", OLD + ":actions/"], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise unittest.SkipTest("git is not available here to read %s:actions/: %s" % (OLD, exc))
    if r.returncode != 0:
        raise unittest.SkipTest("git cannot show %s:actions/ here: %s" % (OLD, r.stderr.strip()[:200]))
    names = {line.strip()[:-3] for line in r.stdout.splitlines() if line.strip().endswith(".py")}
    return {n for n in names if n not in NOT_BINDABLE and not n.startswith("engine_")}


def _literal(node):
    """A Literal["a", "b"] annotation's options as a tuple; None for any other annotation."""
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Literal"):
        elts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if all(isinstance(e, ast.Constant) for e in elts):
            return tuple(e.value for e in elts)
    return None


def _fields(cls, module_classes, seen):
    """An Args class's annotated fields, {name: Literal options or None}, with those of a base Args
    the same file declares (one imported from vfg_engine adds none)."""
    out = {}
    for base in cls.bases:
        name = base.id if isinstance(base, ast.Name) else None
        if name in module_classes and name not in seen:
            out.update(_fields(module_classes[name], module_classes, seen | {name}))
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = _literal(node.annotation)
    return out


def read_class(path):
    """(kind, args) of the one content class in a file: kind "ability", "scenario"... by its base
    class's name; args its Args' fields as {name: Literal options or None} ({} with no Args class)."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    module_classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    found = [(n, KINDS[b.id]) for n in tree.body if isinstance(n, ast.ClassDef)
             for b in n.bases if isinstance(b, ast.Name) and b.id in KINDS]
    if len(found) != 1:
        raise AssertionError("%s holds %d content classes, not one" % (path, len(found)))
    cls, kind = found[0]
    args = next((n for n in cls.body if isinstance(n, ast.ClassDef) and n.name == "Args"), None)
    return kind, (_fields(args, module_classes, {args.name}) if args is not None else {})


def class_file(action, cid):
    """Where the row's class is: engine/library/<id>.py, else made/main/content/<id>.py for a made/main
    row (None while that folder does not exist yet), else ""."""
    lib = os.path.join(LIBRARY, cid + ".py")
    if os.path.isfile(lib):
        return lib
    if action in (made_actions() or MADE_ACTIONS):
        content = made_content()
        return os.path.join(content, cid + ".py") if content else None
    return ""


def targets(rule):
    """The arg a values rule writes, or None for a value dropped on purpose."""
    return rule[0] if isinstance(rule, (list, tuple)) else rule


class TestRows(unittest.TestCase):
    def test_every_row_is_one_kind_with_only_the_keys_the_format_has(self):
        for action, row in MAP.items():
            with self.subTest(action=action):
                self.assertTrue(ID.match(action), action)
                self.assertLessEqual(set(row), ROW_KEYS)
                kinds = [k for k in ("ability", "scenario", "refuse") if row.get(k)]
                self.assertEqual(len(kinds), 1, "a row is exactly one of ability, scenario or refuse: %s" % kinds)
                if not row.get("scenario"):
                    self.assertNotIn("args", row)
                    self.assertNotIn("values", row)
                if not row.get("refuse"):
                    self.assertTrue(ID.match(row.get("ability") or row.get("scenario")))
                if "note" in row:
                    self.assertTrue(isinstance(row["note"], str) and row["note"].strip(), row.get("note"))
                self.assertIsInstance(row.get("args", {}), dict)
                self.assertIsInstance(row.get("values", {}), dict)

    def test_a_refusal_is_a_full_sentence_saying_what_to_do_instead(self):
        for action, row in MAP.items():
            if not row.get("refuse"):
                continue
            words = row["refuse"]
            with self.subTest(action=action):
                self.assertIsInstance(words, str)
                self.assertEqual(words, words.strip())
                self.assertNotIn("\n", words)
                self.assertTrue(words[0].isalpha(), words)
                self.assertTrue(words.endswith("."), "a sentence ends with a full stop: %r" % words)
                self.assertGreaterEqual(len(words.split()), 10, words)
                self.assertTrue("engine_" in words or "by hand" in words,
                                "it says what to do instead (an engine_ action, or bind by hand): %r" % words)

    def test_a_values_rule_is_a_rename_a_translation_or_dropped(self):
        for action, row in MAP.items():
            for field, rule in (row.get("values") or {}).items():
                with self.subTest(action=action, field=field):
                    if rule is None or isinstance(rule, str):
                        continue
                    self.assertIsInstance(rule, (list, tuple))
                    self.assertEqual(len(rule), 2)
                    self.assertIsInstance(rule[0], str)
                    self.assertTrue(isinstance(rule[1], dict) and rule[1])
                    for key in rule[1]:
                        self.assertIsInstance(key, (str, int, float, bool))

    def test_every_row_names_a_class_of_its_kind(self):
        for action, row in MAP.items():
            if row.get("refuse"):
                continue
            kind = "ability" if row.get("ability") else "scenario"
            cid = row[kind]
            with self.subTest(action=action, id=cid):
                path = class_file(action, cid)
                if path is None:
                    self.skipTest("made/main/content/ is not written yet: %s's %s %s is checked once it is"
                                  % (action, kind, cid))
                self.assertTrue(path, "%s's %s %s is not in engine/library" % (action, kind, cid))
                self.assertTrue(os.path.isfile(path), "%s's %s %s: no %s" % (action, kind, cid, path))
                self.assertEqual(read_class(path)[0], kind, "%s is not a %s" % (path, kind))

    def test_a_scenario_rows_args_and_values_are_args_of_that_scenario(self):
        for action, row in MAP.items():
            if not row.get("scenario"):
                continue
            with self.subTest(action=action, scenario=row["scenario"]):
                path = class_file(action, row["scenario"])
                if not path or not os.path.isfile(path):
                    self.skipTest("no class file for %s here (the test above says why)" % row["scenario"])
                fields = read_class(path)[1]
                for name, value in (row.get("args") or {}).items():
                    self.assertIn(name, fields, "%s takes no arg %r (it takes: %s)"
                                  % (row["scenario"], name, ", ".join(fields) or "none"))
                    if fields[name] is not None:
                        self.assertIn(value, fields[name], "%s=%r is not one of %s" % (name, value, fields[name]))
                for field, rule in (row.get("values") or {}).items():
                    arg = targets(rule)
                    if arg is None:
                        continue
                    self.assertIn(arg, fields, "%s -> %s: %s takes no arg %r (it takes: %s)"
                                  % (field, arg, row["scenario"], arg, ", ".join(fields) or "none"))
                    if isinstance(rule, (list, tuple)) and fields[arg] is not None:
                        for old, new in rule[1].items():
                            self.assertIn(new, fields[arg], "%s=%r -> %s=%r is not one of %s"
                                          % (field, old, arg, new, fields[arg]))

    def test_every_old_library_action_has_a_row(self):
        old = old_actions()
        self.assertGreater(len(old), 60)
        self.assertEqual(sorted(old - set(MAP)), [], "old actions with no row are left behind")

    def test_every_made_main_action_has_a_row(self):
        made = made_actions()
        if made is None:
            self.skipTest("made/main/actions/ is not here (it is untracked, kept in External/made/main)")
        self.assertEqual(sorted(made - set(MAP)), [], "made/main actions with no row are left behind")

    def test_no_row_is_for_an_action_that_never_was(self):
        old = old_actions()
        made = made_actions() or MADE_ACTIONS
        self.assertEqual(sorted(set(MAP) - old - made), [])


# KrownJM's three as the PC's cache held them (inventory G4), their values given whole.
FREEZE = {"around_radius": 16, "around_what": "everything", "around_most": 0, "freeze_seconds": 5,
          "freeze_blindness": True, "freeze_shell": False, "freeze_block": "ice", "damage_amount": 6,
          "damage_type": "player_attack", "damage_what": "all", "damage_kill": False, "damage_most": 0,
          "damage_head": 1, "damage_spare": []}
FLAME = {"aura_seconds": 10, "around_radius": 4, "around_what": "everything", "burn_seconds": 10,
         "damage_amount": 2, "damage_type": "on_fire", "flame_particles": 40}
FIREBALL = {"explosion_power": 3, "explosion_layout": "one", "projectile_speed": 40, "reach": 80,
            "fireball_width": 1.2}


class TestTranslate(unittest.TestCase):
    def test_krownjms_three_cast_their_abilities_and_their_values_stay_behind(self):
        for action, values, ability in (("blizzard_freeze", FREEZE, "blizzard"), ("fire_aura_krown", FLAME, "flame"),
                                        ("fireball_cast", FIREBALL, "fireball")):
            with self.subTest(action=action):
                entry = {"player": "KrownJM", "action": action, "values": dict(values, player=""),
                         "caster_fields": ["player"]}
                kind, cid, args, notes = translate(action, entry)
                self.assertEqual((kind, cid, args), ("ability", ability, {}))
                self.assertEqual(len(notes), 1, notes)
                self.assertTrue(notes[0].startswith("its values {") and notes[0].endswith(
                    "stay behind: %s's Config is one version for everyone" % ability), notes[0])
                self.assertNotIn('"player"', notes[0])          # the caster's field is not a value left behind
                # as the store keeps them: nothing asked, so nothing stays behind
                self.assertEqual(translate(action, dict(entry, asked={})), ("ability", ability, {}, []))

    def test_a_scenario_row_renames_translates_sets_its_recipe_and_fills_the_caster(self):
        entry = {"player": "Steve", "action": "three_headed_dragon", "caster_fields": ["player"],
                 "values": {"player": "", "height": 50, "boss_health": 300, "boss_music": "relic",
                            "boss_attack": 20},
                 "asked": {"height": 50, "boss_health": 300, "boss_music": "relic", "boss_attack": 20}}
        kind, cid, args, notes = translate("three_headed_dragon", entry)
        self.assertEqual((kind, cid), ("scenario", "boss_fight"))
        self.assertEqual(args, {"boss": "dragon_head", "bodies": 3, "height": 50, "music": "boss",
                                "griefing": False, "name": "Three-Headed Dragon", "health": 300, "near": "caster"})
        self.assertEqual(notes, ['its values {"boss_attack":20} stay behind: boss_fight has no arg for them'])

    def test_a_translated_value_and_a_value_dropped_on_purpose(self):
        kind, cid, args, notes = translate("tether", {"asked": {"leaves_area": "radius", "leaves_radius": 12},
                                                      "caster_fields": ["player"]})
        self.assertEqual((kind, cid, args, notes),
                         ("scenario", "arena", {"shape": "radius", "radius": 12, "message": "Stay with the group",
                                                "players": "caster"}, []))
        got = translate("sky_platform", {"asked": {"shape_facing": "north", "platform_y": 150}})
        self.assertEqual(got, ("scenario", "sky_platform", {"y": 150}, []))

    def test_summon_arounds_one_mob_is_hordes_list_of_one(self):
        kind, cid, args, notes = translate("summon_around", {"asked": {"mob": "minecraft:creeper", "count": 5,
                                                                       "players": "Alex"}})
        self.assertEqual((kind, cid, notes), ("scenario", "horde", []))
        self.assertEqual(args, {"waves": 1, "stay": True, "announce": False, "per_player": 5, "spread": 16,
                                "mobs": ["creeper"], "players": "Alex"})

    def test_a_value_its_table_lacks_is_refused(self):
        with self.assertRaises(Refused) as caught:
            translate("arena", {"asked": {"leaves_area": "sphere"}, "caster_fields": ["player"]})
        self.assertEqual(str(caught.exception), "leaves_area=sphere has no arena shape in the engine: bind it again "
                                                "by hand")
        with self.assertRaises(Refused):
            translate("summon_around", {"asked": {"mob": "minecart"}})

    def test_a_refused_row_says_its_words(self):
        for action in ("railgun", "keep_effects", "attribute_guard"):
            with self.subTest(action=action):
                with self.assertRaises(Refused) as caught:
                    translate(action, {"asked": {"cooldown": 1}, "caster_fields": ["player"]})
                self.assertEqual(str(caught.exception), MAP[action]["refuse"])

    def test_an_action_with_no_row_is_refused_never_guessed(self):
        with self.assertRaises(Refused) as caught:
            translate("no_such_action", {"values": {}})
        self.assertEqual(str(caught.exception), "no_such_action has no engine class in the migration table")
        self.assertIs(migrate_map.Refused, Refused)


if __name__ == "__main__":
    unittest.main()
