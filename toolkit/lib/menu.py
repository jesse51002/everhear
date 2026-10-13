"""menu: an action as `vfg action list` and `vfg action show` print it, and the words the router
matches on.

  - an action's menu line, `engine_grant — Give a player one of the engine's abilities ...`;
  - its long form, `vfg action show <name>`: its line, the engine op it goes to, and every value
    with its type, limits, default and description. The text itself is lib/py/vfg_actions.py's
    describe(), kept in the actions cache ($VFG_HOME/actions.json) as each action's "describe";
    this reads it from there;
  - the words of a text as the router compares them (lowercased, stemmed, common words out): vfg
    route's fallback when Jev cannot say.

It imports nothing of vfg's, so the brain can load it by path. Python 3.9+, standard library only,
like toolkit/vfg.
"""
import difflib
import re

# ------------------------------------------------------------------ an action, in words


def line(name, entry):
    """`nuke — Drop a nuke on a player or a place: ...`: an action's name and its first line."""
    return "%s — %s" % (name, (entry or {}).get("line") or "(no line)")


def describe(name, entry):
    """An action whole, as the cache renders it (vfg_actions.describe); from an entry without that
    text, its line and the engine op it goes to."""
    text = (entry or {}).get("describe")
    if isinstance(text, str) and text.strip():
        return text.rstrip("\n")
    return "%s\nengine %s" % (line(name, entry), (entry or {}).get("engine") or "?")


def near(name, names, n=5):
    """Up to `n` of `names` that look like `name`: spelled alike first, then those sharing a word of
    it (`grant` → engine_grant, ...)."""
    out = difflib.get_close_matches(name, names, n=n, cutoff=0.6)
    parts = set(w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) >= 3)
    for other in sorted(names):
        if len(out) >= n:
            break
        if other not in out and parts & set(re.split(r"[^a-z0-9]+", other.lower())):
            out.append(other)
    return out


# ------------------------------------------------------------------ the action cards

# --------------------------------------------------------------------- words

STOP = set("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing done down during each else even
every few for from further get gets give gives go going got had has have having he her here
hers him his how i if in into is it its just keep keeps let like make makes many me more most
much my no nor not now of off on once one only or other our out over own please put same
say says she should so some still such than that the their them then there these they this
those through to too two under until up us very was we were what when where which while who
whom why will with would you your yours class classes kind kinds player players task
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
    """A card's words: its name's and its first line's. Field names are left out: radius, damage
    and cooldown_s are on half the library."""
    return words(" ".join([card["name"], card["first"]]))


def common_words(among):
    """The words on more than a quarter of the cards (and on three or more): they say nothing
    about any one of them."""
    seen = {}
    for c in among:
        for w in card_words(c):
            seen[w] = seen.get(w, 0) + 1
    return set(w for w, n in seen.items() if n > max(2, len(among) // 4))
