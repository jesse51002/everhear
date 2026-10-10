"""Fake-world tests for the ray examples, for what the live test cannot reach on a realmc
server (a script started there does not see VFG_PROTECTED): the wither wand summons nothing
in a protected place, and does where it is not.

    uv run --python 3.9 python scripts/_test/test_examples_rays.py

Each example runs as on EC2 (python3 -u main.py in its folder, PYTHONPATH=_lib) against
fake_world with Steve online, looking straight down at the ground.
"""
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
LIB = os.path.join(SCRIPTS, "_lib")
sys.path.insert(0, HERE)

from fake_rcon import FakeRcon  # noqa: E402
from fake_world import World  # noqa: E402

TMP = tempfile.mkdtemp(prefix="vfg-rays-test-")


def start(name, fake, password, protected, *values):
    """An example started as on EC2, against a fake, with a protected file."""
    props = os.path.join(TMP, "server-%d.properties" % fake.port)
    with open(props, "w") as f:
        f.write("enable-rcon=true\nrcon.port=%d\nrcon.password=%s\n" % (fake.port, password))
    env = dict(os.environ, PYTHONPATH=LIB, VFG_MC_PROPERTIES=props, VFG_RCON_HOST="127.0.0.1",
               PYTHONDONTWRITEBYTECODE="1", VFG_INSTANCE=name, VFG_RUN_DIR=tempfile.mkdtemp(dir=TMP),
               VFG_PROTECTED=protected, VFG_RCON_LOCK=os.path.join(TMP, "rcon-%d.lock" % fake.port))
    return subprocess.Popen([sys.executable, "-u", "main.py"] + list(values), cwd=os.path.join(SCRIPTS, name),
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


class WitherWand(unittest.TestCase):
    def play(self, protected):
        world = World()
        world.add_player("Steve", (0.5, 64.0, 0.5), rot=(0.0, 90.0))      # looking straight down
        password = secrets.token_hex(8)
        fake = FakeRcon(password, responder=world.respond).start()
        proc = start("wither_wand", fake, password, protected, "player=Steve", "summon_count=2")
        try:
            end = time.time() + 15
            while time.time() < end and not any("wither_wand" in i for p, i in world.gives):
                time.sleep(0.1)
            world.hold("Steve", main="wither_wand")
            time.sleep(0.5)
            world.click("Steve")
            time.sleep(2.0)
            proc.send_signal(signal.SIGTERM)
            out = proc.communicate(timeout=5)[0].decode()
            self.assertEqual(proc.returncode, 0, out)
            self.assertNotIn("Traceback", out, out)
            return list(world.commands), out
        finally:
            fake.stop()

    def test_nothing_in_a_protected_place(self):
        path = os.path.join(TMP, "protected.json")
        with open(path, "w") as f:
            json.dump([{"name": "castle", "dim": "minecraft:overworld", "box": [-5, 50, -5, 5, 80, 5]}], f)
        cmds, out = self.play(path)
        self.assertTrue(any("actionbar" in c and "protected" in c for c in cmds), cmds[-20:])
        self.assertFalse([c for c in cmds if "summon wither" in c])
        self.assertFalse([c for c in cmds if c.startswith("gamerule mob_griefing f")])

    def test_withers_where_he_looks_elsewhere(self):
        cmds, out = self.play(os.path.join(TMP, "none.json"))
        summons = [c for c in cmds if "summon wither" in c]
        self.assertEqual(len(summons), 2, cmds[-30:])
        self.assertTrue(all("positioned 0.50 6" in c for c in summons), summons)   # at the ground he looks at
        self.assertIn("gamerule mob_griefing false", cmds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
