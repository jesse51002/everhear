"""Fault injection for path 7, not a stand-in: the REAL vfg_feed, plus a SIGKILL of the
calling process right after a LINE batch has been written -- the worst moment for the
Jev filter, after the feed has the lines and before it sets fed_at.

The Jev filter imports `vfg_feed` from VFG_LIB; the harness points VFG_LIB here and
VFG_HARNESS_REAL_FEED at the deployed writer. Arming is one file: the kill happens
only while VFG_HARNESS_CRASH_FLAG exists, and the flag is removed as it fires.
"""
import importlib.util
import os
import signal

_spec = importlib.util.spec_from_file_location("_vfg_feed_real", os.environ["VFG_HARNESS_REAL_FEED"])
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)

TYPES = _real.TYPES


def append(type, fields, text, at=None):  # noqa: A002 -- the contract names it `type`
    return append_batch([(type, fields, text, at)])[0]


def append_batch(entries):
    entries = list(entries)
    seqs = _real.append_batch(entries)
    flag = os.environ.get("VFG_HARNESS_CRASH_FLAG", "")
    if flag and os.path.exists(flag) and any(e[0] == "LINE" for e in entries):
        os.remove(flag)
        os.kill(os.getpid(), signal.SIGKILL)
    return seqs
