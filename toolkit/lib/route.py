"""route: which engine classes an implementer gets in full as examples, for one job.

`vfg route` reads the implementer's brief, builds one card per content class (the game engine
spec): the library's (engine/library/) and this server's (made/<VFG_SERVER>/content/), through
content.py, and asks Jev about all of them in one call, through remote/jev.py on the box: one
`choice` question per class (runs_it, close, no) about the state {"job", "task", "context"}. This
module is everything but the I/O: the cards, the questions, the picks, the fallback to words, the
log line.

THE PICKS: at most 3 classes, best first, of those whose runs_it and close together reach
VFG_ROUTE_LOAD (0.3): those that already do the job first, then by how sure Jev is (Jesse's cap: a
brief that held every close example read 111K tokens of them on every request). What the cap
leaves out is listed under "cut". The kit (engine/KIT.md) and how a class is written
(engine/WRITING.md) are in every implementer's system prompt (vfg prefix), so nothing else is
routed.

THE FALLBACK, when Jev is slow or down: the job's words against each card's name and first line;
every class whose name shares a word with the job, or that shares three, is loaded, leaving out
words on more than a quarter of the cards. A card Jev did not answer for is judged by words the
same way.

Python 3.9+, standard library only, like toolkit/vfg.
"""
import json
import math
import time

import content
import menu
from menu import STOP, WORD, card_words, stem, words  # noqa: F401  (route.words and the rest, as before)

# runs_it is judged on what the players would get from the class as it is (started with its args,
# granted, given, applied or bound), not on how the brief says to get it.
CLASS_LABELS = {
    "runs_it": "Yes: used as it is — started with its args set for this job, granted, given, put on a player "
               "or bound to a phrase — it gives the players what the job asks for.",
    "close": "Only in part: it gives some of what the job asks for, or something like it, and a new class "
             "would start from its code.",
    "no": "No: it has nothing to do with this job.",
}
KINDS = {"ability": "Ability", "item": "Item", "mark": "Mark", "actor": "Actor", "scenario": "Scenario"}
SOURCES = {"library": "the library's", "made": "made on this server"}
MOST_CLASSES = 3   # Jesse's cap (Prototype_Implementer_Harness): the examples a brief gives in full


# ------------------------------------------------------------------- the cards

def load_cards(repo, server):
    """One card per content class that reads, the library's then this server's: name (its id),
    kind, source, path (from the repo) and first (its first line)."""
    return [{"name": c.id, "kind": c.kind, "source": c.source, "path": c.rel, "first": c.first_line or ""}
            for c in content.Content(repo, server or "main").classes() if c.problem is None]


# --------------------------------------------------------------- the questions

def make_state(brief, task, context):
    return {"job": brief, "task": task or "", "context": context or ""}


def qname(card):
    return "class_%s" % card["name"]


def card_text(card):
    """What Jev reads about one class, in the question itself."""
    return '%s "%s" (%s): %s' % (KINDS.get(card["kind"], "Class"), card["name"], SOURCES[card["source"]],
                                 card["first"] or "(no description)")


def make_questions(cards):
    """One choice question per card, named class_<id>."""
    ask = ("An implementer is about to do the job in the state, with the game engine's classes. %s The job may "
           "describe code to write or change; judge only what it should do in the game. Used as it is, would "
           "this class give the players that?")
    return dict((qname(c), {"type": "choice", "instructions": ask % card_text(c), "criteria": dict(CLASS_LABELS)})
                for c in cards)


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
    """(classes, unanswered): the picks from Jev's answers, before the cap. Classes with runs_it +
    close >= load, the runs_it verdicts first, each group by how sure Jev is; `unanswered` are the
    cards Jev gave no usable answer for."""
    classes, unanswered = [], []
    labels = list(CLASS_LABELS)
    for c in cards:
        p = probabilities(answers.get(qname(c)), labels)
        if p is None:
            unanswered.append(c)
            continue
        verdict = "runs_it" if max(labels, key=lambda l: p[l]) == "runs_it" else "close"
        if p["runs_it"] + p["close"] >= load:
            classes.append({"name": c["name"], "kind": c["kind"], "source": c["source"], "verdict": verdict,
                            "p_runs_it": round(p["runs_it"], 3), "p_close": round(p["close"], 3),
                            "path": c["path"]})
    classes.sort(key=lambda s: (s["verdict"] != "runs_it",
                                -(s["p_runs_it"] if s["verdict"] == "runs_it" else s["p_runs_it"] + s["p_close"]),
                                s["name"]))
    return classes, unanswered


# --------------------------------------------------------------------- words

def word_picks(cards, text, among=None):
    """The classes picked by the words they share with the text: every card whose name shares a word
    with it, or that shares three; most shared first. A word on more than a quarter of the cards
    (`among`, default `cards`) says nothing about any one of them and is not counted."""
    job = words(text)
    common = menu.common_words(cards if among is None else among)
    found = []
    for c in cards:
        shared = sorted((job & card_words(c)) - common)
        if not ((job & words(c["name"])) - common) and len(shared) < 3:
            continue
        found.append({"name": c["name"], "kind": c["kind"], "source": c["source"], "verdict": "words",
                      "p_runs_it": None, "p_close": None, "path": c["path"], "_n": len(shared)})
    found.sort(key=lambda s: (-s["_n"], s["name"]))
    return found


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

    `ask(state, questions, budget)` is the Jev call (over SSH, in vfg): it answers {"answers",
    "input_tokens", "estimated_tokens", ...} or raises. Whatever it raises, past `budget` seconds or
    at once, the picks come from words, and `why` says what went wrong. `ms` counts from `t0` (a
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
        classes, unanswered = picks(cards, got.get("answers") or {}, load)
        if unanswered:
            classes += word_picks(unanswered, text, among=cards)
        tokens = got.get("input_tokens")
        if tokens is None:
            tokens = got.get("estimated_tokens") or 0
        fallback = len(unanswered) == len(cards)
        if fallback:
            why, unanswered = "jev answered none of the questions", []
    else:
        classes, unanswered = word_picks(cards, text), []
        fallback = bool(cards)
    cut = [c["name"] for c in classes[MOST_CLASSES:]]
    classes = classes[:MOST_CLASSES]
    for item in classes:
        item.pop("_n", None)
    result = {"task": task_id, "classes": classes, "fallback": fallback,
              "ms": int((time.monotonic() - t0) * 1000), "tokens": int(tokens)}
    if cut:
        # picked, best first, past the cap: said in the log, never in the brief
        result["cut"] = cut
    if why:
        result["why"] = why
    if unanswered:
        result["unanswered"] = [qname(c) for c in unanswered]
    return result


def log_line(result):
    """`route fire_staff_alex fire_staff runs_it 0.91 · fireball close 0.42 · 2 loaded, 1 cut · 312ms
    11840tok`: the task's name, or -, first."""
    parts = []
    for s in result["classes"]:
        if s["verdict"] == "words":
            parts.append(s["name"])
        else:
            p = s["p_runs_it"] if s["verdict"] == "runs_it" else s["p_close"]
            parts.append("%s %s %.2f" % (s["name"], s["verdict"], p))
    cut = result.get("cut") or []
    head = "route %s%s" % (result["task"] or "-", " words (%s):" % result.get("why", "no answer")
                           if result["fallback"] else "")
    return " · ".join([head + (" " + parts[0] if parts else "")] + parts[1:] +
                      ["%d loaded%s" % (len(result["classes"]), ", %d cut" % len(cut) if cut else ""),
                       "%dms %dtok" % (result["ms"], result["tokens"])])
