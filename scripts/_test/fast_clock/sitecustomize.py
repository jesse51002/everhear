"""Tests only: a script started with this folder on its PYTHONPATH and VFG_TEST_CLOCK_RATE set
runs on a clock that many times as fast: time.time() and time.sleep() both, from
VFG_TEST_CLOCK_EPOCH (a time.time() the test gives every script it starts, so scripts that
share a file agree on the time; else this script's start). A seed that ends by itself after a
long show (chosen_one sleeps 18 s between its lines) runs to its end in 2 s, a boss's timers
come round in a quarter of the time. The box never puts this folder on a script's path."""
import os
import time

_rate = float(os.environ.get("VFG_TEST_CLOCK_RATE") or 1)
if _rate != 1:
    _time, _sleep = time.time, time.sleep
    _epoch = float(os.environ.get("VFG_TEST_CLOCK_EPOCH") or _time())
    time.time = lambda: _epoch + (_time() - _epoch) * _rate
    time.sleep = lambda s: _sleep(max(0.0, s) / _rate)
