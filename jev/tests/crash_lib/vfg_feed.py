"""Fault injection, not a stand-in: the stand-in `vfg_feed` (../fake_lib), plus a
SIGKILL of the calling process right after a LINE batch has been written — the
worst moment for the filter, after the feed has the lines and before fed_at is
set. Armed while the file named by $CRASH_FLAG exists; the flag is removed as
it fires. Modelled on the integration harness's shims/crash_after_feed.
"""
import importlib.util
import os
import signal

_spec = importlib.util.spec_from_file_location(
    "_vfg_feed_stand_in", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "fake_lib", "vfg_feed.py"))
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)

TYPES = _real.TYPES


def append(type, fields, text, at=None):  # noqa: A002 -- the contract names it `type`
    return append_batch([(type, fields, text, at)])[0]


def append_batch(entries):
    entries = list(entries)
    seqs = _real.append_batch(entries)
    flag = os.environ.get("CRASH_FLAG", "")
    if flag and os.path.exists(flag) and any(e[0] == "LINE" for e in entries):
        os.remove(flag)
        os.kill(os.getpid(), signal.SIGKILL)
    return seqs
