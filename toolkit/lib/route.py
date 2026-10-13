"""route: which example scripts, presets and actions an implementer gets in full, for one job.

`vfg route` reads the implementer's brief, builds one card per example script, preset and
action — the library's and this server's (made/<VFG_SERVER>/) — and asks Jev about all of them in
one call, through remote/jev.py on the box: one `choice` question per script (runs_it, close,
no), one per preset (use, no) and one per action (needed, no), about the state {"job", "task",
"context"}. This module is everything but the I/O: the cards, the questions, the picks, the
fallback to words, the log line.

THE PICKS: at most 3 scripts, best first, of those whose runs_it and close together reach
VFG_ROUTE_LOAD (0.3): those that run the job first, then by how sure Jev is (Jesse's cap: a
brief that held all 17 close scripts read 111K tokens of them on every request). Every preset
whose use reaches it that no loaded script already holds (a script holds a preset when it has its
`# --- preset: <name>` marker); a picked preset a loaded script holds is listed under "held",
with the script it is in. At most 7 actions, by how sure Jev is that the job needs them, of
those whose needed reaches it: the brief gives those in full, and any other is `vfg action show
<name>` away. What the caps leave out is listed under "cut".

THE FALLBACK, when Jev is slow or down: the job's words against each card's
name, first line and asks; every script or preset whose name shares a word with the job,
or one of whose asks is all in it, or that shares three, is loaded, leaving out words on more than
a quarter of the cards; the actions that share a word with it, those named first (menu.py). A card
Jev did not answer for is judged by words the same way.

Python 3.9+, standard library only, like toolkit/vfg.
"""
import json
import math
import time

import library
import menu
from menu import STOP, WORD, card_words, stem, words  # noqa: F401  (route.words and the rest, as before)

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
# An action is what the implementer runs, binds a phrase to, or copies to make its own; the brief
# gives those it needs in full, and any other is one `vfg action show` away. Measured live on five
# asks of the 2026-10-11 session: asked "will doing this job take this action?", Jev picked only
# the incantation actions and command (not ice_beam for an ice beam incantation: 0.27, nor nuke
# for T212); asked, as for a preset, whether having it in front of the implementer would help,
# with this yes, it picked those and the action the ask is like (ice_beam 0.94, nuke 0.81).
ACTION_LABELS = {
    "needed": "Yes: some part of the job is what this action does, or something like it, or a change to it — "
              "the implementer may run it, bind a phrase or an item to it, change a copy of it, or copy it to "
              "make its own.",
    "no": "No: nothing in the job is like what this action does.",
}
LABELS = {"script": SCRIPT_LABELS, "preset": PRESET_LABELS, "action": ACTION_LABELS}
SOURCES = {"library": "ours", "made": "made on this server"}
# Jesse's caps (Prototype_Implementer_Harness): the scripts and the actions a brief gives in full.
MOST_SCRIPTS = 3
MOST_ACTIONS = 7


# ------------------------------------------------------------------- the cards

def load_cards(repo, server, actions=None):
    """Every example script and preset, the library's then this server's, by name; then, with
    `actions` (actions.json, as vfg_actions.cache builds it), every action (menu.action_cards).

    Each card: name, kind ("script", "preset" or "action"), source ("library" or "made"),
    path (from the repo), first (its first line), asks, values and, for a script,
    presets (the names of the presets copied into it). A script's values are the
    fields of the actions over it; a preset's, the fields of its values class; an action's, its
    own. With no server, the library's only. A name in both places (never meant to happen) keeps
    the library's."""
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
    return cards + [c for c in menu.action_cards(actions) if server or c["source"] == "library"]


# --------------------------------------------------------------- the questions

def make_state(brief, task, context):
    return {"job": brief, "task": task or "", "context": context or ""}


def qname(card):
    return "%s_%s" % (card["kind"], card["name"])


def card_text(card):
    """What Jev reads about one script, preset or action, in the question itself."""
    what = {"script": "Example script", "preset": "Preset", "action": "Action"}[card["kind"]]
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
    """One choice question per card, named script_<name>, preset_<name> or action_<name>."""
    qs = {}
    for c in cards:
        if c["kind"] == "action":
            ask = ("An implementer is about to do the job in the state, by running actions, binding a phrase to "
                   "one, or making a new one from one. %s Would it help the implementer to have this action's "
                   "values in front of it for this job?")
            qs[qname(c)] = {"type": "choice", "instructions": ask % card_text(c),
                            "criteria": dict(ACTION_LABELS)}
        elif c["kind"] == "script":
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
    """(scripts, presets, actions, unanswered): the picks from Jev's answers, before the caps.

    Scripts with runs_it + close >= load, the runs_it verdicts first, each group
    by how sure Jev is; presets with use >= load, by use, unless a loaded script
    holds them; actions with needed >= load, by needed. `unanswered` are the cards
    Jev gave no usable answer for."""
    scripts, presets, actions, unanswered = [], [], [], []
    for c in cards:
        labels = LABELS[c["kind"]]
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
        elif c["kind"] == "action":
            if p["needed"] >= load:
                actions.append({"name": c["name"], "source": c["source"], "p_needed": round(p["needed"], 3),
                                "path": c["path"]})
        elif p["use"] >= load:
            presets.append({"name": c["name"], "source": c["source"], "p_use": round(p["use"], 3),
                            "path": c["path"]})
    scripts.sort(key=lambda s: (s["verdict"] != "runs_it",
                                -(s["p_runs_it"] if s["verdict"] == "runs_it" else s["p_runs_it"] + s["p_close"]),
                                s["name"]))
    presets.sort(key=lambda p: (-p["p_use"], p["name"]))
    actions.sort(key=lambda a: (-a["p_needed"], a["name"]))
    return scripts, presets, actions, unanswered


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

