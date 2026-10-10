"""Stop: stop a running copy of an action by its name, and take away everything it made that carries its tag."""
ASKS = ["can you stop spamming it on me", "remove the wither wand", "can you remove the lightning strikes on me",
        "remove the glow on everyone", "stop that"]
from kit import values, result
import sys
import instances
import mc


def fail(summary, **facts):
    result(summary, **facts)
    sys.exit(1)


v = values()
copy = v["copy"]
if not copy:
    fail("which copy? give a running copy's name, as the list of running copies has it")
if copy == mc.INSTANCE:
    fail("%s is this stop itself" % copy)
box = instances.here()
known = copy in box.names()
action = box.meta(copy)["action"] if known else None
try:
    was = instances.stop(copy)   # SIGTERM: its cleanup takes away what carries its tag
except instances.CopyError as e:
    fail(str(e), copy=copy)
# What a cleanup that ran out of time left goes too. What a copy that ended by itself kept is
# under vfg_left_<copy> (mc.py), which no stop takes: a later copy may have this name.
tag = "vfg_" + copy
left = mc.count("@e[tag=%s]" % tag)
if left:
    mc.rcon("kill @e[tag=%s]" % tag)
if not (was or known or left):
    fail("nothing runs as %s" % copy, copy=copy)
summary = ("stopped %s" if was else "%s had already ended") % copy
if left:
    summary += "; took away %d thing%s it left" % (left, "" if left == 1 else "s")
result(summary, copy=copy, action=action, was_running=was, removed=left)
