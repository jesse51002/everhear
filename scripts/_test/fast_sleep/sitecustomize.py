"""Tests only: time.sleep(s) sleeps s * VFG_TEST_SLEEP_SCALE in a script started with this folder
on its PYTHONPATH, so a seed that ends by itself after a long show (chosen_one sleeps 18 s between
its lines) runs to its end in a test in 2 s. run_tests.py puts it there for those seeds alone;
the box never does. time.time() is left as it is."""
import os
import time

_scale = float(os.environ.get("VFG_TEST_SLEEP_SCALE") or 1)
if _scale != 1:
    _sleep = time.sleep
    time.sleep = lambda s: _sleep(max(0.0, s) * _scale)
