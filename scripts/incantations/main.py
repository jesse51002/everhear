"""Incantations: store, change or remove players' incantations, the phrases that fire an action the moment they are said."""
ASKS = ["make fire ball shoot a fireball", "give my fire ball a ten second cooldown", "remove my fire ball spell"]
# Run by the incantation_create, incantation_edit and incantation_remove actions. vfg checks
# everything on the PC (toolkit/vfg, run_incantation) and sends, beside the action's own fields,
# "op" and "entries": put, each a whole entry; update, each {"player", "phrase", "set"}, the
# keys that change; remove, each {"player", "phrase"}. The store is the kit's incantations.Store,
# which the bridge reads as players cast.
from kit import values, result
import json
import sys
import incantations


def fail(summary, **facts):
    result(summary, **facts)
    sys.exit(1)


def names(players):
    return " and ".join("everyone" if p == "@a" else p for p in players)


def word(x):
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer():
        return "%d" % x
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def change(old, new):
    """What an update changed, in words: cooldown 5 s; aliases fireball, fire bowl; power=4."""
    bits = []
    if old.get("cooldown_s") != new.get("cooldown_s"):
        bits.append("cooldown %s s" % word(new.get("cooldown_s")))
    if old.get("aliases") != new.get("aliases"):
        bits.append("aliases " + (", ".join(new.get("aliases") or []) or "none"))
    was, now = old.get("values") or {}, new.get("values") or {}
    for k in sorted(now):
        if was.get(k) != now[k]:
            bits.append("%s=%s" % (k, "the caster" if k in (new.get("caster_fields") or []) else word(now[k])))
    return "; ".join(bits) or "nothing new"


v = values()
sent = dict(v.items())
op, entries = sent.get("op"), sent.get("entries")
if op not in ("put", "update", "remove") or not isinstance(entries, list) or not entries:
    fail("nothing to do: incantations are made, changed and removed through vfg, which sends the entries")
store = incantations.Store()
phrase = entries[0]["phrase"]
done, replaced, missing, said = [], [], [], ""


def apply(e):
    """One entry's change; what it did lands in done, replaced, missing and said."""
    global said
    if op == "put":
        got, was = store.put(e)
        done.append(got)
        if was:
            replaced.append(got)
    elif op == "update":
        old = store.find(e["player"], e["phrase"]) or {}
        try:
            got = store.update(e["player"], e["phrase"], **e["set"])
        except KeyError:
            missing.append(e["player"])
            return
        done.append(got)
        said = said or change(old, got)
    else:
        got = store.remove(e["player"], e["phrase"])
        if got:
            done.append(got)
        else:
            missing.append(e["player"])


for e in entries:
    try:
        apply(e)
    except incantations.StoreError as exc:  # a store that cannot be read is never written over
        fail(str(exc))
version = store.version()
if not done:
    fail('%s no incantation "%s"' % (names(missing) + (" has" if len(missing) == 1 else " have"), phrase),
         missing=missing, version=version)
facts = {"ids": [e["id"] for e in done], "version": version}
players = names(e["player"] for e in done)
if op == "put":
    summary = 'stored "%s" for %s: %s' % (phrase, players, done[0]["action"])
    if done[0].get("aliases"):
        summary += ", aliases " + ", ".join(done[0]["aliases"])
    if replaced:
        summary += "; it replaced the one %s had" % names(e["player"] for e in replaced)
        facts["replaced"] = [e["id"] for e in replaced]
elif op == "update":
    summary = 'changed "%s" for %s: %s' % (phrase, players, said)
else:
    summary = 'removed "%s" for %s' % (phrase, players)
if missing:
    summary += "; %s had none" % names(missing)
    facts["missing"] = missing
result("%s; version %d" % (summary, version), **facts)
