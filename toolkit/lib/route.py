"""route: which example scripts and presets an implementer gets in full, for one job.

`vfg route` reads the implementer's brief, builds one card per example script
and preset — the library's and this server's (made/<VFG_SERVER>/) — and asks
Jev about all of them in one call, through remote/jev.py on the box: one
`choice` question per script (runs_it, close, no) and one per preset (use, no),
about the state {"job", "task", "context"}. This module is everything but the
I/O: the cards, the questions, the picks, the fallback to words, the log line.

THE PICKS are generous on purpose: every script whose runs_it and close
together reach VFG_ROUTE_LOAD (0.3), those that run the job first, and every
preset whose use reaches it that no loaded script already holds (a script holds
a preset when it has its `# --- preset: <name>` marker). A picked preset a
loaded script holds is listed under "held", with the script it is in. A script loaded that
was not needed costs a cached read; one left out costs the implementer a turn.

THE FALLBACK, when Jev is slow or down: the job's words against each card's
name, first line and asks; every card whose name shares a word with the job,
or one of whose asks is all in it, or that shares three, is loaded, leaving out words on more than a quarter of
the cards. A card Jev did not answer for is judged by words the same way.

Python 3.9+, standard library only, like toolkit/vfg.
"""
import json
import math
import re
import time

import library

# runs_it is judged on what the players would get, not on how the brief says to
# get it: briefs say "edit scripts/x/main.py" or "write scripts/x/", and asked
# "does this script do what the job asks?" Jev answered close for a job that was
# exactly an example with other values (4% runs_it over the run's briefs, 35% over
# short asks that only change values; with these words 20% and 95%), loading the same.
SCRIPT_LABELS = {
    "runs_it": "Yes: run as it is, or with its values set for this job — damage, radius, range, cooldown, "
               "who it is for or targets, a switch on or off, anything its values can set — it gives the "
               "players what the job asks for.",
    "close": "Only in part: it gives some of what the job asks for, or something like it, and its code "
             "would have to be changed or added to.",
    "no": "No: it has nothing to do with this job.",
}
# Measured live on the run's 116 briefs (2026-10-09): asked "is this preset part of
# what the job needs", Jev loaded 55% of the labelled presets at 0.3; asked whether
# having its code would help, with this yes, 97%, about 5 presets a brief of 29.
PRESET_LABELS = {
    "use": "Yes: some part of the job — when it acts, what it targets, what it does, or a show of "
           "sound, music or particles — is what this preset does, or something like it, or a change to it.",
    "no": "No: nothing in the job is like what this preset does.",
}
SOURCES = {"library": "ours", "made": "made on this server"}


# ------------------------------------------------------------------- the cards

def load_cards(repo, server, actions=None):
    """Every example script and preset, the library's then this server's, by name.

    Each card: name, kind ("script" or "preset"), source ("library" or "made"),
    path (from the repo), first (its first line), asks, values and, for a script,
    presets (the names of the presets copied into it). A script's values are the
    fields of the actions over it, from `actions` (actions.json, as vfg_actions.cache
    builds it); a preset's, the fields of its values class. With no server, the
    library's only. A name in both places (never meant to happen) keeps the library's."""
    lib = library.Library(repo, server or "main")
    over = {}
    for info in ((actions or {}).get("actions") or {}).values():
        if isinstance(info, dict) and isinstance(info.get("fields"), dict):
            key = (info.get("script"), info.get("source"))
            over.setdefault(key, {}).update(info["fields"])
    seen, cards = set(), []
    for it in lib.scripts() + lib.presets():
        if (it.kind, it.name) in seen or not it.text or (not server and it.source != "library"):
            continue
        seen.add((it.kind, it.name))
        if it.kind == "script":
            values = over.get((it.name, it.source)) or {}
        else:
            values = dict((k, None) for k in library.values_class_fields(it) or [])
        cards.append({"name": it.name, "kind": it.kind, "source": it.source, "path": it.rel,
                      "first": it.first_line or "", "asks": [a for a in it.asks or [] if isinstance(a, str)],
                      "values": values, "presets": list(it.holds) if it.kind == "script" else []})
    return cards


# --------------------------------------------------------------- the questions

def make_state(brief, task, context):
    return {"job": brief, "task": task or "", "context": context or ""}


def qname(card):
    return "%s_%s" % (card["kind"], card["name"])


def card_text(card):
    """What Jev reads about one script or preset, in the question itself."""
    what = "Example script" if card["kind"] == "script" else "Preset"
    parts = ['%s "%s" (%s): %s' % (what, card["name"], SOURCES[card["source"]],
                                   card["first"] or "(no description)")]
    if card["asks"]:
        parts.append("Players asked for it as: %s." % ", ".join('"%s"' % a for a in card["asks"]))
    if card["values"]:
        parts.append("Its values: %s." % ", ".join(sorted(card["values"])))
    if card["presets"]:
        parts.append("Built from the presets: %s." % ", ".join(card["presets"]))
    return " ".join(parts)


