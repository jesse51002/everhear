"""menu: the actions as an implementer reads them, and the words the router matches on.

One place for what vfg and the brain's brief must say alike, so the two never drift:

  - an action's menu line, `nuke — Drop a nuke on a player or a place: ...`, as `vfg action list`
    and `vfg prefix` print it and the brief lists this server's made ones;
  - its long form, `vfg action show <name>`, and each action the brief gives in full: its line,
    its script, how it runs, and every value with its type, limits, default and description. The
    text itself is lib/py/vfg_actions.py's describe(), kept in the actions cache
    ($VFG_HOME/actions.json) as each action's "describe"; this reads it from there;
  - the words of a text as the router compares them (lowercased, stemmed, common words out), and
    the actions a job's words point to, best first, when Jev cannot say: vfg route's fallback,
    and the brief's own when vfg route fails.

It imports nothing of vfg's, so the brain loads it by path (brain/brain/implementers/actions.py),
as guard.py loads scripts/_lib/commands.py. Python 3.9+, standard library only, like toolkit/vfg.
"""
import difflib
import re

# ------------------------------------------------------------------ an action, in words


def line(name, entry):
    """`nuke — Drop a nuke on a player or a place: ...`: an action's name and its first line."""
    return "%s — %s" % (name, (entry or {}).get("line") or "(no line)")


def describe(name, entry):
    """An action whole, as the cache renders it (vfg_actions.describe); from an entry without that
    text, its line, its script (an engine action's op) and how it runs."""
    text = (entry or {}).get("describe")
    if isinstance(text, str) and text.strip():
        return text.rstrip("\n")
    runs = (entry or {}).get("engine")
    head = "engine %s" % runs if runs else "script %s" % ((entry or {}).get("script") or "?")
    return "%s\n%s · runs %s" % (line(name, entry), head, str((entry or {}).get("runs") or "?").replace("_", " "))


def near(name, names, n=5):
    """Up to `n` of `names` that look like `name`: spelled alike first, then those sharing a word of
    it (`incantation` → incantation_create, incantation_edit, ...)."""
    out = difflib.get_close_matches(name, names, n=n, cutoff=0.6)
    parts = set(w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) >= 3)
    for other in sorted(names):
        if len(out) >= n:
            break
        if other not in out and parts & set(re.split(r"[^a-z0-9]+", other.lower())):
            out.append(other)
    return out


# ------------------------------------------------------------------ the action cards

def action_cards(actions):
    """One card per action in the actions cache ({"actions": {name: entry}}), as route.py's cards
    are: name, kind "action", source, path (its file), first (its line), asks (none), values (the
    names of its fields: what it takes first, then the rest) and presets (none). The library's by
    name, then this server's."""
    acts = (actions or {}).get("actions") if isinstance(actions, dict) else None
    cards = []
    for name, e in sorted((acts or {}).items(), key=lambda x: (not isinstance(x[1], dict) or
                                                                x[1].get("source") != "library", x[0])):
        if not isinstance(e, dict):
            continue
        fields = e.get("fields") if isinstance(e.get("fields"), dict) else {}
        takes = [k for k in e.get("takes") or [] if k in fields]
        cards.append({"name": name, "kind": "action", "source": "library" if e.get("source") == "library" else "made",
                      "path": e.get("file") or "", "first": e.get("line") or "", "asks": [],
                      "values": dict((k, None) for k in takes + [k for k in fields if k not in takes]),
                      "presets": []})
    return cards


# --------------------------------------------------------------------- words

STOP = set("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing done down during each else even
every few for from further get gets give gives go going got had has have having he her here
hers him his how i if in into is it its just keep keeps let like make makes many me more most
much my no nor not now of off on once one only or other our out over own please put same
say says she should so some still such than that the their them then there these they this
those through to too two under until up us very was we were what when where which while who
whom why will with would you your yours script scripts preset presets player players task
job ask asked asks asking run runs running new want wants him her them it's don't can't
thing things way back now yes ok okay also report reports value values default use used
using sure set right left time times first last next around block blocks chat second seconds
minute minutes within never near nearby far until stop stopped online everyone anyone spot
stay take call long short small big bigger huge while whole already look add added tell told
show shows check work works working fix need needs try test tested update start started
restart done made write wrote line lines file files readme main vfg cmd command commands
minecraft server verify traceback instead don't won't isn't doesn't
""".split())
WORD = re.compile(r"[a-z0-9]+")


def stem(w):
    """A rough stem, the same on both sides: explode, explodes and exploded meet."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    for suffix in ("ing", "ed"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            w = w[:-len(suffix)]
            return w[:-1] if len(w) > 3 and w[-1] == w[-2] else w
    if len(w) > 4 and w.endswith(("shes", "ches", "xes", "zes", "sses")):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    return w[:-1] if len(w) > 4 and w.endswith("e") else w


def words(text):
    """The text's significant words, lowercased and stemmed; `_` splits words."""
    return {stem(w) for w in WORD.findall(text.lower().replace("_", " "))
            if len(w) >= 3 and w not in STOP}


def card_words(card):
    """A card's words: its name's, its first line's and its asks'. Value names are
    left out: damage_amount, cooldown and player are on half the library."""
    return words(" ".join([card["name"], card["first"], " ".join(card["asks"])]))


def common_words(among):
    """The words on more than a quarter of the cards (and on three or more): they say nothing
    about any one of them."""
    seen = {}
    for c in among:
        for w in card_words(c):
            seen[w] = seen.get(w, 0) + 1
    return set(w for w, n in seen.items() if n > max(2, len(among) // 4))


def actions_by_words(cards, text, among=None):
    """[(card, shared)]: the action cards that share a word with the text, those whose name
    shares the most first, then by how many they share, then by name. Words common to the
    actions (`among`, default `cards`) are not counted. The caller keeps the first seven."""
    job = words(text)
    common = common_words(cards if among is None else among)
    found = []
    for c in cards:
        shared = (job & card_words(c)) - common
        if shared:
            named = (job & words(c["name"])) - common
            found.append((-len(named), -len(shared), c["name"], c, len(shared)))
    found.sort(key=lambda x: x[:3])
    return [(x[3], x[4]) for x in found]
