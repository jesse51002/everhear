"""Tests for the conversation's hooks: hooks/usage.py (Stop) and hooks/after-compact.sh.

Run from the conversation folder:  python3 -m unittest test/test_usage.py
They use a temp dir for VFG_HOME and the fake vfg's calls; nothing touches ~/.vfg.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
USAGE = os.path.join(ROOT, "hooks", "usage.py")
AFTER_COMPACT = os.path.join(ROOT, "hooks", "after-compact.sh")
FAKE = os.path.join(HERE, "fake-vfg")
FIXTURE = os.path.join(HERE, "fixtures", "transcript.jsonl")
SESSION = "3f2a9c1e-7b44-4d2a-9e10-5c6d7e8f9a0b"

# What the fixture's three assistant messages add up to (see test/fixtures/transcript.jsonl):
#   msg_A haiku 5.5, two lines one id: 3 in, 1000 1h-write, 5000 read, 50 out
#   msg_B haiku 5.5: 2 in, 200 write (no split: 5-minute), 6000 read, 20 out
#   msg_C sonnet 5.5 (a turn switchModelsOnFlag moved): 100 in, 10 out
#   msg_S <synthetic>: skipped;  msg_D: half-written
# Haiku 5.5 per million: 0.10 in, 0.125 5m-write, 0.20 1h-write, 0.01 read, 0.50 out, each x5
# for a call whose prompt is over 100,000 tokens. Sonnet 5.5: 2.00 in, 10.00 out.
HAIKU_IN, HAIKU_OUT = 3 + 1000 + 5000 + 2 + 200 + 6000, 50 + 20
HAIKU_COST = (5 * 0.10 + 200 * 0.125 + 1000 * 0.20 + 11000 * 0.01 + 70 * 0.50) / 1e6
SONNET_COST = (100 * 2.00 + 10 * 10.00) / 1e6


class Env:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="vfg-conv-test-")
        self.home = os.path.join(self.tmp, "vfghome")
        self.out = os.path.join(self.tmp, "out")
        self.transcript = os.path.join(self.tmp, "transcript.jsonl")
        shutil.copy(FIXTURE, self.transcript)
        self.env = dict(os.environ, VFG_HOME=self.home, VFG_FAKE_DIR=self.out,
                        PATH=FAKE + os.pathsep + os.environ.get("PATH", ""))

    def run_hook(self, stdin=None, env=None):
        if stdin is None:
            stdin = json.dumps({"session_id": SESSION, "transcript_path": self.transcript,
                                "hook_event_name": "Stop", "stop_hook_active": False})
        return subprocess.run(["python3", USAGE], input=stdin, capture_output=True, text=True,
                              env=env or self.env, timeout=20)

    def calls(self, want=None, wait=5.0):
        """The fake vfg's recorded `usage add` calls; waits for `want` of them (they run detached)."""
        path = os.path.join(self.out, "calls.jsonl")
        deadline = time.time() + wait
        while True:
            rows = []
            if os.path.exists(path):
                with open(path) as f:
                    rows = [json.loads(l)["argv"] for l in f if l.strip()]
            rows = [r for r in rows if r[:2] == ["usage", "add"]]
            if (want is not None and len(rows) >= want) or time.time() > deadline:
                return rows
            time.sleep(0.1)

    def log(self):
        path = os.path.join(self.home, "conversation", "usage.log")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [l.split() for l in f if l.strip()]

    def state(self):
        with open(os.path.join(self.home, "conversation", "usage-state.json")) as f:
            return json.load(f)["transcripts"][self.transcript]


def opts(argv):
    """`--key value` pairs of a `vfg usage add` call."""
    out = {}
    for i in range(2, len(argv) - 1, 2):
        out[argv[i].lstrip("-")] = argv[i + 1]
    return out


