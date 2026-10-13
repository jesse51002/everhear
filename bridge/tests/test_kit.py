"""The kit's modules: imported from VFG_KIT, again when its stamp changes; a kit without them, or one
older than the bridge (without what it calls), is None, and says why."""

import logging
import os
import shutil
import sys
import tempfile
import unittest

from vfg_bridge.kit import Kit


class TestKit(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mkdtemp(prefix="vfg-kit-test-")
        self.addCleanup(shutil.rmtree, self.path, True)
        saved = {n: sys.modules.get(n) for n in ("instances", "incantations")}

        def restore():
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
            if self.path in sys.path:
                sys.path.remove(self.path)
        self.addCleanup(restore)

    def deploy(self, value, stamp, old=False):
        """Both modules, as a kit holds them; `old`: an incantations.py from before cast_meta."""
        for name in ("instances", "incantations"):
            path = os.path.join(self.path, name + ".py")
            with open(path, "w") as f:
                f.write("VALUE = %d\n\ndef warm():\n    pass\n\nclass Box:\n    pass\n" % value)
                if name == "incantations" and not old:
                    f.write("\ndef cast_meta(entry, player):\n    return {}\n")
            os.utime(path, (1e9 + value, 1e9 + value))  # a cached .pyc of the last one is stale
        tmp = os.path.join(self.path, ".vfg-stamp.tmp")  # replaced whole, as vfg writes it
        with open(tmp, "w") as f:
            f.write(stamp + "\n")
        os.replace(tmp, os.path.join(self.path, ".vfg-stamp"))

    def test_a_new_stamp_imports_them_again(self):
        self.deploy(1, "a" * 64)
        kit = Kit(self.path)
        inc, ins = kit.modules()
        self.assertEqual((inc.VALUE, ins.VALUE, inc.__name__), (1, 1, "incantations"))
        self.deploy(2, "a" * 64)
        self.assertIs(kit.modules()[0], inc)  # the same stamp: the same modules
        self.deploy(2, "b" * 64)
        inc2, ins2 = kit.modules()
        self.assertEqual((inc2.VALUE, ins2.VALUE), (2, 2))
        self.assertEqual(inc.VALUE, 1)  # a call still on the old module finishes on it

    def test_a_kit_without_them_is_none_and_said_once(self):
        kit = Kit(self.path)
        with self.assertLogs("vfg_bridge", logging.WARNING) as logs:
            self.assertIsNone(kit.modules())
            self.assertIsNone(kit.modules())
        self.assertEqual(len(logs.output), 1)
        self.assertIn("has no instances or incantations", logs.output[0])
        self.deploy(1, "c" * 64)
        self.assertEqual(kit.modules()[0].VALUE, 1)

    def test_a_kit_older_than_the_bridge_is_none_until_deployed_again(self):
        self.deploy(1, "d" * 64, old=True)
        kit = Kit(self.path)
        with self.assertLogs("vfg_bridge", logging.WARNING) as logs:
            self.assertIsNone(kit.modules())
            self.assertIsNone(kit.modules())
        self.assertEqual(len(logs.output), 1)
        self.assertIn("is older than this bridge (no incantations.cast_meta): deploy it again, vfg script deploy "
                      "--kit (deploy.sh does)", logs.output[0])
        self.assertIn("older than this bridge", kit.why)
        self.deploy(2, "e" * 64)
        self.assertEqual(kit.modules()[0].VALUE, 2)
        self.assertIsNone(kit.why)


if __name__ == "__main__":
    unittest.main()
