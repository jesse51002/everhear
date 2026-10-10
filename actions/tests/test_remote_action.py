"""toolkit/remote/action.py end to end: the program toolkit/vfg sends (common.py + ARGS +
action.py) run on `python3 -` against the fake RCON server, its one JSON line parsed."""
from __future__ import annotations

import socket

import pytest

import mc_26_3 as R
from conftest import PY39, REMOTE, REPO, FakeWorld, action_namespace


def world() -> FakeWorld:
    return FakeWorld(players={"Alex": (197.5, 68.0, 251.5)})


# ------------------------------------------------------------------ commands mode

def test_commands_run_in_order_and_report_the_replies(serve):
    s = serve(world())
    r = s.commands(["/time set day", "give Alex minecraft:golden_apple 64", "weather rain"])
    assert r.code == 0, r.stderr
    assert r.out == {"ok": True,
                     "summary": "Set minecraft:overworld to time marker minecraft:day; "
                                "Gave 64 [Golden Apple] to Alex; Set the weather to rain",
                     "replies": ["Set minecraft:overworld to time marker minecraft:day",
                                 "Gave 64 [Golden Apple] to Alex", "Set the weather to rain"],
                     "failed": None, "ran": ["time set day", "give Alex minecraft:golden_apple 64", "weather rain"]}
    assert s.world.sent == ["time set day", "give Alex minecraft:golden_apple 64", "weather rain"]


def test_a_minecraft_error_stops_the_list(serve):
    s = serve(world())
    r = s.commands(["time set day", "give @a minecraft:nonsense", "weather rain"])
    assert r.code == 0
    assert r.out["ok"] is False
    assert r.out["failed"] == "give @a minecraft:nonsense"
    assert r.out["ran"] == ["time set day"]  # took effect: a retry must not send it again
    assert "partial" not in r.out  # Minecraft refused it: nothing of it happened
    assert r.out["summary"].startswith("Set minecraft:overworld to time marker minecraft:day; Unknown item ")
    assert "weather rain" not in s.world.sent


def test_rcon_dropping_mid_list_is_partial(serve):
    s = serve(world())

    def drops_on_weather(cmd):
        if cmd.startswith("weather"):
            raise OSError("the connection drops")  # the fake server closes it, as a crash would
        return s.world.respond(cmd)

    s.rcon.responder = drops_on_weather
    r = s.commands(["time set day", "weather rain", "give Alex minecraft:diamond 1"])
    assert r.code == 0, r.stderr
    assert r.out["ok"] is False and r.out["partial"] is True
    assert r.out["ran"] == ["time set day"] and r.out["failed"] == "weather rain"
    assert "'weather rain' was sent: it may or may not have happened" in r.out["summary"]
    assert "give Alex minecraft:diamond 1" not in s.world.sent


def test_an_offline_player_stops_the_list(serve):
    s = serve(world())
    r = s.commands(["give Steve minecraft:diamond 1", "time set night"])
    assert r.out == {"ok": False, "summary": "No player was found", "replies": ["No player was found"],
                     "failed": "give Steve minecraft:diamond 1", "ran": []}
    assert s.world.sent == ["give Steve minecraft:diamond 1"]


def test_answers_that_change_nothing_are_not_errors(serve):
    s = serve(world())
    r = s.commands(["difficulty easy", "say hello", "data get storage everhear:places"])
    assert r.out["ok"] is True
    assert r.out["summary"] == ("The difficulty did not change; it is already set to Easy; `say hello` gave no reply; "
                                "Storage everhear:places has the following contents: {}")
    assert r.out["replies"][1] == "(no reply)"


def test_an_execute_without_a_reply_may_not_have_happened(serve):
    """A failing `execute as|at ... run`, or one aimed at nobody online, replies "": never call it done."""
    s = serve(world())
    r = s.commands(["time set day", "execute at Steve run summon minecraft:zombie ~ ~ ~"])
    assert r.out == {"ok": True,
                     "summary": "Set minecraft:overworld to time marker minecraft:day; "
                                "`execute at Steve run summon minecraft:zombie ~ ~ ~` gave no reply, "
                                "so it may not have happened",
                     "replies": ["Set minecraft:overworld to time marker minecraft:day", "(no reply)"],
                     "failed": None, "ran": ["time set day", "execute at Steve run summon minecraft:zombie ~ ~ ~"]}


def test_when_every_reply_is_empty_the_summary_is_only_the_notes(serve):
    s = serve(world())
    r = s.commands(["execute as @a run say hi", "say hello"])
    assert r.out["ok"] is True
    assert r.out["replies"] == ["(no reply)", "(no reply)"]
    assert r.out["summary"] == ("`execute as @a run say hi` gave no reply, so it may not have happened; "
                                "`say hello` gave no reply")
    assert "done" not in r.out["summary"]


@pytest.mark.parametrize("bad", [
    "op Alex", "execute as @a run kick Alex", "/stop", "whitelist add Bob",
    "fill ~ ~ ~ ~5 ~5 ~5 stone", "execute as @a at @s run setblock ~ ~ ~ tnt", "return run clone 0 0 0 1 1 1 5 5 5",
    "say " + "a" * 1400, "say one\nop Alex", "",
])
def test_refused_before_anything_is_sent(serve, bad):
    s = serve(world())
    r = s.commands(["time set day", bad])
    assert r.code == 0
    assert r.out["ok"] is False and r.out["summary"].startswith("refused: ")
    assert r.out["replies"] == []
    assert s.world.sent == []


