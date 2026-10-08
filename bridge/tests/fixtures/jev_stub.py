#!/usr/bin/env python3
"""A stand-in for /opt/vfg/jev/jev_filter.py that proves the vfg-jev slot on the box.

On the box, before the real Jev filter is deployed:

    cp /opt/vfg/bridge/tests/fixtures/jev_stub.py /opt/vfg/jev/jev_filter.py
    sudo systemctl restart vfg-jev
    systemctl is-active vfg-jev          # active
    tail -n 1 /opt/vfg/feed.log          # SYS#n HH:MM:SS | jev stub up
    rm /opt/vfg/jev/jev_filter.py && sudo systemctl stop vfg-jev

It imports vfg_feed the way the Jev filter does (VFG_LIB first on sys.path),
appends one SYS line and sleeps until SIGTERM.  JEV_STUB_ONCE=1 makes it exit
after the append, which is how the unit tests run it.
"""

import os
import signal
import sys
import time

sys.path.insert(0, os.environ.get("VFG_LIB", "/opt/vfg/lib"))
import vfg_feed  # noqa: E402

seq = vfg_feed.append("SYS", "", "jev stub up")
print("jev stub: fed SYS#%d" % seq, flush=True)
if os.environ.get("JEV_STUB_ONCE"):
    sys.exit(0)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    time.sleep(3600)