def make_questions(cards):
    """One choice question per card, named script_<name> or preset_<name>."""
    qs = {}
    for c in cards:
        if c["kind"] == "script":
            ask = ("An implementer is about to do the job in the state. %s The job may describe code to write "
                   "or change; judge only what it should do in the game. If this script were run, with its "
                   "values set for this job, would the players get that?")
            qs[qname(c)] = {"type": "choice", "instructions": ask % card_text(c),
                            "criteria": dict(SCRIPT_LABELS)}
        else:
            ask = ("An implementer is about to do the job in the state, by writing a script or changing one. "
                   "%s Would it help the implementer to have this preset's code in front of it for this job?")
            qs[qname(c)] = {"type": "choice", "instructions": ask % card_text(c),
                            "criteria": dict(PRESET_LABELS)}
    return qs


def estimate(*objs):
    """Tokens, estimated as the Jev filter does: UTF-8 bytes of the JSON / 3, rounded up."""
    return sum(int(math.ceil(len(json.dumps(o, ensure_ascii=False).encode("utf-8")) / 3.0)) for o in objs)


# -------------------------------------------------------------------- the picks

def _prob(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0 or v > 1:
        return None
    return float(v)


def probabilities(answer, labels):
    """{label: p} from one answer, or None when it is malformed.

    Jev's `probabilities` when it gives them for every label; otherwise the
    chosen label gets `confidence` and the others share what is left."""
    if not isinstance(answer, dict):
        return None
    probs = answer.get("probabilities")
    if isinstance(probs, dict) and all(_prob(probs.get(l)) is not None for l in labels):
        return {l: _prob(probs[l]) for l in labels}
    choice, conf = answer.get("choice"), _prob(answer.get("confidence"))
    if choice not in labels or conf is None:
        return None
    rest = (1.0 - conf) / max(1, len(labels) - 1)
    return {l: (conf if l == choice else rest) for l in labels}


def picks(cards, answers, load):
    """(scripts, presets, unanswered): the loaded picks from Jev's answers.

    Scripts with runs_it + close >= load, the runs_it verdicts first, each group
    by how sure Jev is; presets with use >= load, by use, unless a loaded script
    holds them. `unanswered` are the cards Jev gave no usable answer for."""
    scripts, presets, unanswered = [], [], []
    for c in cards:
        labels = SCRIPT_LABELS if c["kind"] == "script" else PRESET_LABELS
        p = probabilities(answers.get(qname(c)), list(labels))
        if p is None:
            unanswered.append(c)
            continue
        if c["kind"] == "script":
            # a loaded script runs the job when Jev chose runs_it, and is close otherwise
            verdict = "runs_it" if max(labels, key=lambda l: p[l]) == "runs_it" else "close"
            if p["runs_it"] + p["close"] >= load:
                scripts.append({"name": c["name"], "source": c["source"], "verdict": verdict,
                                "p_runs_it": round(p["runs_it"], 3), "p_close": round(p["close"], 3),
                                "path": c["path"], "_presets": c["presets"]})
        elif p["use"] >= load:
            presets.append({"name": c["name"], "source": c["source"], "p_use": round(p["use"], 3),
                            "path": c["path"]})
    scripts.sort(key=lambda s: (s["verdict"] != "runs_it",
                                -(s["p_runs_it"] if s["verdict"] == "runs_it" else s["p_runs_it"] + s["p_close"]),
                                s["name"]))
    presets.sort(key=lambda p: (-p["p_use"], p["name"]))
    return scripts, presets, unanswered


def drop_held(scripts, presets):
    """(presets no loaded script holds, the rest): each of the rest gets `in`, the
    first loaded script that holds it, whose copy the implementer reads instead."""
    holder = {}
    for s in scripts:
        for name in s.get("_presets") or []:
            holder.setdefault(name, s["name"])
    free, held = [], []
    for p in presets:
        if p["name"] in holder:
            held.append(dict(p, **{"in": holder[p["name"]]}))
        else:
            free.append(p)
    return free, held


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


def word_picks(cards, text, among=None):
    """(scripts, presets): every card whose name shares a word with the text, or
    one of whose asks is all in it, or that shares three words with it; most
    shared first.

    A word on more than a quarter of the library's cards (`among`, default
    `cards`) says nothing about any one of them and is not counted. Over the
    run's briefs this loaded every labelled item with about 21 of 83 cards."""
    job = words(text)
    among = cards if among is None else among
    seen = {}
    for c in among:
        for w in card_words(c):
            seen[w] = seen.get(w, 0) + 1
    common = set(w for w, n in seen.items() if n > max(2, len(among) // 4))
    scripts, presets = [], []
    for c in cards:
        shared = sorted((job & card_words(c)) - common)
        named = (job & words(c["name"])) - common or any(
            ask and ask <= job for ask in (words(a) - common for a in c["asks"]))
        if not named and len(shared) < 3:
            continue
        if c["kind"] == "script":
            scripts.append({"name": c["name"], "source": c["source"], "verdict": "words",
                            "p_runs_it": None, "p_close": None, "path": c["path"],
                            "_presets": c["presets"], "_n": len(shared)})
        else:
            presets.append({"name": c["name"], "source": c["source"], "p_use": None,
                            "path": c["path"], "_n": len(shared)})
    scripts.sort(key=lambda s: (-s["_n"], s["name"]))
    presets.sort(key=lambda p: (-p["_n"], p["name"]))
    return scripts, presets


# --------------------------------------------------------------------- route

class JevFailed(Exception):
    """Jev gave no answer in time, or an error: the router falls back to words.

    `tokens` is what the request was estimated to cost when it may still have
    reached Jev (a timeout), else 0."""

    def __init__(self, msg, tokens=0):
        Exception.__init__(self, msg)
        self.tokens = tokens


def route(cards, brief, task_id, task, context, ask, load, budget, t0=None):
    """The picks for one job, as the JSON `vfg route` prints.

    `ask(state, questions, budget)` is the Jev call (over SSH, in vfg): it
    answers {"answers", "input_tokens", "estimated_tokens", ...} or raises.
    Whatever it raises, past `budget` seconds or at once, the picks come from
    words, and `why` says what went wrong. `ms` counts from `t0` (a
    time.monotonic(); default now), so the caller can count reading the cards."""
    t0 = time.monotonic() if t0 is None else t0
    state = make_state(brief, task, context)
    questions = make_questions(cards)
    why, got, tokens = None, None, 0
    if cards:
        try:
            got = ask(state, questions, budget)
        except JevFailed as e:
            why = str(e) or "jev failed"
            tokens = e.tokens
        except Exception as e:   # anything at all: the implementer is never held up
            why = "%s: %s" % (type(e).__name__, e)
    text = "\n".join([brief, task or "", context or ""])
    if got is not None:
        scripts, presets, unanswered = picks(cards, got.get("answers") or {}, load)
        if unanswered:
            ws, wp = word_picks(unanswered, text, among=cards)
            scripts += ws
            presets += wp
        tokens = got.get("input_tokens")
        if tokens is None:
            tokens = got.get("estimated_tokens") or 0
        fallback = len(unanswered) == len(cards)
        if fallback:
            why, unanswered = "jev answered none of the questions", []
    else:
        scripts, presets = word_picks(cards, text)
        unanswered = []
        fallback = bool(cards)
    presets, held = drop_held(scripts, presets)
    for item in scripts + presets + held:
        for k in [k for k in item if k.startswith("_")]:
            del item[k]
    result = {"task": task_id, "scripts": scripts, "presets": presets, "fallback": fallback,
              "ms": int((time.monotonic() - t0) * 1000), "tokens": int(tokens)}
    if held:
        # picked, and already in a loaded script: not loaded again, but said, so the
        # brief can point at the copy (Jev's "beam_clash use 0.68" is not lost)
        result["held"] = held
    if why:
        result["why"] = why
    if unanswered:
        result["unanswered"] = [qname(c) for c in unanswered]
    return result


def log_line(result):
    """`route T7 ice_beam runs_it 0.91 · galick_gun close 0.42 · crater use 0.77 · 7 loaded · 312ms 11840tok`"""
    parts = []
    for s in result["scripts"]:
        if s["verdict"] == "words":
            parts.append(s["name"])
        else:
            p = s["p_runs_it"] if s["verdict"] == "runs_it" else s["p_close"]
            parts.append("%s %s %.2f" % (s["name"], s["verdict"], p))
    for p in result["presets"]:
        parts.append(p["name"] if p["p_use"] is None else "%s use %.2f" % (p["name"], p["p_use"]))
    for p in result.get("held") or []:
        parts.append("%s%s in %s" % (p["name"], "" if p["p_use"] is None else " use %.2f" % p["p_use"], p["in"]))
    n = len(result["scripts"]) + len(result["presets"])
    head = "route %s%s" % (result["task"] or "-", " words (%s):" % result.get("why", "no answer")
                           if result["fallback"] else "")
    return " · ".join([head + (" " + parts[0] if parts else "")] + parts[1:] +
                      ["%d loaded" % n, "%dms %dtok" % (result["ms"], result["tokens"])])
