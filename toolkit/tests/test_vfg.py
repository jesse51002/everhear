"""The vfg toolkit's commands, each at its edge: no EC2, no network, vfg in this process.

    python3 -m unittest discover -s toolkit/tests

harness.py has the box: vfg's main() runs here, and its remote programs run here as the box's
Python would. The actions and the copies they start: test_copies.py. The router: test_route.py.
The library (script new, check, preset save): test_library.py. vfg as a process: test_e2e.py.
"""
import base64
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (ISO_RE, PY, REAL_LIB, REPO, SCRIPTS_TEST, TESTS, TOOLKIT, VFG, Box, BoxCase,  # noqa: E402
                     alive, iso, load_module, vfg_module, wait_for, write)
import standins  # noqa: E402


def feed_lines(box):
    with open(box.feed) as f:
        return f.read().splitlines()


# ===================================================================== basics

class TestBasics(BoxCase):
    def test_help_lists_every_command(self):
        r = self.vfg("--version")
        self.assertEqual((r.returncode, r.stdout), (0, "vfg 0.1.0\n"))
        r = self.vfg("--help")
        self.assertOk(r)
        for c in ("up", "watch", "say", "cmd", "history", "context", "watchers", "lookout", "brain", "jev",
                  "feed", "script", "preset", "state", "route", "prefix", "action", "made", "place", "usage"):
            self.assertRegex(r.stdout, r"\n\s+%s\s" % c)
        self.assertNotRegex(r.stdout, r"\n\s+orch\s")
        for args, words in ((["brain", "--help"], ["start", "stop", "status"]),
                            (["context", "--help"], ["--since", "--json", "mark"]),
                            (["jev", "menu", "--help"], ["set", "show"]),
                            (["watch", "--help"], ["--tail", "--after"]),
                            (["say", "--help"], ["--to", "--task"]),
                            (["state", "--help"], ["--json", "--items"]),
                            (["feed", "post", "--help"], ["LINE, GAME, AI or SYS"]),
                            (["action", "run", "--help"], ["key=value", "as=<copy>", "--task", "JSON line"]),
                            (["script", "--help"], ["new", "check", "deploy", "list", "set", "stop", "logs", "save"]),
                            (["history", "--help"], ["--since", "--until", "--player", "--class", "--source",
                                                     "--events", "--feed", "--limit"]),
                            (["usage", "add", "--help"], ["--component", "--cache-read", "--ref"])):
            r = self.vfg(*args)
            self.assertOk(r)
            for w in words:
                self.assertIn(w, r.stdout, args)
        for gone in (["orch", "status"], ["script", "run", "hello"]):
            self.assertOneLine(self.vfg(*gone), 2)

    def test_not_configured_exits_3_without_naming_the_key(self):
        os.remove(self.box.key)
        r = self.vfg("history")
        self.assertOneLine(r, 3, r"^vfg: not configured: the key file VFG_SSH_KEY names is not readable$")
        self.assertNotIn(self.box.key, r.stderr)
        os.remove(os.path.join(self.box.vhome, "config"))
        for args in (["say", "hi"], ["history"], ["up"], ["context"], ["feed", "post", "SYS", "", "x"]):
            r = self.vfg(*args)
            self.assertOneLine(r, 3, r"^vfg: not configured: VFG_SSH_HOST missing in .*config$")
            self.assertEqual(r.stdout, "")
        self.assertEqual(self.box.calls, [])

    def test_environment_wins_over_config_and_the_repo_defaults_to_the_toolkits(self):
        self.assertOk(self.vfg("history", VFG_SSH_HOST="otherbox", VFG_SSH_USER="steve"))
        self.assertEqual(self.box.calls[-1]["dest"], "steve@otherbox")
        with self.box.environ(VFG_REPO=None) as mod:
            self.assertEqual(mod.Config().repo, os.path.dirname(os.path.realpath(TOOLKIT)))
        with self.box.environ() as mod:
            self.assertEqual(mod.Config().repo, self.box.repo)

    def test_ec2_unreachable_or_out_of_time_exits_4(self):
        self.box.down = True
        for args in (["say", "hi"], ["history"], ["up"]):
            r = self.vfg(*args)
            self.assertOneLine(r, 4, r"^vfg: cannot reach EC2 \(ec2-user@fakebox\): "
                                     r"ssh: connect to host fakebox port 22: Connection timed out$")
            self.assertEqual(r.stdout, "")
        self.box.down, self.box.slow = False, True
        self.assertOneLine(self.vfg("history"), 4, r"^vfg: EC2 did not answer within 30 s$")
        self.assertOneLine(self.vfg("--timeout", "1", "history"), 4, r"^vfg: EC2 did not answer within 1 s$")

    def test_bad_arguments_are_one_line_and_2(self):
        for args in (["say"], ["nosuch"], ["history", "--limit", "x"], ["usage", "add"], ["watchers"],
                     ["brain"], ["context", "mark"], ["watch", "--tail", "2", "--after", "3"]):
            self.assertOneLine(self.vfg(*args), 2)
        self.assertEqual(self.box.calls, [])

    def test_a_command_that_loads_the_actions_starts_again_under_uv_where_pydantic_is_missing(self):
        from unittest import mock
        with self.box.environ() as mod, mock.patch("importlib.util.find_spec", return_value=None), \
                mock.patch("os.execvpe") as execvpe:
            for argv in (["--timeout", "5", "action", "run", "ping"], ["history"], ["--version"]):
                mod.under_uv(argv)
            os.environ["VFG_UNDER_UV"] = "1"            # started again already: never a loop
            mod.under_uv(["cmd", "list"])
        (cmd, argv, env), = [c.args for c in execvpe.call_args_list]
        self.assertEqual((cmd, argv), ("uv", ["uv", "run", "--script", "--quiet", os.path.realpath(VFG),
                                              "--timeout", "5", "action", "run", "ping"]))
        self.assertEqual(env["VFG_UNDER_UV"], "1")

    def test_program_ships_libraries_as_modules(self):
        mod = vfg_module()

        def read(name):
            with open(os.path.join(TOOLKIT, "remote", name), encoding="utf-8") as f:
                return f.read()
        self.assertEqual(mod.program("rcon.py", {"a": 1}).decode(),
                         read("common.py") + "\nARGS = json.loads('{\"a\": 1}')\n\n" + read("rcon.py"))
        text = mod.program("place.py", {"op": "list"}, ["places.py"]).decode()
        self.assertTrue(text.endswith("\n" + read("place.py")))
        head = text[:-len(read("place.py"))]
        ns = {"__name__": "__main__"}
        saved = sys.modules.pop("places", None)
        try:
            exec(compile(head, "<program>", "exec"), ns)
            self.assertIs(sys.modules["places"], ns["places"])
            self.assertEqual(ns["places"].STORAGE, "everhear:places")
            self.assertNotIn("all", ns)  # places.all stays inside its module
        finally:
            sys.modules.pop("places", None)
            if saved is not None:
                sys.modules["places"] = saved

    def test_every_invocation_is_logged(self):
        self.vfg("--version")
        self.vfg("history", "--limit", "1")
        with open(os.path.join(self.box.vhome, "vfg.log")) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[1], r'^\d{4}-\d\d-\d\dT[\d:.]+Z \["history", "--limit", "1"\] exit=0 \d+ms$')


class TestSsh(BoxCase):
    """vfg's own Ssh against fake_ssh, which runs the remote command here with bash."""

    def call(self, **env):
        log = os.path.join(self.box.root, "ssh.jsonl")
        with self.box.environ(FAKE_SSH_LOG=log, **env) as mod:
            ssh = mod.RealSsh(mod.Config())
            try:
                got = ssh.call(mod.program("history.py", {"vfg": self.box.rvfg, "what": "feed", "limit": 1}),
                               float(env.get("TIMEOUT", 30)))
            except mod.Fail as f:
                got = f
        with open(log) as f:
            return got, [json.loads(l) for l in f]

    def test_a_call_out_of_time_is_cut_off(self):
        """The call through the master and its options: test_e2e.py, through vfg as a process."""
        got, calls = self.call(FAKE_SSH_DELAY="3", TIMEOUT="0.1")
        self.assertEqual((got.code, got.msg), (4, "EC2 did not answer within 0.1 s"))
        self.assertEqual(calls[-2][-2:], ["ec2-user@fakebox", self.box.rvfg + "/python/bin/python3 -u -"])
        self.assertEqual(calls[-1][-3:], ["-O", "exit", "ec2-user@fakebox"])   # a dead link's master is closed