def word_picks(cards, text, among=None):
    """(scripts, presets, actions), picked by the words they share with the text.

    Scripts and presets: every card whose name shares a word with the text, or
    one of whose asks is all in it, or that shares three words with it; most
    shared first. A word on more than a quarter of the library's cards (`among`,
    default `cards`) says nothing about any one of them and is not counted. Over the
    run's briefs this loaded every labelled item with about 21 of 83 cards.

    Actions: every one that shares a word with it, those whose name shares most first
    (menu.actions_by_words); a word common among the actions is not counted. The
    caller caps them. Scripts and presets are counted among themselves, actions among
    actions, so the actions change nothing of the examples' picks."""
    job = words(text)
    among = cards if among is None else among
    common = menu.common_words([c for c in among if c["kind"] != "action"])
    scripts, presets = [], []
    for c in cards:
        if c["kind"] == "action":
            continue
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
    actions = [{"name": c["name"], "source": c["source"], "p_needed": None, "path": c["path"]}
               for c, _n in menu.actions_by_words([c for c in cards if c["kind"] == "action"], text,
                                                  [c for c in among if c["kind"] == "action"])]
    return scripts, presets, actions


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
        scripts, presets, actions, unanswered = picks(cards, got.get("answers") or {}, load)
        if unanswered:
            ws, wp, wa = word_picks(unanswered, text, among=cards)
            scripts += ws
            presets += wp
            actions += wa
        tokens = got.get("input_tokens")
        if tokens is None:
            tokens = got.get("estimated_tokens") or 0
        fallback = len(unanswered) == len(cards)
        if fallback:
            why, unanswered = "jev answered none of the questions", []
    else:
        scripts, presets, actions = word_picks(cards, text)
        unanswered = []
        fallback = bool(cards)
    # The caps, before what the loaded scripts hold: a preset held only by a script cut here is
    # loaded itself.
    cut = {"scripts": [s["name"] for s in scripts[MOST_SCRIPTS:]],
           "actions": [a["name"] for a in actions[MOST_ACTIONS:]]}
    scripts, actions = scripts[:MOST_SCRIPTS], actions[:MOST_ACTIONS]
    presets, held = drop_held(scripts, presets)
    for item in scripts + presets + held:
        for k in [k for k in item if k.startswith("_")]:
            del item[k]
    result = {"task": task_id, "scripts": scripts, "presets": presets, "actions": actions,
              "fallback": fallback, "ms": int((time.monotonic() - t0) * 1000), "tokens": int(tokens)}
    if held:
        # picked, and already in a loaded script: not loaded again, but said, so the
        # brief can point at the copy (Jev's "beam_clash use 0.68" is not lost)
        result["held"] = held
    if cut["scripts"] or cut["actions"]:
        # picked, best first, past the caps: said in the log, never in the brief
        result["cut"] = dict((k, v) for k, v in cut.items() if v)
    if why:
        result["why"] = why
    if unanswered:
        result["unanswered"] = [qname(c) for c in unanswered]
    return result


def log_line(result):
    """`route ice_beam_alex ice_beam runs_it 0.91 · galick_gun close 0.42 · crater use 0.77 · ice_beam needed 0.93
    · 3 loaded, 2 cut · 1 action · 312ms 11840tok`: the task's name, or -, first."""
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
    for a in result.get("actions") or []:
        parts.append("%s %s" % (a["name"], "by words" if a.get("p_needed") is None else "needed %.2f" % a["p_needed"]))
    cut = result.get("cut") or {}
    n = len(result["scripts"]) + len(result["presets"])
    m = len(result.get("actions") or [])
    head = "route %s%s" % (result["task"] or "-", " words (%s):" % result.get("why", "no answer")
                           if result["fallback"] else "")
    return " · ".join([head + (" " + parts[0] if parts else "")] + parts[1:] +
                      ["%d loaded%s" % (n, ", %d more scripts cut" % len(cut["scripts"]) if cut.get("scripts") else ""),
                       "%d action%s%s" % (m, "" if m == 1 else "s",
                                          ", %d more cut" % len(cut["actions"]) if cut.get("actions") else ""),
                       "%dms %dtok" % (result["ms"], result["tokens"])])