class UsageHookTest(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        shutil.rmtree(self.e.tmp, ignore_errors=True)

    def test_one_row_per_model_with_the_right_sums(self):
        r = self.e.run_hook()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        calls = self.e.calls(want=2)
        self.assertEqual(len(calls), 2, calls)
        by_model = {opts(c)["model"]: opts(c) for c in calls}
        self.assertEqual(set(by_model), {"claude-haiku-5-5", "claude-sonnet-5-5"})
        h, s = by_model["claude-haiku-5-5"], by_model["claude-sonnet-5-5"]
        for row in (h, s):
            self.assertEqual(row["component"], "conversation")
            self.assertEqual(row["ref"], SESSION[:8])
        self.assertEqual((int(h["in"]), int(h["out"])), (HAIKU_IN, HAIKU_OUT))
        self.assertAlmostEqual(float(h["cost"]), HAIKU_COST, places=9)
        self.assertEqual((int(s["in"]), int(s["out"])), (100, 10))
        self.assertAlmostEqual(float(s["cost"]), SONNET_COST, places=6)
        log = self.e.log()
        self.assertEqual(len(log), 2)
        self.assertEqual({row[1] for row in log}, set(by_model))
        for row in log:
            self.assertEqual(len(row), 6)          # at model in out cost ref
            self.assertEqual(row[5], SESSION[:8])

    def test_second_run_adds_nothing(self):
        self.e.run_hook()
        self.assertEqual(len(self.e.calls(want=2)), 2)
        r = self.e.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        time.sleep(1.0)
        self.assertEqual(len(self.e.calls()), 2)
        self.assertEqual(len(self.e.log()), 2)

    def test_half_written_line_is_not_consumed(self):
        self.e.run_hook()
        self.e.calls(want=2)
        with open(self.e.transcript, "rb") as f:
            data = f.read()
        complete = data[: data.rfind(b"\n") + 1]
        self.assertEqual(self.e.state()["offset"], len(complete))
        self.assertNotIn("msg_D", self.e.state()["ids"])
        # Claude Code finishes writing the line: it is counted once, on the next run.
        half = data[len(complete):].decode()
        line = json.dumps({"type": "assistant", "uuid": "d1", "sessionId": SESSION, "requestId": "req_msg_D",
                           "message": {"id": "msg_D", "type": "message", "role": "assistant",
                                       "model": "claude-haiku-5-5", "content": [{"type": "text", "text": "x"}],
                                       "stop_reason": None, "usage": {"input_tokens": 7, "output_tokens": 9}}})
        self.assertTrue(line.startswith(half), "fixture's half line should be the start of msg_D")
        with open(self.e.transcript, "a") as f:
            f.write(line[len(half):] + "\n")
        self.e.run_hook()
        calls = self.e.calls(want=3)
        self.assertEqual(len(calls), 3)
        last = opts(calls[-1])
        self.assertEqual((last["model"], last["in"], last["out"]), ("claude-haiku-5-5", "7", "9"))
        self.assertAlmostEqual(float(last["cost"]), (7 * 0.10 + 9 * 0.50) / 1e6, places=9)

    def test_haiku_5_5_call_over_100k_prompt_tokens_pays_five_times_every_token(self):
        def call(mid, plain, write, read, out):
            return json.dumps({"type": "assistant", "message": {
                "id": mid, "model": "claude-haiku-5-5", "content": [],
                "usage": {"input_tokens": plain, "cache_creation_input_tokens": write,
                          "cache_read_input_tokens": read, "output_tokens": out}}}) + "\n"
        with open(self.e.transcript, "w") as f:
            f.write(call("msg_at", 1000, 4000, 95000, 100))       # prompt 100,000: not over
            f.write(call("msg_over", 1000, 4000, 95001, 100))     # prompt 100,001: over
        self.e.run_hook()
        calls = self.e.calls(want=1)
        self.assertEqual(len(calls), 1)
        row = opts(calls[0])
        self.assertEqual((row["in"], row["out"]), ("200001", "200"))
        at = (1000 * 0.10 + 4000 * 0.125 + 95000 * 0.01 + 100 * 0.50) / 1e6
        over = (1000 * 0.50 + 4000 * 0.625 + 95001 * 0.05 + 100 * 2.50) / 1e6
        self.assertAlmostEqual(float(row["cost"]), at + over, places=9)

    def test_unpriced_model_gets_no_cost(self):
        with open(self.e.transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {
                "id": "msg_X", "model": "claude-mystery-1", "content": [],
                "usage": {"input_tokens": 5, "output_tokens": 6}}}) + "\n")
        self.e.run_hook()
        calls = self.e.calls(want=1)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("--cost", calls[0])
        self.assertEqual(opts(calls[0])["in"], "5")
        self.assertEqual(self.e.log()[0][4], "-")

    def test_failed_vfg_leaves_the_row_in_the_log(self):
        env = dict(self.e.env, PATH="/usr/bin:/bin")       # no vfg at all
        r = self.e.run_hook(env=env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(len(self.e.log()), 2)
        self.assertIn("could not start vfg", r.stderr)

    def test_bad_input_exits_0_quietly(self):
        for stdin in ("", "not json", json.dumps({"session_id": "x", "transcript_path": "/nonexistent"})):
            r = self.e.run_hook(stdin=stdin)
            self.assertEqual((r.returncode, r.stdout), (0, ""), stdin)
        self.assertEqual(self.e.calls(wait=0.5), [])


class AfterCompactTest(unittest.TestCase):
    REMINDER = ("Your watch on vfg watch is still running: do not arm another; "
                "re-arm only when its end notice arrives.")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vfg-conv-test-")
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_hook(self, path):
        env = dict(os.environ, VFG_FAKE_DIR=self.out, PATH=path)
        return subprocess.run([AFTER_COMPACT], input="{}", capture_output=True, text=True, env=env, timeout=40)

    def test_prints_both_histories_and_the_reminder(self):
        r = self.run_hook(FAKE + os.pathsep + os.environ["PATH"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("Lines from the last 15 minutes:", r.stdout)
        self.assertIn("Game events from the last 15 minutes:", r.stdout)
        self.assertEqual(r.stdout.count("hey AI build me a castle"), 2)  # the fixture, once per history
        self.assertTrue(r.stdout.rstrip().endswith(self.REMINDER))
        with open(os.path.join(self.out, "calls.jsonl")) as f:
            argvs = [json.loads(l)["argv"] for l in f]
        self.assertEqual(argvs, [["history", "--since", "-15m", "--limit", "60"],
                                 ["history", "--events", "--since", "-15m", "--limit", "30"]])

    def test_failing_vfg_still_prints_the_reminder(self):
        bad = os.path.join(self.tmp, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "vfg"), "w") as f:
            f.write("#!/bin/sh\necho 'vfg: EC2 did not answer within 30 s' >&2\nexit 4\n")
        os.chmod(os.path.join(bad, "vfg"), 0o755)
        r = self.run_hook(bad + os.pathsep + os.environ["PATH"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("(vfg history failed)", r.stdout)
        self.assertIn("(vfg history --events failed)", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith(self.REMINDER))


if __name__ == "__main__":
    unittest.main()