class TestOneRefusedList(unittest.TestCase):
    """scripts/_lib/commands.py is the one list of refused server commands: mc.py imports it,
    vfg ships it with each program, the implementers' guard loads it, and no other file
    writes the words out again."""
    COMMANDS = os.path.join(REPO, "scripts", "_lib", "commands.py")

    def test_mc_imports_it_and_vfg_ships_it(self):
        code = "import mc, commands; print(mc.REFUSED is commands.REFUSED, commands.__file__)"
        env = dict(os.environ, PYTHONPATH=REAL_LIB, PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([PY, "-c", code], env=env, stdout=subprocess.PIPE, universal_newlines=True,
                             cwd=TESTS).stdout.split()
        self.assertEqual(out, ["True", self.COMMANDS])
        with open(self.COMMANDS, encoding="utf-8") as f:
            self.assertIn(repr(f.read()), vfg_module().program("rcon.py", {}, ["commands.py"]).decode())

    def test_the_implementers_guard_loads_it(self):
        import ast
        folder = os.path.join(REPO, "brain", "brain", "implementers")
        path = os.path.join(folder, "guard.py")
        if not os.path.isfile(path):
            self.skipTest("no brain/brain/implementers/guard.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == "refused")
                or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_COMMANDS")]
        self.assertEqual(len(keep), 2)
        root = load_module("implementers_config", os.path.join(folder, "config.py")).ROOT
        self.assertEqual(os.path.realpath(root), os.path.realpath(REPO))
        ns = {"os": os, "__file__": path, "ROOT": root}
        exec(compile(ast.Module(body=keep, type_ignores=[]), path, "exec"), ns)
        self.assertEqual(ns["refused"](), load_module("one_list_commands", self.COMMANDS).REFUSED)

    def test_no_other_file_writes_the_words_out(self):
        words = re.compile(r"frozenset\(\[[^\]]*\"deop\"")
        found = []
        for top in ("scripts", "toolkit", "brain", "actions", "lib", "jev", "integration"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, top)):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
                for fn in filenames:
                    if fn.endswith(".py") or "." not in fn:
                        try:
                            with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                                if words.search(f.read()):
                                    found.append(os.path.relpath(os.path.join(dirpath, fn), REPO))
                        except (OSError, UnicodeDecodeError):
                            continue
        self.assertEqual(found, ["scripts/_lib/commands.py"])


# ======================================================================= cmd

class TestCmd(BoxCase):
    """vfg cmd is the command action: its stand-in (standins.py) runs on the real kit."""

    def setUp(self):
        BoxCase.setUp(self)
        standins.install(self.box.repo, ["command"])

    def sent(self):
        return [c for c in self.box.rcon_commands() if c != "kill @e[tag=vfg_command]"]

    def test_the_command_as_written_and_its_reply(self):
        r = self.vfg("cmd", "/time", "set", "day")
        self.assertEqual((r.returncode, r.stdout), (0, "Set the time to 1000\n"), r.stderr)
        text = """bogus @a ~ ~10 ~ he said "hi" $x $(id) `id` 'q' \\n ;|&"""
        r = self.vfg("cmd", text)
        self.assertOneLine(r, 1, "^vfg: minecraft rejected the command$")
        self.assertIn("Unknown or incomplete command", r.stdout)
        self.assertEqual(self.sent(), ["time set day", text])

    def test_refused_before_anything_is_sent(self):
        self.assertOneLine(self.vfg("cmd", "tp", "@a", self.box.home, "~", "~"), 2, "your shell expanded a ~")
        self.assertOneLine(self.vfg("cmd", "tp", "@a", self.box.home + "/10"), 2)
        self.assertOneLine(self.vfg("cmd", "say " + "x" * 1496), 1,
                           r"^vfg: command is 1500 bytes; RCON takes at most 1400 — put it in a script$")
        self.assertOneLine(self.vfg("cmd"), 2)
        self.assertOneLine(self.vfg("cmd", "/"), 2)
        self.assertEqual((self.box.rcon_commands(), self.box.calls), ([], []))

    def test_minecraft_down_exits_5(self):
        port = self.box.rcon_down()
        self.assertOneLine(self.vfg("cmd", "list"), 5,
                           r"^vfg: minecraft is not running \(RCON on 127\.0\.0\.1:%d refused\)$" % port)
        self.box.write_props(enable=False)
        self.assertOneLine(self.vfg("cmd", "list"), 5, "minecraft is not running")
        self.box.write_props(password="not-the-password")
        r = self.vfg("cmd", "list")
        self.assertOneLine(r, 5, "^vfg: RCON refused the password$")
        self.assertNotIn(self.box.password, r.stdout + r.stderr)

    def test_a_long_reply_is_read_whole(self):
        """The RCON client every remote program has (common.py) reads a reply in three packets."""
        ns = {"__name__": "vfg_remote_common"}
        with open(os.path.join(TOOLKIT, "remote", "common.py")) as f:
            exec(compile(f.read(), "common.py", "exec"), ns)
        r = ns["Rcon"](self.box.rmc)
        try:
            got = r.command("big")
        finally:
            r.close()
        self.assertEqual((len(got), got[:10], got[-5:]), (9000, "0000 0001 ", "1799 "))


# ======================================================================= say

class TestSay(BoxCase):
    def ai_rows(self):
        return self.box.query("SELECT id, at, player_uuid, player_name, source, text, jev_class,"
                              " ended_at, client_line_id FROM lines WHERE source = 'ai' AND id != 'L4'")

    def tellraw(self, target, text, prefix="[AI] "):
        return 'tellraw %s [{"text":""},{"text":%s,"color":"aqua"},{"text":%s}]' % (
            target, json.dumps(prefix), json.dumps(text, ensure_ascii=False))

    def test_tellraw_the_ai_line_and_the_feed_row_in_one_call(self):
        before = datetime.now(timezone.utc)
        r = self.vfg("say", 'hi ~ "there"')
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(self.box.rcon_commands(), [self.tellraw("@a", 'hi ~ "there"')])
        rows = self.ai_rows()
        self.assertEqual(len(rows), 1)
        rid, at, uuid, name, source, text, cls, ended, client = rows[0]
        self.assertRegex(rid, r"^[0-9A-HJKMNP-TV-Z]{26}$")
        self.assertRegex(at, ISO_RE)
        self.assertGreaterEqual(at, iso(before - timedelta(seconds=1)))
        self.assertEqual((uuid, name, source, text, cls, ended, client), (None, "AI", "ai", 'hi ~ "there"', None, None, None))
        self.assertRegex(feed_lines(self.box)[-1], r'^AI#4 \d\d:\d\d:\d\d say - \| hi ~ "there"$')
        self.assertEqual(self.box.query("SELECT seq, at FROM feed WHERE seq = 4"), [(4, at)])
        self.assertEqual(len(self.box.calls), 1)

    def test_a_whisper_about_a_task(self):
        self.assertOk(self.vfg("say", "your base is ready", "--to", "Steve", "--task", "T7"))
        self.assertEqual(self.box.rcon_commands(), [self.tellraw("Steve", "your base is ready")])
        self.assertEqual(self.ai_rows()[0][5], "(to Steve) your base is ready")
        self.assertRegex(feed_lines(self.box)[-1], r"^AI#4 \d\d:\d\d:\d\d say T7 \| \(to Steve\) your base is ready$")
        for args in (["x", "--to", "not a name"], ["x", "--task", "7"], ["x", "--task"], ["hi", "--to"],
                     ["--to", "Steve"], [" \n "]):
            self.assertOneLine(self.vfg("say", *args), 2)
        self.assertEqual(len(feed_lines(self.box)), 4)

    def test_every_word_is_the_text_wherever_the_flags_sit(self):
        self.box.write_config("VFG_SSH_HOST=fakebox\nVFG_SSH_KEY=%s\nVFG_CHAT_PREFIX=\"<Bot> \"\n" % self.box.key)
        for args, target, text, task in (
                (["can", "t", "do", "that", "right", "now"], "@a", "can t do that right now", "-"),
                (["hello", "--to", "Steve", "world"], "Steve", "hello world", "-"),
                (["on", "it", "--to=Steve", "--task=T3"], "Steve", "on it", "T3"),
                (["--task", "T4", "your", "base"], "@a", "your base", "T4"),
                (["--", "--to", "Steve"], "@a", "--to Steve", "-"),
                (["-5", "hearts"], "@a", "-5 hearts", "-"),
                (["  one\ntwo\tthree — ok\r\n "], "@a", "one two three — ok", "-")):
            self.box.server.reset()
            self.assertOk(self.vfg("say", *args))
            self.assertEqual(self.box.rcon_commands(), [self.tellraw(target, text, "<Bot> ")])
            self.assertIn(" say %s | " % task, feed_lines(self.box)[-1])

    def test_a_long_text_is_split_in_order_stored_and_fed_once(self):
        text = " ".join("w%04d" % i for i in range(500)) + "!"
        self.assertEqual(len(text), 3000)
        self.assertOk(self.vfg("say", text))
        cmds = self.box.rcon_commands()
        self.assertGreater(len(cmds), 1)
        parts = []
        for c in cmds:
            self.assertLessEqual(len(c.encode("utf-8")), 1400)
            m = re.match(r'^tellraw @a \[\{"text":""\},\{"text":"\[AI\] ","color":"aqua"\},\{"text":(".*")\}\]$', c)
            parts.append(json.loads(m.group(1)))
        self.assertEqual(" ".join(parts), text)
        self.assertEqual([r[5] for r in self.ai_rows()], [text])
        self.assertEqual(len(feed_lines(self.box)), 4)

    def test_said_but_not_stored_or_fed_only_warns(self):
        os.chmod(self.box.db, 0o444)
        r = self.vfg("say", "still goes")
        os.chmod(self.box.db, 0o644)
        self.assertEqual(r.returncode, 0)
        self.assertEqual([l.split(":")[0] for l in r.stderr.splitlines()],
                         ["vfg", "vfg"])
        self.assertRegex(r.stderr, r"^vfg: said, but not stored in history: .*\nvfg: said, but not fed: ")
        self.assertEqual(len(self.box.rcon_commands()), 1)
        self.assertEqual(self.ai_rows(), [])

    def test_nobody_there_or_minecraft_down_says_nothing_and_stores_nothing(self):
        self.assertOneLine(self.vfg("say", "hello", "--to", "Nobody"), 1, "^vfg: Nobody is not online$")
        self.box.rcon_down()
        self.assertOneLine(self.vfg("say", "hi"), 5)
        self.assertEqual(self.ai_rows(), [])
        self.assertEqual(len(feed_lines(self.box)), 3)


# =================================================================== history

class TestHistory(BoxCase):
    def lines(self, *args):
        r = self.vfg("history", *args)
        self.assertOk(r)
        return r.stdout.splitlines()

    def texts(self, *args):
        return [l.split(" | ")[1] for l in self.lines(*args)]

    def test_lines_oldest_first_and_their_filters(self):
        out = self.lines()
        self.assertEqual(len(out), 6)
        self.assertIn(" Bob voice conversational 0.88 | thanks AI", out[0])
        self.assertRegex(out[2], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d Steve voice action_oriented 0\.93 \| build me a castle$")
        self.assertTrue(out[4].endswith(" Alex chat - | lol"))
        self.assertTrue(out[5].endswith(" AI ai - | on it — castle at spawn"))
        self.assertEqual(self.texts("--player", "steve"), ["hmm", "build me a castle"])
        self.assertEqual(self.texts("--class", "unjudged"), ["lol"])
        self.assertEqual(self.texts("--class", "action_oriented,context", "--since", "-10m"),
                         ["build me a castle", "that cave by spawn"])
        self.assertEqual(self.texts("--class", "not_relevant,conversational"), ["thanks AI", "hmm"])
        self.assertEqual(self.texts("--source", "ai"), ["on it — castle at spawn"])
        self.assertEqual(len(self.lines("--source", "voice")), 3)
        self.assertEqual(self.texts("--limit", "2"), ["lol", "on it — castle at spawn"])
        self.assertEqual(self.lines("--player", "Nobody"), [])

    def test_times(self):
        self.assertEqual(len(self.lines("--since", "-10m")), 4)
        self.assertEqual(len(self.lines("--since=-30m")), 5)
        self.assertEqual(len(self.lines("--since", "-2h", "--until", "-10m")), 2)
        self.assertEqual(len(self.lines("--since", "-90s")), 0)
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(len(self.lines("--until", tomorrow)), 6)
        self.assertEqual(len(self.lines("--since", tomorrow)), 0)
        t = iso(datetime.now(timezone.utc) - timedelta(minutes=10))[:19] + "Z"
        self.assertEqual(len(self.lines("--since", t)), 4)

    def test_events_and_the_feed(self):
        out = self.lines("--events")
        self.assertEqual(len(out), 3)
        self.assertTrue(out[0].endswith(" server - | Server started"))
        self.assertRegex(out[2], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d death Steve \| Steve was slain by Zombie$")
        self.assertEqual(len(self.lines("--events", "--player", "STEVE", "--since", "-10m")), 2)
        self.assertEqual(self.lines("--feed"), [l for _, l in self.box.feed_rows])
        self.assertEqual(self.lines("--feed", "--limit", "1"), [self.box.feed_rows[-1][1]])

    def test_refusals(self):
        for args in (["--class", "bogus"], ["--class", "direct_ask"], ["--source", "radio"], ["--limit", "1001"],
                     ["--limit", "0"], ["--since", "yesterday"], ["--class", "context", "--events"],
                     ["--source", "ai", "--feed"], ["--player", "Steve", "--feed"], ["--events", "--feed"],
                     ["--since", "-1m", "--until", "-1h"]):
            self.assertOneLine(self.vfg("history", *args), 2)
        os.remove(self.box.db)
        self.assertOneLine(self.vfg("history"), 1, r"^vfg: no transcript history yet \(.*/vfg\.db missing\)$")
        self.assertFalse(os.path.exists(self.box.db))


# =================================================================== context

class TestContext(BoxCase):
    def rows(self, *args, **env):
        r = self.vfg("context", "--json", *args, **env)
        self.assertOk(r)
        return [json.loads(l) for l in r.stdout.splitlines()]

    def test_the_rows_of_the_last_hours_oldest_first(self):
        old = iso(datetime.now(timezone.utc) - timedelta(hours=2))
        con = __import__("sqlite3").connect(self.box.db)
        con.execute("INSERT INTO feed (seq, at, line) VALUES (0, ?, 'SYS#0 00:00:00 | two hours ago')", (old,))
        con.commit()
        con.close()
        self.box.post("AI", "action T7", "give Steve diamond_pickaxe — ok")
        rows = self.rows()
        self.assertEqual([r["seq"] for r in rows], [0, 1, 2, 3, 4])
        self.assertEqual(sorted(rows[1]), ["at", "line", "processed_at", "seq"])
        self.assertEqual((rows[1]["line"], rows[1]["processed_at"]), (self.box.feed_rows[0][1], None))
        self.assertRegex(rows[4]["line"], r"^AI#4 \d\d:\d\d:\d\d action T7 \| give Steve diamond_pickaxe — ok$")
        self.assertEqual([r["seq"] for r in self.rows(VFG_BRAIN_HISTORY_HOURS="1")], [1, 2, 3, 4])
        self.assertEqual([r["seq"] for r in self.rows("--since", "-8m")], [3, 4])
        r = self.vfg("context", "--since", "-8m")
        self.assertEqual(r.stdout.splitlines(), [rows[3]["line"], rows[4]["line"]])
        self.assertEqual(len(self.box.programs("read")), 4)

    def test_mark_sets_processed_at_once_in_one_call(self):
        r = self.vfg("context", "mark", "1", "2", "99")
        self.assertEqual((r.returncode, r.stdout), (0, "2\n"), r.stderr)
        first = self.box.query("SELECT processed_at FROM feed WHERE seq = 1")[0][0]
        self.assertRegex(first, ISO_RE)
        self.assertEqual(self.vfg("context", "mark", "1", "2", "3").stdout, "1\n")
        self.assertEqual(self.box.query("SELECT processed_at FROM feed WHERE seq = 1")[0][0], first)
        self.assertEqual([r["processed_at"] is not None for r in self.rows()], [True, True, True])
        many = [str(s) for s in range(1, 1300)]
        before = len(self.box.calls)
        self.assertEqual(self.vfg("context", "mark", *many).stdout, "0\n")
        self.assertEqual(len(self.box.calls), before + 1)
        for bad in ("x", "0", "-3", "1.5"):
            self.assertOneLine(self.vfg("context", "mark", bad), 2)

    def test_without_the_column_it_says_so(self):
        self.box.close()
        self.box = Box(self.rcon, processed_at=False)
        for args in (["context"], ["context", "mark", "1"]):
            self.assertOneLine(self.vfg(*args), 1, "^vfg: the feed table has no processed_at column yet — "
                                                   "restart vfg-bridge, which adds it$")


# ===================================================================== usage

class TestUsage(BoxCase):
    def usage(self):
        return self.box.query("SELECT id, at, component, model, input_tokens, output_tokens, cost_usd, ref FROM usage")

    def test_a_row_with_the_pcs_time_for_every_component(self):
        before = iso(datetime.now(timezone.utc))
        r = self.vfg("usage", "add", "--component", "brain", "--model", "openai/gpt-6.1-sol", "--in", "10",
                     "--out", "5", "--cost", "0.0012", "--ref", "T7")
        self.assertEqual((r.returncode, r.stdout), (0, "1\n"), r.stderr)
        rows = self.usage()
        self.assertEqual(rows[0][2:], ("brain", "openai/gpt-6.1-sol", 10, 5, 0.0012, "T7"))
        self.assertRegex(rows[0][1], ISO_RE)
        self.assertGreaterEqual(rows[0][1], before[:19])
        for c in ("respond", "jev", "implementer", "conversation", "orchestrator"):
            self.assertOk(self.vfg("usage", "add", "--component", c, "--model", "m", "--in", "1", "--out", "0"))
        self.assertEqual([r[2] for r in self.usage()], ["brain", "respond", "jev", "implementer", "conversation",
                                                       "orchestrator"])
        self.assertIsNone(self.usage()[1][6])

    def test_queued_when_down_then_inserted_first(self):
        self.box.down = True
        self.assertOneLine(self.vfg("usage", "add", "--component", "brain", "--model", "sol", "--in", "100",
                                    "--out", "50", "--ref", "T1"), 4, "cannot reach EC2")
        pending = os.path.join(self.box.vhome, "usage.pending.jsonl")
        with open(pending) as f:
            self.assertEqual(len(f.read().splitlines()), 1)
        self.box.down = False
        r = self.vfg("usage", "add", "--component", "implementer", "--model", "haiku", "--in", "7", "--out", "3")
        self.assertEqual(r.stdout, "2\n")
        rows = self.usage()
        self.assertEqual([x[2] for x in rows], ["brain", "implementer"])
        self.assertLess(rows[0][1], rows[1][1])
        self.assertFalse(os.path.exists(pending) and os.path.getsize(pending))
        self.assertEqual([n for n in os.listdir(self.box.vhome) if n.startswith("usage.sending")], [])

    def test_bad_arguments(self):
        base = ["usage", "add", "--model", "m", "--in", "1", "--out", "1"]
        for extra in (["--component", "player"], ["--component", "jev", "--cost", "-1"],
                      ["--component", "jev", "--ref", "x" * 65], ["--component", "jev", "--in", "-3"],
                      ["--component", "jev", "--cache-read", "-1"]):
            self.assertOneLine(self.vfg(*(base + extra)), 2)
        self.assertEqual(self.usage(), [])


# ===================================================== watchers, lookout, menu

class TestFiles(BoxCase):
    def read(self, name):
        with open(os.path.join(self.box.rvfg, name)) as f:
            return f.read()

    def test_watchers(self):
        r = self.vfg("watchers", "show")
        self.assertOneLine(r, 0, "watch.json missing — the bridge uses the default")
        self.assertEqual(json.loads(r.stdout)["events"], ["join", "leave", "death", "advancement", "server"])
        for args in (["dance"], ["join", "--players", "bad name"], ["join", "--contains", "(unclosed"], ["join,none"]):
            self.assertOneLine(self.vfg("watchers", "set", *args), 2)
        r = self.vfg("watchers", "set", "death,join", "--players", "Steve,Alex", "--contains", "diamond",
                     "--contains", r"^\[.*\] x+$")
        want = {"events": ["join", "death"], "players": ["Steve", "Alex"], "contains": ["diamond", r"^\[.*\] x+$"]}
        self.assertEqual(r.stdout.splitlines(), [json.dumps(want)])
        self.assertEqual(list(json.loads(self.read("watch.json")).items()), list(want.items()))
        self.assertEqual(oct(os.stat(os.path.join(self.box.rvfg, "watch.json")).st_mode & 0o777), "0o644")
        self.assertOk(self.vfg("watchers", "set", "all"))
        self.assertEqual(json.loads(self.read("watch.json"))["events"], ["join", "leave", "death", "advancement", "server"])
        self.assertOk(self.vfg("watchers", "set", "none"))
        self.assertEqual(json.loads(self.vfg("watchers", "show").stdout), {"events": [], "players": [], "contains": []})

    def test_lookout(self):
        r = self.vfg("lookout", "show")
        self.assertOneLine(r, 0, "lookout.md missing")
        r = self.vfg("lookout", "set", "  look out for builds ~ and $HOME  ")
        self.assertEqual(r.stdout, "lookout.md: 31 characters\n")
        self.assertEqual(self.read("lookout.md"), "look out for builds ~ and $HOME\n")
        self.assertEqual(self.vfg("lookout", "show").stdout, "look out for builds ~ and $HOME\n")
        self.assertEqual(self.vfg("lookout", "set", "-", input="line one\nline two\n").stdout,
                         "lookout.md: 17 characters\n")
        self.assertOneLine(self.vfg("lookout", "set", "x" * 8001), 2)
        self.assertOneLine(self.vfg("lookout", "set", "-", input="  \n"), 2)

    def test_jev_menu(self):
        r = self.vfg("jev", "menu", "show")
        self.assertOneLine(r, 0, "^vfg: menu.md missing — Jev has no action menu$")
        self.assertEqual(r.stdout, "")
        menu = "give — give a player items\nteleport — move a player or everyone to a place\n"
        r = self.vfg("jev", "menu", "set", "-", input=menu)
        self.assertEqual((r.returncode, r.stdout), (0, "menu.md: 2 lines, %d characters\n" % len(menu.strip())))
        self.assertEqual(self.read("menu.md"), menu)
        self.assertEqual(oct(os.stat(os.path.join(self.box.rvfg, "menu.md")).st_mode & 0o777), "0o644")
        self.assertEqual(self.vfg("jev", "menu", "show").stdout, menu)
        self.assertEqual(self.vfg("jev", "menu", "set", "stop — stop a running copy").stdout,
                         "menu.md: 1 line, 26 characters\n")
        self.assertEqual(self.vfg("jev", "menu", "set", "").stdout, "menu.md: 0 lines, 0 characters\n")
        self.assertEqual(self.read("menu.md"), "")
        self.assertOneLine(self.vfg("jev", "menu", "set", "x" * 32001), 2, "at most 32000 characters")


# ====================================================================== feed

class TestFeed(BoxCase):
    def test_post_prints_the_seq_and_appends_the_line(self):
        r = self.vfg("feed", "post", "SYS", "", "smoke ~ $HOME 'q' \"d\"")
        self.assertEqual((r.returncode, r.stdout), (0, "4\n"), r.stderr)
        self.assertRegex(feed_lines(self.box)[-1], r"^SYS#4 \d\d:\d\d:\d\d \| smoke ~ \$HOME 'q' \"d\"$")
        self.assertEqual(self.box.calls[-1]["cmd"],
                         "env VFG_DB=%s/vfg.db VFG_FEED=%s/feed.log %s/python/bin/python3 %s/lib/vfg_feed.py post "
                         "SYS '' 'smoke ~ $HOME '\"'\"'q'\"'\"' \"d\"'" % ((self.box.rvfg,) * 4))
        self.assertOk(self.vfg("feed", "post", "GAME", "death Steve", "--", "-5 hearts"))
        for fields in ("say -", "action T7", "task T183", "result T183", "note -"):
            self.assertOk(self.vfg("feed", "post", "AI", fields, "text"))
        lines = feed_lines(self.box)
        self.assertRegex(lines[4], r"^GAME#5 \d\d:\d\d:\d\d death Steve \| -5 hearts$")
        self.assertRegex(lines[6], r"^AI#7 \d\d:\d\d:\d\d action T7 \| text$")

    def test_bad_posts(self):
        for args in (["NOISE", "", "x"], ["SYS", "f", "x"], ["ORCH", "T7 done", "x"], ["AI", "finished T7", "x"],
                     ["AI", "say 7", "x"], ["AI", "say", "x"], ["AI", "action T0", "x"],
                     ["GAME", "death | Steve", "x"], ["LINE", "a\nb", "x"]):
            self.assertOneLine(self.vfg("feed", "post", *args), 2)
        self.assertEqual(len(feed_lines(self.box)), 3)
        self.assertEqual(self.box.calls, [])


# ===================================================================== brain

BRAIN = """#!/usr/bin/env bash
# A stand-in for brain/run: up until SIGTERM, which it notes.
trap 'echo "stopping" >> "$VFG_HOME/brain/order"; exit 0' TERM
echo "fake brain $$ up"
while true; do sleep 0.02; done
"""


class TestBrain(BoxCase):
    def setUp(self):
        BoxCase.setUp(self)
        self.run_path = self.box.write("brain/run", BRAIN)
        os.chmod(self.run_path, 0o755)

    def brain(self, name):
        return os.path.join(self.box.vhome, "brain", name)

    def log(self):
        with open(self.brain("brain.log")) as f:
            return f.read()

    def started(self, r):
        """The pid `vfg brain start` printed. A vfg process would exit and leave the brain to init;
        this test's process stays its parent, so it reaps it, or a stop would wait on a zombie."""
        m = re.match(r"^brain  started pid (\d+)\n$", r.stdout)
        self.assertTrue(m and r.returncode == 0, r)
        pid = int(m.group(1))

        def reap():
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
        threading.Thread(target=reap, daemon=True).start()
        return pid

    def test_start_status_stop(self):
        r = self.vfg("brain", "status")
        self.assertOneLine(r, 1, "^vfg: the brain is not running — vfg brain start$")
        self.assertEqual(r.stdout, "brain  stopped\nopen   none\n")
        self.assertEqual(self.vfg("brain", "stop").stdout, "brain  not running\n")
        pid = self.started(self.vfg("brain", "start", CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli"))
        self.assertTrue(alive(pid))      # it outlived the vfg that started it
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = f.read().split(b"\0")
        for want in (b"VFG_BRAIN=1", b"VFG_HOME=" + self.box.vhome.encode(), b"VFG_REPO=" + self.box.repo.encode()):
            self.assertIn(want, env)
        self.assertFalse(any(e.startswith((b"CLAUDECODE=", b"CLAUDE_CODE_ENTRYPOINT=")) for e in env))
        self.assertEqual(os.getpgid(pid), pid)
        with open(self.brain("brain.pid")) as f:
            self.assertEqual(int(f.read()), pid)
        self.assertEqual(self.vfg("brain", "start").stdout, "brain  running pid %d\n" % pid)
        self.assertTrue(wait_for(lambda: self.log().endswith("up\n")))
        self.assertRegex(self.log(), r"^\d{4}-\d\d-\d\dT[\d:.]+Z vfg brain start\nfake brain %d up\n$" % pid)
        write(self.brain("tasks.json"), json.dumps({"next": 11, "tasks": {
            "T10": {"id": "T10", "player": "Alex", "line": "a castle", "task": "a castle at spawn, 40 by 40",
                    "status": "open"},
            "T9": {"id": "T9", "player": "Steve", "line": "ice", "task": "an ice trail behind Steve\nfor a minute",
                   "status": "open"},
            "T8": {"id": "T8", "player": "Steve", "task": "a done one", "status": "done"}}}))
        r = self.vfg("brain", "status")
        self.assertRegex(r.stdout, r"^brain  running pid %d since \d\d:\d\d:\d\d\nopen   T9 Steve: an ice trail behind "
                                   r"Steve for a minute\nopen   T10 Alex: a castle at spawn, 40 by 40\n$" % pid)
        r = self.vfg("brain", "stop")
        self.assertEqual(r.stdout, "brain  stopped pid %d\n" % pid)
        self.assertTrue(wait_for(lambda: not alive(pid)))
        with open(self.brain("order")) as f:
            self.assertEqual(f.read(), "stopping\n")   # SIGTERM, which it saw
        self.assertFalse(os.path.exists(self.brain("brain.pid")))
        self.assertOneLine(self.vfg("brain", "status"), 1)

    def test_a_brain_deaf_to_sigterm_is_killed(self):
        write(self.run_path, "#!/usr/bin/env bash\ntrap '' TERM\nwhile true; do sleep 0.02; done\n")
        pid = self.started(self.vfg("brain", "start"))
        mod = vfg_module()
        mod.STOP_GRACE_S, saved = 0.1, mod.STOP_GRACE_S
        try:
            self.assertEqual(self.vfg("brain", "stop").stdout, "brain  stopped pid %d\n" % pid)
        finally:
            mod.STOP_GRACE_S = saved
        self.assertFalse(alive(pid))

    def test_start_refusals_and_a_stale_pid(self):
        os.chmod(self.run_path, 0o644)
        r = self.vfg("brain", "start")
        self.assertOneLine(r, 1, r"^vfg: .*/brain/run is missing or not executable$")
        self.assertEqual(r.stdout, "")
        write(self.run_path, "#!/usr/bin/env bash\necho broken\nexit 7\n")
        os.chmod(self.run_path, 0o755)
        mod = vfg_module()
        mod.START_CHECK_S, saved = 3.0, mod.START_CHECK_S   # it exits at once: the wait ends then, however loaded
        try:
            r = self.vfg("brain", "start")
        finally:
            mod.START_CHECK_S = saved
        self.assertOneLine(r, 1, r"^vfg: the brain exited at once \(code 7\) — see .*brain/brain\.log$")
        self.assertEqual(r.stdout, "brain  exited at once (code 7)\n")
        self.assertFalse(os.path.exists(self.brain("brain.pid")))
        with open(self.brain("brain.log")) as f:
            self.assertIn("broken\n", f.read())
        write(self.brain("brain.pid"), "%d\n" % os.getpid())   # alive, but not the brain: no VFG_BRAIN=1
        self.assertOneLine(self.vfg("brain", "status"), 1, "not running")
        self.assertFalse(os.path.exists(self.brain("brain.pid")))


# ===================================================================== state

class TestState(BoxCase):
    def test_who_is_where_the_time_the_weather_and_their_items(self):
        self.rcon.world = {"players": {"Steve": {"pos": [120.7, 64.0, -40.2], "shown": "[Owner] Steve", "items": {
            "Inventory": '[{Slot: 4b, id: "minecraft:iron_sword", count: 1}]',
            "equipment": '{feet: {id: "minecraft:diamond_boots", count: 1}}', "SelectedItemSlot": 4}},
            "Alex": {"pos": [1, 2, 3], "dim": "minecraft:the_nether"}},
            "daytime": 18000, "day": 3, "raining": True, "thundering": False}
        r = self.vfg("state")
        self.assertEqual(r.stdout.splitlines(), ["online    Steve 120 64 -41 overworld, Alex 1 2 3 the_nether",
                                                 "time      night (18000), day 3", "weather   rain",
                                                 "running   nothing"])
        self.assertNotIn("Inventory", "".join(self.box.rcon_commands()))
        got = json.loads(self.vfg("state", "--json", "--items").stdout)
        steve, alex = got["online"]
        self.assertEqual((steve["name"], steve["pos"], steve["dimension"]), ("Steve", [120, 64, -41], "overworld"))
        self.assertEqual(steve["items"], [
            'Steve has the following entity data: [{Slot: 4b, id: "minecraft:iron_sword", count: 1}]',
            'Steve has the following entity data: {feet: {id: "minecraft:diamond_boots", count: 1}}',
            "Steve has the following entity data: 4"])
        self.assertEqual(alex["items"], ["Alex has the following entity data: []", "",
                                         "Alex has the following entity data: 0"])   # wears nothing
        self.assertEqual((got["time"], got["weather"]), ({"daytime": 18000, "day": 3, "phase": "night"}, "rain"))
        self.assertIn("execute if data entity Alex equipment run data get entity Alex equipment",
                      self.box.rcon_commands())
        self.assertEqual([p["items"] for p in self.box.programs("state")], [False, True])
        r = self.vfg("state", "--items")
        self.assertIn("items     Steve: Steve has the following entity data: [{Slot: 4b", r.stdout)

    def test_minecraft_down_exits_5_with_what_it_knows(self):
        self.box.rcon_down()
        r = self.vfg("state")
        self.assertEqual(r.returncode, 5)
        self.assertTrue(r.stdout.startswith("minecraft minecraft is not running"), r.stdout)


# =================================================================== scripts

HELLO = '"""Hello: says hello and exits."""\nimport sys\nprint("hello", " ".join(sys.argv[1:]))\n'


class TestScripts(BoxCase):
    def setUp(self):
        BoxCase.setUp(self)
        for rel, text in {"scripts/_lib/mc.py": "NAME = 'fake mc'\n", "scripts/_lib/__pycache__/mc.cpython-39.pyc": "x",
                          "scripts/_test/run_tests.py": "# not a script\n", "scripts/hello/main.py": HELLO,
                          "scripts/hello/.secret": "never deployed\n", "scripts/hello/__pycache__/m.pyc": "x",
                          "scripts/loop/main.py": '"""Loop: ticks."""\n', "scripts/README.md": "The folder.\n"}.items():
            self.box.write(rel, text)

    def deployed(self, *p):
        return os.path.join(self.box.rvfg, "scripts", *p)

    def test_deploy_all_and_one(self):
        r = self.vfg("script", "deploy")
        self.assertEqual((r.returncode, r.stdout), (0, "deployed: _lib hello loop\n"), r.stderr)
        self.assertTrue(os.path.isfile(self.deployed("hello", "main.py")))
        with open(self.deployed("_lib", ".vfg-stamp")) as f:      # what an action's run checks first
            self.assertEqual(f.read(), vfg_module().stamp(list(vfg_module().tree(
                os.path.join(self.box.repo, "scripts"), "_lib"))) + "\n")
        self.assertTrue(os.path.isfile(self.deployed("_lib", "mc.py")))
        for gone in (("_test",), ("hello", ".secret"), ("hello", "__pycache__"), ("_lib", "__pycache__")):
            self.assertFalse(os.path.exists(self.deployed(*gone)), gone)
        os.makedirs(self.deployed("orphan"))
        write(self.deployed("hello", "stale.txt"), "old\n")
        self.assertEqual(self.vfg("script", "deploy", "hello").stdout, "deployed: _lib hello\n")
        self.assertFalse(os.path.exists(self.deployed("hello", "stale.txt")))   # swapped whole
        self.assertTrue(os.path.isdir(self.deployed("orphan")))                 # no twin here: left alone
        self.assertEqual([n for n in os.listdir(self.deployed()) if n.startswith(".")], [])

    def test_what_is_not_a_script_is_skipped_or_refused(self):
        self.box.write("scripts/broken/main.py", "print(1)\n")
        r = self.vfg("script", "deploy")
        self.assertOneLine(r, 1, "skipped.*: broken$")
        self.assertEqual(r.stdout, "deployed: _lib hello loop\n")
        self.assertFalse(os.path.exists(self.deployed("broken")))
        self.assertOneLine(self.vfg("script", "deploy", "broken"), 1, "has no README.md")
        self.assertOneLine(self.vfg("script", "deploy", "Bad.Name"), 2)
        self.assertOneLine(self.vfg("script", "logs", "hello"), 1, "no log for hello")
        self.assertOneLine(self.vfg("script", "logs", "../x"), 2)

    def test_an_unsafe_tar_member_is_refused(self):
        mod = vfg_module()

        def tar_with(name, kind=tarfile.REGTYPE):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tf:
                ti = tarfile.TarInfo(name)
                ti.type = kind
                if kind == tarfile.SYMTYPE:
                    ti.linkname = "/etc/passwd"
                    tf.addfile(ti)
                else:
                    ti.size = 1
                    tf.addfile(ti, io.BytesIO(b"x"))
            return base64.b64encode(buf.getvalue()).decode()
        for name, kind in (("hello/../../escape.txt", tarfile.REGTYPE), ("/abs.txt", tarfile.REGTYPE),
                           ("hello/link", tarfile.SYMTYPE)):
            prog = mod.program("scripts.py", {"vfg": self.box.rvfg, "mc": self.box.rmc, "op": "deploy",
                                              "tar": tar_with(name, kind)}, mod.SCRIPTS_LIBS)
            code, out, err = self.box.run_program(prog)
            self.assertEqual(code, 1, err)
            self.assertIn(b"deploy refused", err)
        self.assertFalse(os.path.exists(os.path.join(self.box.root, "box", "escape.txt")))
        self.assertFalse(os.path.exists(self.deployed("hello")))


# =================================================================== refs

FAKE_JAVA = """#!%(py)s
# A stand-in for the data generator: java -DbundlerMainClass=... -jar <jar> --reports --output <dir>.
import json, os, sys, time
argv = sys.argv[1:]
out = argv[argv.index("--output") + 1]
with open(%(record)r, "w") as f:
    json.dump({"argv": argv, "cwd": os.getcwd(), "pid": os.getpid()}, f)
print("[ServerMain/INFO]: Starting provider: vanilla/Command Syntax", flush=True)
os.makedirs("logs", exist_ok=True)  # as log4j does, in the working directory
mode = %(mode)r
if mode == "fail":
    print("Exception in thread main: boom")
    sys.exit(1)
if mode == "slow":
    time.sleep(60)
if mode != "nothing":
    os.makedirs(os.path.join(out, "reports"), exist_ok=True)
    with open(os.path.join(out, "reports", "commands.json"), "w") as f:
        json.dump(%(tree)r, f, indent=2)
"""

TREE = {"type": "root", "children": {"time": {"type": "literal", "children": {
    "set": {"type": "literal", "executable": True}}}}}


class TestRefs(BoxCase):
    def make_jar(self, version=None, raw=None):
        jar = os.path.join(self.box.rmc, "server.jar")
        if raw is not None:
            with open(jar, "wb") as f:
                f.write(raw)
            return
        with zipfile.ZipFile(jar, "w") as z:
            if version is not None:
                z.writestr("version.json", json.dumps(version, indent=4))

    def java(self, mode="ok", tree=TREE):
        self.record = os.path.join(self.box.root, "java.json")
        java = write(os.path.join(self.box.bin, "java"), FAKE_JAVA % {"py": PY, "record": self.record,
                                                                      "mode": mode, "tree": tree})
        os.chmod(java, 0o755)

    def ran(self):
        with open(self.record) as f:
            return json.load(f)

    def test_the_version_from_the_jar(self):
        self.make_jar({"id": "26.3", "name": "26.3", "world_version": 5023})
        self.assertEqual(self.vfg("action", "refs", "--version").stdout, "26.3\n")
        self.make_jar({"id": "26.4-pre1"})
        self.assertEqual(self.vfg("action", "refs", "--version").stdout, "26.4-pre1\n")
        self.make_jar(None)
        self.assertOneLine(self.vfg("action", "refs", "--version"), 1, "server.jar has no version.json$")
        self.make_jar(raw=b"not a zip")
        self.assertOneLine(self.vfg("action", "refs", "--version"), 1, "^vfg: cannot read .*server.jar")
        os.remove(os.path.join(self.box.rmc, "server.jar"))
        self.assertOneLine(self.vfg("action", "refs", "--version"), 1, "^vfg: no server.jar in ")

    def test_the_command_tree_from_the_generator(self):
        self.make_jar({"id": "26.3", "name": "26.3"})
        self.java()
        r = self.vfg("action", "refs")
        self.assertEqual(r.stdout.count("\n"), 1)                # the generator's chatter stayed on EC2
        self.assertEqual(json.loads(r.stdout), {"version": "26.3", "commands": TREE})
        ran = self.ran()
        tmp = ran["argv"][-1]
        self.assertEqual(ran["argv"], ["-DbundlerMainClass=net.minecraft.data.Main", "-jar",
                                       os.path.join(self.box.rmc, "server.jar"), "--reports", "--output", tmp])
        self.assertTrue(tmp.startswith("/tmp/vfg-refs-"), tmp)
        self.assertEqual(os.path.realpath(ran["cwd"]), os.path.realpath(tmp))   # its logs/ went there
        self.assertFalse(os.path.exists(tmp))
        self.assertFalse(os.path.exists(os.path.join(self.box.rmc, "logs")))

    def test_failures_clean_up(self):
        self.make_jar({"name": "26.3"})
        self.java("fail")
        self.assertOneLine(self.vfg("action", "refs"), 1, r"^vfg: the data generator exited 1: Exception in thread "
                                                         r"main: boom$")
        self.assertFalse(os.path.exists(self.ran()["argv"][-1]))
        self.java("slow")
        self.assertOneLine(self.vfg("--timeout", "0.3", "action", "refs"), 1,
                           r"^vfg: the data generator did not finish within 0\.15 s$")
        self.assertFalse(alive(self.ran()["pid"]))
        self.assertFalse(os.path.exists(self.ran()["argv"][-1]))


# ===================================================================== places

HAVE_WORLD = all(os.path.isfile(os.path.join(SCRIPTS_TEST, n)) for n in ("fake_places_world.py", "mc_26_3.py"))


@unittest.skipUnless(HAVE_WORLD, "no scripts/_test/fake_places_world.py beside the toolkit")
class TestPlace(BoxCase):
    """RCON answered by fake_places_world.FakeWorld, Minecraft 26.3's recorded replies; vfg place
    save is the save_place action (its stand-in, standins.py, on the real kit)."""

    def setUp(self):
        BoxCase.setUp(self)
        if SCRIPTS_TEST not in sys.path:
            sys.path.append(SCRIPTS_TEST)   # fake_places_world imports mc_26_3
        self.world = load_module("scripts_test_fake_places_world",
                                 os.path.join(SCRIPTS_TEST, "fake_places_world.py")).FakeWorld(
            players={"Steve": (197.5, 68.0, 251.5)})
        self.world.add("Alex", (10.2, -5.0, 3.9), dim="minecraft:the_nether")
        self.rcon.responder = self.world.respond
        standins.install(self.box.repo, ["save_place"])

    def place(self, *args, **env):
        return self.vfg("place", *args, **env)

    def cache(self):
        with open(os.path.join(self.box.vhome, "places.json")) as f:
            return json.load(f)

    def age_cache(self, seconds):
        data = self.cache()
        data["fetched_at"] -= seconds
        write(os.path.join(self.box.vhome, "places.json"), json.dumps(data))

    def lists(self):
        return [c for c in self.world.sent if c == "data get storage everhear:places"]

    def test_save_list_and_forget(self):
        r = self.place("save", "castle", "--player", "Steve", "--by", "T23")
        self.assertEqual((r.returncode, r.stdout), (0, "saved castle at 197 68 251 in overworld\n"), r.stderr)
        self.assertIn('data modify storage everhear:places castle set value '
                      '{pos:[197,68,251],dim:"minecraft:overworld",by:"T23"}', self.world.sent)
        self.assertEqual(self.place("save", "nest", "--at", "-368.7", "70", "912.2", "--dim", "THE_END", "--by",
                                    "T5").stdout, "saved nest at -369 70 912 in the_end\n")
        self.assertEqual(self.world.storage["nest"], {"pos": [-369, 70, 912], "by": "T5", "dim": "minecraft:the_end"})
        self.assertEqual(self.place("list", "--fresh").stdout, "castle 197 68 251 overworld (T23)\n"
                                                               "nest -369 70 912 the_end (T5)\n")
        self.assertEqual(json.loads(self.place("list", "--json").stdout)["castle"],
                         {"pos": [197, 68, 251], "dim": "minecraft:overworld", "by": "T23"})
        self.assertEqual(self.place("forget", "castle").stdout, "forgot castle\n")
        self.assertOneLine(self.place("forget", "castle"), 1, "^vfg: no place named castle$")
        self.assertOneLine(self.place("save", "x", "--player", "Nobody"), 1, "^vfg: Nobody is not online$")
        self.assertNotIn("x", self.world.storage)

    def test_bad_arguments_never_reach_minecraft(self):
        for args in (["save", "Castle", "--player", "Steve"], ["save", "a-b", "--at", "1", "2", "3"],
                     ["save", "x" * 33, "--at", "1", "2", "3"], ["save", "x", "--player", "bad name"],
                     ["save", "x", "--at", "1", "2"], ["save", "x", "--at", "nan", "2", "3"],
                     ["save", "x", "--player", "Steve", "--dim", "the_end"],
                     ["save", "x", "--at", "1", "2", "3", "--dim", "bad dim!"], ["save", "x", "--player", "Alex", "--by", "7"],
                     ["save", "x"], ["save", "x", "--player", "Alex", "--at", "1", "2", "3"], ["forget", "Castle"],
                     ["list", "x"]):
            self.assertOneLine(self.place(*args), 2)
        self.assertEqual((self.world.sent, self.box.calls), ([], []))

    def test_the_cache(self):
        self.assertEqual(self.place("list").stdout, "none\n")
        self.world.save_place("castle", [197, 68, 251], by="T23")
        self.assertEqual(self.place("list").stdout, "none\n")                     # inside the TTL: the cache
        self.assertEqual(os.stat(os.path.join(self.box.vhome, "places.json")).st_mode & 0o777, 0o600)
        self.assertEqual(self.place("list", "--fresh").stdout, "castle 197 68 251 overworld (T23)\n")
        self.world.save_place("zoo", [1, 2, 3])
        self.age_cache(31)
        self.assertEqual(self.place("list").stdout, "castle 197 68 251 overworld (T23)\nzoo 1 2 3 overworld\n")
        self.assertEqual(len(self.lists()), 3)
        self.assertEqual(self.place("list", VFG_PLACES_TTL_S="0").stdout.count("\n"), 2)
        self.assertEqual(len(self.lists()), 4)
        # save and forget write through, keeping the cache's age
        self.age_cache(10)
        at = self.cache()["fetched_at"]
        self.assertOk(self.place("save", "nest", "--at", "1", "2", "3", "--dim", "the_end"))
        self.assertOk(self.place("forget", "zoo"))
        self.assertEqual(sorted(self.cache()["places"]), ["castle", "nest"])
        self.assertAlmostEqual(self.cache()["fetched_at"], at, delta=1)
        self.assertEqual(self.place("list").stdout, "castle 197 68 251 overworld (T23)\nnest 1 2 3 the_end\n")
        self.assertEqual(len(self.lists()), 4)

    def test_minecraft_down(self):
        self.box.write_props(enable=False)
        self.assertOneLine(self.place("list"), 5, "minecraft is not running")
        self.assertFalse(os.path.exists(os.path.join(self.box.vhome, "places.json")))
        self.assertOneLine(self.place("save", "x", "--at", "1", "2", "3"), 5, "minecraft is not running")
        self.box.write_props()
        self.world.save_place("castle", [197, 68, 251], by="T23")
        self.place("list")
        self.age_cache(600)
        self.box.write_props(enable=False)
        r = self.place("list")              # a failed fetch serves a stale cache
        self.assertEqual((r.returncode, r.stdout), (0, "castle 197 68 251 overworld (T23)\n"))
        self.assertRegex(r.stderr, r"^vfg: saved places from 60\d s ago: .*minecraft is not running")


# ======================================================================== up

class TestUp(BoxCase):
    def unit(self, name, state):
        write(os.path.join(self.box.units, name), state + "\n")

    def test_starts_what_is_down(self):
        self.unit("minecraft", "inactive")
        r = self.vfg("up")
        self.assertEqual(r.stdout.splitlines(), ["ssh         ok  ec2-user@fakebox",
                                                 "minecraft   active  started  rcon ok", "vfg-bridge  active",
                                                 "vfg-jev     active", "feed        last seq 3", "scripts     running: none"])
        os.remove(self.box.feed)
        self.assertIn("feed        no feed.log yet", self.vfg("up").stdout)

    def test_a_missing_unit_and_one_that_will_not_start(self):
        os.remove(os.path.join(self.box.units, "vfg-bridge"))
        r = self.vfg("up")
        self.assertOneLine(r, 1, "^vfg: not up: vfg-bridge missing$")
        self.assertIn("vfg-bridge  missing", r.stdout)
        self.unit("vfg-bridge", "active")
        self.unit("vfg-jev", "failed")
        write(os.path.join(self.box.units, "vfg-jev.fail"), "")
        write(os.path.join(self.box.units, "vfg-jev.journal"), "starting\nKeyError: TYPESAFE_API_KEY\n")
        r = self.vfg("up")
        self.assertOneLine(r, 1, r"^vfg: not up: vfg-jev failed \(KeyError: TYPESAFE_API_KEY\)$")
        self.assertIn("vfg-jev     failed  start failed  KeyError: TYPESAFE_API_KEY", r.stdout)


# ===================================================================== watch

def follow_module():
    """remote/follow.py's functions, without its main()."""
    with open(os.path.join(TOOLKIT, "remote", "follow.py")) as f:
        text = f.read()
    ns = {"__name__": "follow", "ARGS": {}}
    exec(compile("import os, sys, time\n" + text.rsplit("\nmain()", 1)[0], "follow.py", "exec"), ns)
    return ns


def seqs(lines):
    return [int(l.split("#")[1].split()[0]) for l in lines]


class TestWatch(BoxCase):
    """vfg watch's parts, here: follow.py's opening on the box, and the Watcher on the PC. A
    watch as a process: test_e2e.py."""

    def watcher(self, *args, **kw):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        with self.box.environ() as mod:
            watcher = mod.Watcher(mod.Config(), *args, fd=w, **kw)
        os.set_blocking(r, False)

        def read():
            try:
                return os.read(r, 1 << 20).decode().splitlines()
            except BlockingIOError:
                return []
        watcher.read = read
        return watcher

    def lines(self, first, last):
        return [("SYS#%d 12:00:00 | line %d" % (s, s)).encode() for s in range(first, last + 1)]

    def cursor(self):
        try:
            with open(os.path.join(self.box.vhome, "cursor")) as f:
                return int(f.read())
        except OSError:
            return None

    def test_the_box_side_opening(self):
        F = follow_module()
        entries = F["parse"](self.lines(1, 500) + [b"AI#501 12:00:01 say - | hi", b"@hb", b"junk"])
        self.assertEqual(entries[-1], (501, b"AI#501 12:00:01 say - | hi"))   # every type
        parts = []
        self.assertEqual(F["opening"](entries, None, 100, None, parts), 501)
        self.assertEqual(parts, [b"@at 501"])                               # no cursor: nothing old
        parts = []
        F["opening"](entries, 0, 100, None, parts)
        self.assertEqual((parts[0], len(parts)), (b"@skip 1 401 401", 101))  # far behind: the last 100
        parts = []
        F["opening"](entries, 600, 100, None, parts)
        self.assertEqual(parts[:2], [b"@reset", b"@skip 1 401 401"])       # a shorter feed: from the start
        parts = []
        F["opening"](entries, 499, 100, None, parts)
        self.assertEqual(parts, [entries[-2][1], entries[-1][1]])
        parts = []
        F["opening"](entries, None, 100, 2, parts)
        self.assertEqual(parts, [b"@at 499", entries[-2][1], entries[-1][1]])

    def test_a_persons_watch_prints_and_moves_its_cursor(self):
        w = self.watcher()
        self.assertTrue(w.persist)
        w.handle([b"@at 5", b"@hb"], True)
        self.assertEqual((w.read(), self.cursor()), ([], 5))
        w.handle(self.lines(6, 7) + self.lines(5, 6), False)       # a repeat is printed once
        self.assertEqual(seqs(w.read()), [6, 7])
        self.assertEqual(self.cursor(), 7)
        w.handle([b"@skip 8 20 13"] + self.lines(21, 21), False)
        got = w.read()
        self.assertRegex(got[0], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: skipped 13 lines \(seq 8–20\) while away — "
                                 r"vfg history --feed has them$")
        self.assertEqual(seqs(got[1:]), [21])
        w.handle([b"@reset"] + self.lines(1, 1), False)
        got = w.read()
        self.assertRegex(got[0], r"\| vfg watch: feed restarted — cursor reset$")
        self.assertEqual(self.cursor(), 1)
        # A second watch resumes from the cursor.
        self.assertEqual(self.watcher().last, 1)

    def test_the_valve_holds_back_a_flood_but_not_for_code(self):
        w = self.watcher()
        w.max_per_min = 5
        w.handle(self.lines(1, 12), False)
        self.assertEqual(seqs(w.read()), [1, 2, 3, 4, 5])
        self.assertEqual(self.cursor(), 12)
        w.tick(True)
        self.assertRegex(w.read()[0], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: held back 7 lines \(seq 6–12\), feed "
                                      r"too busy — vfg history --feed has them$")
        code = self.watcher(after=3)
        self.assertEqual((code.persist, code.last, code.max_per_min), (False, 3, None))
        code.handle(self.lines(1, 400), False)
        self.assertEqual(seqs(code.read()), list(range(4, 401)))
        self.assertEqual(len(code.printed), 0)
        self.assertEqual(self.cursor(), 12)                      # --after writes no cursor
        self.assertEqual(self.watcher(tail=5).last, None)

    def test_reconnects_after_the_last_line_with_no_gap_and_no_repeat(self):
        """Each connection here prints the two lines after the seq it was sent, then drops."""
        box_side = ("import json, re, sys\n"
                    "after = json.loads(eval(re.search(r'^ARGS = json.loads\\((.*)\\)$', sys.stdin.read(), re.M)"
                    ".group(1)))['after']\n"
                    "print('\\n'.join('SYS#%d 12:00:00 | line %d' % (s, s) for s in (after + 1, after + 2)))\n"
                    "print('@hb')\n")
        w = self.watcher(after=10)
        w.ssh.argv = lambda remote_cmd=None, direct=False: [PY, "-c", box_side]
        with self.box.environ():   # its environment, and quiet about the pipes it leaves to the GC
            self.assertTrue(w.connect(True))
            self.assertTrue(w.connect(False))
        self.assertEqual(seqs(w.read()), [11, 12, 13, 14])
        w.down_since -= 100
        w.tick(False)
        self.assertRegex(w.read()[0], r"^SYS#0 \d\d:\d\d:\d\d \| vfg watch: EC2 unreachable since \d\d:\d\d:\d\d — "
                                      r"retrying$")

    def test_the_newest_persons_watch_takes_the_lock(self):
        lock, pidfile = (os.path.join(self.box.vhome, n) for n in ("watch.lock", "watch.pid"))
        holder = subprocess.Popen([PY, "-c", "import fcntl, os, sys, time\n"
                                   "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
                                   "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                                   "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
                                   "print('held', flush=True)\ntime.sleep(10)\n", lock, pidfile, "vfg", "watch"],
                                  stdout=subprocess.PIPE)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline(), b"held\n")
        with self.box.environ() as mod:
            fd = mod.take_watch_lock(mod.Config())
        os.close(fd)
        self.assertEqual(holder.wait(5), -signal.SIGTERM)       # the older watch was asked to go
        with open(pidfile) as f:
            self.assertEqual(int(f.read()), os.getpid())
        holder.stdout.close()

    def test_only_a_persons_watch_is_taken_over(self):
        mod = vfg_module()
        procs = [subprocess.Popen(["bash", "-c", "sleep 5; :", "vfg"] + args, stdout=subprocess.DEVNULL,
                                  start_new_session=True)
                 for args in (["watch"], ["watch", "--tail", "3"], ["watch", "--after", "7"], ["watch", "--after=7"])]
        try:
            self.assertEqual([mod.is_vfg_watch(p.pid) for p in procs], [True, False, False, False])
        finally:
            for p in procs:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()


# =============================================================== RCON lock

class TestRconLock(BoxCase):
    """Vanilla answers every RCON connection from one buffer (RconConsoleSource): two clients with
    a command out at once get each other's output. Every client holds one lock file from send to
    reply; the fake answers from one shared buffer, as vanilla does."""
    shared_rcon = True

    def setUp(self):
        BoxCase.setUp(self)
        self.lock = os.path.join(self.box.root, "rcon.lock")
        os.environ["VFG_RCON_LOCK"] = self.lock
        self.addCleanup(os.environ.pop, "VFG_RCON_LOCK", None)
        self.common = {"__name__": "vfg_remote_common"}
        with open(os.path.join(TOOLKIT, "remote", "common.py")) as f:
            exec(compile(f.read(), "common.py", "exec"), self.common)

    def client(self):
        r = self.common["Rcon"](self.box.rmc)
        self.addCleanup(r.close)
        return r.command

    def bare(self):
        """An RCON client with no lock."""
        import socket
        import struct
        s = socket.create_connection(("127.0.0.1", self.box.port), timeout=10)
        self.addCleanup(s.close)

        def ask(typ, body):
            b = body.encode()
            s.sendall(struct.pack("<iii", len(b) + 10, 2, typ) + b + b"\0\0")
            n = struct.unpack("<i", s.recv(4))[0]
            data = b""
            while len(data) < n:
                data += s.recv(n - len(data))
            return data[8:-2].decode()
        ask(3, self.box.password)
        return lambda cmd: ask(2, cmd)

    def hammer(self, clients, n=15):
        wrong = []

        def run(tag, ask):
            for i in range(n):
                want = "%s-%d" % (tag, i)
                got = ask("echo " + want)
                if got != want:
                    wrong.append((want, got))
        threads = [threading.Thread(target=run, args=("c%d" % i, c)) for i, c in enumerate(clients)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return wrong

    def test_without_the_lock_replies_mix_and_with_it_they_never_do(self):
        wrong = self.hammer([self.bare(), self.bare()])
        self.assertGreater(len(wrong), 3)
        self.assertTrue(any(len(got) > len(want) for want, got in wrong), wrong[:3])   # glued
        r = self.common["Rcon"](self.box.rmc)
        r.close()
        self.assertEqual(r.lock_path, self.lock)
        clients = [self.client(), self.client()]
        realmc_py = os.path.join(REPO, "integration", "realmc", "realmc.py")
        if os.path.isfile(realmc_py):
            r = load_module("realmc_for_test", realmc_py).Rcon(os.path.join(self.box.rmc, "server.properties"))
            self.addCleanup(r.close)
            clients.append(r.command)
        # A script's own mc.rcon in another process, with the same lock, at the same time.
        script = subprocess.Popen([PY, "-c", "import mc\nprint(sum(mc.rcon('echo s%d' % i) != 's%d' % i "
                                             "for i in range(15)))"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                  env=dict(os.environ, PYTHONPATH=REAL_LIB, VFG_INSTANCE="locktest",
                                           VFG_MC_PROPERTIES=os.path.join(self.box.rmc, "server.properties")))
        self.assertEqual(self.hammer(clients), [])
        o, e = script.communicate(timeout=30)
        self.assertEqual(o.strip(), "0", e)

    def test_a_thread_holding_the_lock_takes_it_again_others_wait(self):
        """A thread that holds the lock re-enters through any client (vfg's, realmc's, mc's);
        another thread still waits."""
        realmc_py = os.path.join(REPO, "integration", "realmc", "realmc.py")
        if not os.path.isfile(realmc_py):
            self.skipTest("needs integration/realmc/realmc.py")
        code = """
import importlib.util, os, signal, sys, threading, time
import mc
signal.signal(signal.SIGALRM, lambda *a: (print("deadlock"), os._exit(3)))
signal.alarm(10)
common = {"__name__": "common"}
exec(compile(open(sys.argv[1]).read(), "common.py", "exec"), common)
spec = importlib.util.spec_from_file_location("realmc", sys.argv[2])
realmc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(realmc)
props = os.environ["VFG_MC_PROPERTIES"]
vfg, real = common["Rcon"](os.path.dirname(props)), realmc.Rcon(props)
outer = mc._Flock()
outer.acquire()
got = []
other = threading.Thread(target=lambda: got.append(common["Rcon"](os.path.dirname(props)).command("echo other")))
other.start()
inner = [vfg.command("echo vfg"), real.command("echo realmc"), mc.rcon("echo mc")]
time.sleep(0.05)
waited = list(got)
outer.release()
other.join(5)
print(inner, waited, got)
"""
        r = subprocess.run([PY, "-c", code, os.path.join(TOOLKIT, "remote", "common.py"), realmc_py],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30,
                           env=dict(os.environ, PYTHONPATH=REAL_LIB, VFG_INSTANCE="reentry",
                                    VFG_MC_PROPERTIES=os.path.join(self.box.rmc, "server.properties")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "['vfg', 'realmc', 'mc'] [] ['other']")

    def test_the_default_lock_path(self):
        os.environ.pop("VFG_RCON_LOCK")
        self.assertEqual(self.common["rcon_lock_path"](25575), "/tmp/vfg-rcon-25575.lock")
        r = self.common["Rcon"](self.box.rmc)
        self.assertEqual(r.lock_path, "/tmp/vfg-rcon-%d.lock" % self.box.port)
        self.assertEqual(r.command("echo hi"), "hi")
        r.close()


# ======================================================== the real feed writer

REAL_FEED = os.path.join(REPO, "bridge", "vfg_feed.py")


@unittest.skipUnless(os.path.isfile(REAL_FEED), "no bridge/vfg_feed.py beside the toolkit")
class TestRealFeedWriter(BoxCase):
    """The bridge's vfg_feed.py (copied, never edited) in place of the fake: feed post, say,
    history and context agree with the box's writer."""
    box_kwargs = {"feed_lines": 2}

    def test_post_say_history_and_context_agree(self):
        shutil.copy(REAL_FEED, os.path.join(self.box.rvfg, "lib", "vfg_feed.py"))
        r = self.vfg("feed", "post", "SYS", "", "smoke ~ $HOME\nsecond 'line'")
        self.assertEqual((r.returncode, r.stdout), (0, "3\n"), r.stderr)
        self.assertEqual(self.vfg("feed", "post", "GAME", "death Steve", "--", "-5 hearts").stdout, "4\n")
        want = [r"^SYS#3 \d\d:\d\d:\d\d \| smoke ~ \$HOME second 'line'$", r"^GAME#4 \d\d:\d\d:\d\d death Steve \| -5 hearts$"]
        if "AI" in load_module("real_vfg_feed", REAL_FEED).TYPES:
            r = self.vfg("say", "on it", "--task", "T7")
            self.assertEqual((r.returncode, r.stderr), (0, ""))
            self.assertEqual(self.vfg("feed", "post", "AI", "result T7", "T7 done: castle").stdout, "6\n")
            want += [r"^AI#5 \d\d:\d\d:\d\d say T7 \| on it$", r"^AI#6 \d\d:\d\d:\d\d result T7 \| T7 done: castle$"]
        got = self.vfg("history", "--feed", "--limit", str(len(want))).stdout.splitlines()
        self.assertEqual(len(got), len(want))
        for line, pattern in zip(got, want):
            self.assertRegex(line, pattern)
        self.assertEqual(feed_lines(self.box)[2:], got)
        self.assertEqual(self.vfg("context").stdout.splitlines()[2:], got)
        self.assertOneLine(self.vfg("feed", "post", "GAME", "Dance Steve", "x"), 1)   # the writer refuses it


if __name__ == "__main__":
    unittest.main()
