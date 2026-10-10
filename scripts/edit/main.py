"""Edit: change values on a running copy of an action by its name; a live value lands within half a second, any other restarts the copy with the rest kept."""
ASKS = ["remove the cool down and make it faster and make it spam more", "can you reduce the cooldown on my beam",
        "make my ice beam bigger", "can you also make the range of the ice staff infinite",
        "also make the galick gun do more damage"]
from kit import values, result
import json
import sys
import instances
import mc


def fail(summary, **facts):
    result(summary, **facts)
    sys.exit(1)


def show(x):
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return "%d" % x
    if isinstance(x, (list, dict)):
        return json.dumps(x, ensure_ascii=False, separators=(",", ":"))
    return "%s" % (x,)


v = values()
copy, new = v["copy"], v["values"]
if not copy:
    fail("which copy? give a running copy's name, as the list of running copies has it")
if copy == mc.INSTANCE:
    fail("%s is this edit itself" % copy)
if not isinstance(new, dict) or not new:
    fail("no values to change on %s" % copy, copy=copy)
# vfg sends, beside the action's two fields, what its check found (toolkit/vfg, run_edit): the
# fields that change and whether each is live. Without them, the box works them out.
sent = dict(v.items())
changed, live_only = sent.get("changed"), sent.get("live_only")
box = instances.here()
if not isinstance(changed, list):
    now = box.copy(copy)["values"]
    changed, live_only = [k for k in new if now.get(k) != new[k]], None
    new = dict(now, **new)
if not changed:
    result("%s: nothing changed" % copy, copy=copy, changed={}, live=True, restarted=False)
    sys.exit(0)
try:
    got = instances.edit(copy, new, changed, live_only)
except instances.CopyError as e:
    fail(str(e), copy=copy)
said = " ".join("%s=%s" % (k, show(new[k])) for k in got["changed"])
facts = {"copy": copy, "action": got["action"], "changed": dict((k, new[k]) for k in got["changed"]),
         "live": got["live"], "restarted": not got["live"]}
if got["live"]:
    result("%s: %s, live" % (copy, said), **facts)
    sys.exit(0)
res = got.get("result") or {}
if not got["running"] and got.get("code"):
    # What it failed with, as vfg action run says it: the error it crashed on, else its
    # result(), else the last line it printed.
    tail = [l for l in box.tail(copy, 20) if l.strip()]
    why = box.error(copy) or res.get("summary") or (tail[-1].strip() if tail else "")
    fail("restarted %s with %s, but it exited with code %d%s" % (copy, said, got["code"], ": " + why if why else ""),
         **facts)
summary = "restarted %s with %s" % (copy, said)
if res.get("summary"):
    summary += ": " + res["summary"]
facts["running"] = bool(got["running"])
result(summary, **facts)