def test_rcon_down_exits_5(serve, tmp_path):
    s = serve(world())
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    s.write_props(dead)
    r = s.commands(["time set day"])
    assert r.code == 5 and r.out is None
    assert "minecraft is not running" in r.stderr
    s.write_props(dead, enabled=False)
    assert s.commands(["time set day"]).code == 5


def test_bad_args_exit_2(serve):
    s = serve(world())
    assert s.run({"mode": "commands", "commands": []}).code == 2
    assert s.run({"mode": "commands", "commands": "time set day"}).code == 2
    assert s.run({"mode": "nope"}).code == 2
    assert s.run({"mode": "handler", "name": "x", "args": {}}).code == 2
    assert s.world.sent == []


def test_mc_error_on_every_recorded_reply():
    ns = action_namespace()
    for command, reply, is_error in R.COMMANDS:
        assert ns["mc_error"](reply) is is_error, (command, reply)
    # A name that happens to start like an error is still an answer.
    assert ns["mc_error"]("Invalid Bob has the following entity data: 20.0f") is False
    assert ns["mc_error"]("Bob is not holding any item") is True


# ------------------------------------------------------------------ handler mode

def handler_run(s, source, args=None, name="probe", task="T9"):
    return s.run({"mode": "handler", "name": name, "task": task, "args": args or {},
                  "handler": source, "places": (REPO / "scripts" / "_lib" / "places.py").read_text()})


def test_a_handler_gets_its_args_mc_and_places(serve):
    s = serve(world())
    s.world.save_place("castle", [197, 68, 251], by="T23")
    source = (
        "import json\n"
        "from mc import rcon, pos, log\n"
        "import places\n"
        "def run(args):\n"
        "    log('looking for', args['who'])\n"
        "    print('a stray print')\n"
        "    p = places.get(rcon, 'castle')\n"
        "    return {'ok': True, 'summary': places.line('castle', p), 'args': args, 'at': pos(args['who'])}\n")
    r = handler_run(s, source, {"who": "Alex"})
    assert r.code == 0, r.stderr
    assert r.out == {"ok": True, "summary": "castle 197 68 251 overworld (T23)",
                     "args": {"who": "Alex", "task": "T9"}, "at": [197.5, 68.0, 251.5]}
    # mc.log and print went to stderr, named after the action.
    assert "probe looking for Alex" in r.stderr and "a stray print" in r.stderr


def test_a_handler_exception_is_ok_false(serve):
    s = serve(world())
    r = handler_run(s, "def run(args):\n    print('chatty')\n    raise KeyError('x')\n")
    assert r.code == 0
    assert r.out == {"ok": False, "summary": "handler error: KeyError: 'x'"}  # it sent nothing: no partial
    assert "Traceback" in r.stderr and "chatty" in r.stderr


def test_a_handler_raising_after_it_sent_a_command_is_partial(serve):
    s = serve(world())
    source = ("from mc import rcon\n"
              "def run(args):\n"
              "    rcon('time set night')\n"
              "    raise ValueError('half way')\n")
    r = handler_run(s, source)
    assert r.code == 0
    assert r.out == {"ok": False, "partial": True,
                     "summary": "handler error: ValueError: half way; it had sent 1 command(s), "
                                "so it may have partly happened"}
    assert s.world.sent == ["time set night"]


@pytest.mark.parametrize("source,starts", [
    ("def run(args):\n    return 'done'\n", "handler error: run() returned str"),
    ("def run(args):\n    return {'ok': 'yes', 'summary': 'x'}\n", "handler error: run() returned dict"),
    ("def run(args)\n    pass\n", "handler error: SyntaxError: "),
    ("import sys\ndef run(args):\n    sys.exit(2)\n", "handler error: SystemExit: 2"),
    ("x = 1\n", "handler error: AttributeError: "),
])
def test_a_broken_handler_is_ok_false(serve, source, starts):
    r = handler_run(serve(world()), source)
    assert r.code == 0
    assert r.out["ok"] is False and r.out["summary"].startswith(starts), r.out


def test_handler_mode_without_rcon_exits_5(serve):
    s = serve(world())
    s.write_props(1)
    r = handler_run(s, "def run(args):\n    return {'ok': True, 'summary': 'never'}\n")
    assert r.code == 5 and r.out is None


def test_handler_mode_without_the_scripts_lib_exits_3(serve, tmp_path):
    s = serve(world())
    r = s.run({"mode": "handler", "name": "probe", "task": "T1", "args": {},
               "handler": "def run(args):\n    return {'ok': True, 'summary': 'x'}\n",
               "places": (REPO / "scripts" / "_lib" / "places.py").read_text()}, vfg=tmp_path / "empty")
    assert r.code == 3 and "mc.py is missing" in r.stderr


def test_the_remote_programs_are_python_39():
    import ast
    for path in (REMOTE / "action.py", REPO / "scripts" / "_lib" / "places.py", REMOTE / "common.py"):
        ast.parse(path.read_text(), feature_version=(3, 9))
    assert PY39  # the end-to-end tests above ran under it when it is installed
