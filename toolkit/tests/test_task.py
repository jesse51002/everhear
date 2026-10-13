"""vfg task set and vfg task list, and the tasks object of vfg context --json: the brain's tasks
table in the box's vfg.db (Prototype_Named_Tasks). No EC2, no network.

    python3 -m unittest discover -s toolkit/tests

harness.py's box has the table as the bridge makes it; Box(..., tasks=False) is a box whose bridge
has not added it yet. vfg brain status's read is test_vfg.py's, vfg route --task's test_route.py's.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ISO_RE, BoxCase, iso  # noqa: E402

RETIRED = "T%d" % 7   # a task as it was named before named tasks: refused now
EMPTY = {"content": [], "bindings": [], "grants": []}
VARIABLES = [{"name": "cooldown", "type": "number", "default": 5, "ge": 0, "le": 60, "choices": None,
              "live": True, "fixed": False, "description": "seconds between shots"}]


def galick(**more):
    """The whole row the brain sends for a new task, with `more` over it."""
    return dict({"name": "galick_gun_styrk", "description": "A Galick Gun for Styrkaeopteryx: charge and fire a beam",
                 "player": "Styrkaeopteryx", "status": "open", "line": "make me a galick gun",
                 "brief": "a Galick Gun: charge with right-click and fire a beam", "made": EMPTY,
                 "variables": None, "session": None, "result": None, "created_at": "2026-10-11T18:22:33.12Z",
                 "updated_at": "2026-10-11T18:22:33.120Z", "closed_at": None}, **more)


class TaskCase(BoxCase):
    def set(self, task, **kw):
        return self.vfg("task", "set", task if isinstance(task, str) else json.dumps(task), **kw)

    def row(self, name="galick_gun_styrk"):
        got = [t for t in self.box.tasks() if t["name"] == name]
        return got[0] if got else None

    def raw(self, column, name="galick_gun_styrk"):
        return self.box.query("SELECT %s FROM tasks WHERE name = ?" % column, name)[0][0]


class TestSet(TaskCase):
    def test_an_insert_then_an_update_of_the_keys_given_each_one_call(self):
        r = self.set(galick())
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "added galick_gun_styrk\n", ""))
        self.assertEqual(len(self.box.calls), 1)
        args = self.box.programs()[0]
        self.assertEqual((args["op"], args["library_action"]), ("set", False))
        self.assertEqual(args["task"]["created_at"], "2026-10-11T18:22:33.120Z")   # normalised on the PC
        self.assertEqual(self.row(), dict(galick(), created_at="2026-10-11T18:22:33.120Z"))
        self.assertEqual(self.raw("made"), '{"content":[],"bindings":[],"grants":[]}')
        # An update writes the keys given and keeps the rest; name and created_at never change.
        r = self.set({"name": "galick_gun_styrk", "description": "A Galick Gun that pierces blocks",
                      "made": {"content": ["galick_gun_styrk"], "bindings": ["styrkaeopteryx.galick"],
                               "grants": ["Styrkaeopteryx:galick_gun_styrk"]}, "variables": VARIABLES,
                      "session": "a-session",
                      "created_at": "2030-01-01T00:00:00Z", "updated_at": "2026-10-11T18:23:40.002Z"})
        self.assertEqual((r.returncode, r.stdout), (0, "updated galick_gun_styrk\n"), r.stderr)
        self.assertEqual(len(self.box.calls), 2)
        got = self.row()
        self.assertEqual((got["description"], got["session"], got["variables"], got["made"]["grants"]),
                         ("A Galick Gun that pierces blocks", "a-session", VARIABLES, ["Styrkaeopteryx:galick_gun_styrk"]))
        self.assertEqual((got["created_at"], got["updated_at"]), ("2026-10-11T18:22:33.120Z", "2026-10-11T18:23:40.002Z"))
        self.assertEqual((got["brief"], got["line"], got["player"], got["status"]),
                         (galick()["brief"], "make me a galick gun", "Styrkaeopteryx", "open"))
        self.assertEqual(json.loads(self.raw("variables")), VARIABLES)
        self.assertNotIn(", ", self.raw("variables"))                               # stored compact
        # With no updated_at given, the update's time is now.
        before = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(self.set({"name": "galick_gun_styrk", "brief": "make the beam pierce blocks"}).stdout,
                         "updated galick_gun_styrk\n")
        self.assertRegex(self.row()["updated_at"], ISO_RE)
        self.assertGreater(self.row()["updated_at"], before)

    def test_a_new_task_gets_its_defaults_and_needs_a_description(self):
        before = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(self.set({"name": "castle_steve", "description": "A castle"}).stdout, "added castle_steve\n")
        got = self.row("castle_steve")
        self.assertEqual((got["status"], got["made"], got["variables"], got["player"], got["closed_at"]),
                         ("open", None, None, None, None))
        self.assertEqual(got["created_at"], got["updated_at"])
        self.assertGreater(got["created_at"], before)
        r = self.set({"name": "ice_trail_steve", "player": "Steve"})
        self.assertOneLine(r, 1, "^vfg: a new task needs a description$")
        self.assertIsNone(self.row("ice_trail_steve"))

    def test_an_older_write_is_kept_out(self):
        self.set(galick(updated_at="2026-10-11T18:30:00.000Z"))
        r = self.set({"name": "galick_gun_styrk", "description": "older", "updated_at": "2026-10-11T18:29:59.999Z"})
        self.assertEqual((r.returncode, r.stdout), (0, "kept galick_gun_styrk: a newer write is there\n"))
        self.assertEqual((self.row()["description"], self.row()["updated_at"]),
                         (galick()["description"], "2026-10-11T18:30:00.000Z"))
        r = self.set({"name": "galick_gun_styrk", "description": "the same time", "updated_at": "2026-10-11T18:30:00Z"})
        self.assertEqual(r.stdout, "updated galick_gun_styrk\n")
        self.assertEqual(self.row()["description"], "the same time")

    def test_closed_at_follows_the_status(self):
        self.set(galick())
        self.set({"name": "galick_gun_styrk", "status": "done", "result": "galick_gun_styrk done: it fires"})
        closed = self.row()["closed_at"]
        self.assertRegex(closed, ISO_RE)
        self.assertEqual(self.row()["result"], "galick_gun_styrk done: it fires")
        self.set({"name": "galick_gun_styrk", "status": "failed"})            # closed already: kept
        self.assertEqual((self.row()["status"], self.row()["closed_at"]), ("failed", closed))
        self.set({"name": "galick_gun_styrk", "status": "open", "brief": "make it pierce blocks"})   # a follow-up
        self.assertEqual((self.row()["status"], self.row()["closed_at"]), ("open", None))
        self.set({"name": "galick_gun_styrk", "status": "done", "closed_at": "2026-10-11T19:00:00Z"})
        self.assertEqual(self.row()["closed_at"], "2026-10-11T19:00:00.000Z")   # given: as given
        self.set({"name": "castle_steve", "description": "A castle", "status": "failed"})
        self.assertRegex(self.row("castle_steve")["closed_at"], ISO_RE)      # a new row given closed

    def test_a_library_class_or_an_actions_name_is_refused_for_a_new_task_only(self):
        self.box.write("engine/library/railgun.py", "# the library's railgun\n")
        r = self.set({"name": "railgun", "description": "Steve's railgun"})
        self.assertOneLine(r, 1, "^vfg: railgun is a library class's or an action's name$")
        self.assertTrue(self.box.programs()[-1]["library_action"])
        self.box.write("actions/engine_grant.py", "# an action\n")
        self.assertOneLine(self.set({"name": "engine_grant", "description": "x"}), 1, "an action's name$")
        self.assertEqual(self.box.tasks(), [])
        self.box.add_task("railgun", "a row from before the class came")
        self.assertEqual(self.set({"name": "railgun", "description": "changed"}).stdout, "updated railgun\n")
        self.box.write("made/main/content/frost_bow_alex.py", "# a made class: the task's own\n")
        self.assertEqual(self.set({"name": "frost_bow_alex", "description": "a bow"}).stdout, "added frost_bow_alex\n")

    def test_bad_input_exits_2_before_any_ssh_call(self):
        cases = [(galick(name=RETIRED), "is not a task's name"), (galick(name="cast"), "the bridge's task"),
                 (galick(name="Galick"), "not a task's name"), (galick(name="g"), "not a task's name"),
                 (galick(name="a" * 33), "not a task's name"), (galick(name="7gun"), "not a task's name"),
                 (galick(name="gun_a\n"), "not a task's name"), (galick(player="Steve\n"), "player"),
                 (dict(galick(), colour="red"), "colour is not a task's key"), ({"description": "x"}, "its name"),
                 (galick(status="running"), "status is open, done, failed"), (galick(description=""), "description"),
                 (galick(description=None), "description"), (galick(player="not a name"), "player"),
                 (galick(line=7), "line is a text"), (galick(made={"content": "x"}), "made is null"),
                 (galick(made={"scripts": []}), "made is null"), (galick(made=[]), "made is null"),
                 (galick(variables={"cooldown": 5}), "variables"), (galick(variables=[1]), "variables"),
                 (galick(updated_at="yesterday"), "updated_at: bad time"), (galick(closed_at=5), "closed_at is a time"),
                 ("not json", "one JSON object"), ("[1, 2]", "one JSON object, not list"), ("", "one JSON object")]
        for task, pattern in cases:
            self.assertOneLine(self.set(task), 2, pattern)
        self.assertEqual(self.box.calls, [])
        os.remove(os.path.join(self.box.vhome, "config"))
        self.assertOneLine(self.set(galick()), 3, "not configured")

    def test_stdin(self):
        r = self.vfg("task", "set", "-", input=json.dumps(galick(description="A Galick Gun — from stdin")))
        self.assertEqual((r.returncode, r.stdout), (0, "added galick_gun_styrk\n"), r.stderr)
        self.assertEqual(self.row()["description"], "A Galick Gun — from stdin")
        self.assertOneLine(self.vfg("task", "set", "-", input="{"), 2, "one JSON object")


class TestList(TaskCase):
    def setUp(self):
        TaskCase.setUp(self)
        self.box.add_task("ice_trail_steve", "An ice trail behind Steve\nfor a minute", player="Steve", minutes_ago=8,
                          made={"content": ["ice_trail_steve"], "bindings": [], "grants": []},
                          variables=VARIABLES)
        self.box.add_task("castle_alex", "A castle at spawn, 40 by 40", status="done", player="Alex", minutes_ago=6,
                          result="castle_alex done: built")
        self.box.add_task("long_one", "y" * 130, minutes_ago=2)

    def lines(self, *args):
        r = self.vfg("task", "list", *args)
        self.assertOk(r)
        return r.stdout.splitlines()

    def test_plain_open_and_by_name(self):
        self.assertEqual(self.lines(), ["ice_trail_steve open Steve: An ice trail behind Steve for a minute",
                                        "castle_alex done Alex: A castle at spawn, 40 by 40",
                                        "long_one open -: " + "y" * 119 + "…"])
        self.assertEqual(self.lines("--open"), [self.lines()[0], self.lines()[2]])
        self.assertEqual(self.lines("--name", "castle_alex"), [self.lines()[1]])
        self.assertEqual(self.lines("--name", "castle_alex", "--open"), ["none"])
        self.assertEqual(self.lines("--name", "nobody_here"), ["none"])
        self.assertEqual(self.box.programs()[-1], {"op": "list", "open": False, "name": "nobody_here", "json": False,
                                                   "vfg": self.box.rvfg, "mc": self.box.rmc})

    def test_json_rows_with_made_and_variables_decoded(self):
        rows = json.loads(self.vfg("task", "list", "--json").stdout)
        self.assertEqual([r["name"] for r in rows], ["ice_trail_steve", "castle_alex", "long_one"])
        self.assertEqual(list(rows[0]), ["name", "description", "player", "status", "line", "brief", "made", "variables",
                                         "session", "result", "created_at", "updated_at", "closed_at"])
        self.assertEqual(rows, self.box.tasks())
        self.assertEqual((rows[0]["made"]["content"], rows[0]["variables"]), (["ice_trail_steve"], VARIABLES))
        self.assertEqual((rows[1]["made"], rows[1]["result"]), (None, "castle_alex done: built"))
        con = __import__("sqlite3").connect(self.box.db)
        con.execute("UPDATE tasks SET made = 'not json', variables = '[1,' WHERE name = 'long_one'")
        con.commit()
        con.close()
        got = json.loads(self.vfg("task", "list", "--json", "--open", "--name", "long_one").stdout)
        self.assertEqual([(r["made"], r["variables"]) for r in got], [(None, None)])

    def test_none_and_bad_names(self):
        con = __import__("sqlite3").connect(self.box.db)
        con.execute("DELETE FROM tasks")
        con.commit()
        con.close()
        self.assertEqual(self.lines(), ["none"])
        self.assertEqual(self.vfg("task", "list", "--json").stdout, "[]\n")
        before = len(self.box.calls)
        for name in (RETIRED, "cast", "Castle", "x"):
            self.assertOneLine(self.vfg("task", "list", "--name", name), 2)
        self.assertEqual(len(self.box.calls), before)


class TestContextTasks(TaskCase):
    def test_context_json_holds_every_task_before_the_last_seq(self):
        self.box.add_task("castle_alex", "A castle", status="done", player="Alex", minutes_ago=600)  # older than 6 h
        self.box.add_task("galick_gun_styrk", "A Galick Gun", player="Styrkaeopteryx", made=EMPTY, minutes_ago=3)
        r = self.vfg("context", "--json")
        self.assertOk(r)
        objs = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual(objs[-1], {"last_seq": 3})
        self.assertEqual(objs[-2], {"tasks": json.loads(self.vfg("task", "list", "--json").stdout)})
        self.assertEqual([t["name"] for t in objs[-2]["tasks"]], ["castle_alex", "galick_gun_styrk"])
        self.assertEqual([o["seq"] for o in objs[:-2]], [1, 2, 3])
        self.assertEqual(len(self.box.programs("read")), 1)                     # one SSH call
        self.assertEqual(len(self.vfg("context").stdout.splitlines()), 3)      # a plain read: the lines alone


class TestNoTasksTable(TaskCase):
    """A box whose bridge has not added the table yet (not redeployed since named tasks)."""
    box_kwargs = {"tasks": False}

    def test_every_read_and_write_of_it_says_so_and_a_plain_context_reads_on(self):
        why = "^vfg: vfg.db has no tasks table yet — restart vfg-bridge, which adds it$"
        self.assertOneLine(self.set(galick()), 1, why)
        self.assertOneLine(self.vfg("task", "list"), 1, why)
        self.assertOneLine(self.vfg("context", "--json"), 1, why)
        self.assertEqual(self.vfg("context").stdout.count("\n"), 3)
        os.remove(self.box.db)
        self.assertOneLine(self.vfg("task", "list"), 1, r"^vfg: no transcript history yet \(.*/vfg\.db missing\)$")


if __name__ == "__main__":
    unittest.main()
