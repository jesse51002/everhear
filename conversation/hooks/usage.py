#!/usr/bin/env python3
"""Stop hook: writes the conversation's usage rows.

Claude Code runs it after every turn with the hook's JSON on stdin. It reads the
session's transcript from where it stopped last time, adds up the tokens of each
new assistant message per model, appends one line per model to
~/.vfg/conversation/usage.log and starts `vfg usage add --component conversation`
detached, so a turn never waits on SSH. It prints nothing and always exits 0.

State lives in ${VFG_HOME:-~/.vfg}/conversation/: usage-state.json (per
transcript: the byte offset read up to and the last 500 message ids counted)
and usage.log (`at model in out cost ref`, cost `-` when unpriced).
"""

import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# US dollars per million tokens, keyed by model-id prefix (the longest matching
# prefix wins). From https://platform.claude.com/docs/en/about-claude/pricing on
# 2026-10-09; ./start runs claude-haiku-5-5. Haiku 5.5 is priced by prompt length:
# a call whose prompt (all its input tokens, cache reads and writes included) is
# over LONG_PROMPT tokens pays its "long" prices, every token of that call. The
# other rows cover a turn that `switchModelsOnFlag` moves to another model. It is
# what the tokens would cost on the API; the subscription bills nothing per token.
PRICES = {
    "claude-haiku-5-5":  {"input": 0.10, "cache_write_5m": 0.125, "cache_write_1h": 0.20, "cache_read": 0.01, "output": 0.50,
                          "long": {"input": 0.50, "cache_write_5m": 0.625, "cache_write_1h": 1.00, "cache_read": 0.05, "output": 2.50}},
    "claude-sonnet-5-5": {"input": 2.00, "cache_write_5m": 2.50, "cache_write_1h": 4.00, "cache_read": 0.10, "output": 10.00},
    "claude-sonnet-5":   {"input": 2.00, "cache_write_5m": 2.50, "cache_write_1h": 4.00, "cache_read": 0.20, "output": 10.00},
    "claude-sonnet-4-6": {"input": 3.00, "cache_write_5m": 3.75, "cache_write_1h": 6.00, "cache_read": 0.30, "output": 15.00},
    "claude-opus-5-5":   {"input": 4.00, "cache_write_5m": 5.00, "cache_write_1h": 8.00, "cache_read": 0.20, "output": 20.00},
    "claude-opus-5":     {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-haiku-4-5":  {"input": 1.00, "cache_write_5m": 1.25, "cache_write_1h": 2.00, "cache_read": 0.10, "output": 5.00},
}
LONG_PROMPT = 100000
KINDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")

KEEP_IDS = 500          # message ids remembered per transcript
KEEP_TRANSCRIPTS = 50   # transcripts remembered in the state file


def state_dir():
    home = os.environ.get("VFG_HOME") or os.path.join(os.path.expanduser("~"), ".vfg")
    return os.path.join(home, "conversation")


def now_iso():
    t = datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (t.microsecond // 1000)


def price_for(model, prompt=0):
    """The model's prices for one call whose prompt is `prompt` tokens; None when unpriced."""
    for prefix in sorted(PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            prices = PRICES[prefix]
            return prices["long"] if prompt > LONG_PROMPT and "long" in prices else prices
    return None


def num(value):
    return value if isinstance(value, (int, float)) and value > 0 else 0


def read_new(path, offset):
    """Bytes from offset to the end of the last complete line, and the new offset."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size < offset:      # not the file we read before: start again
            offset = 0
        f.seek(offset)
        data = f.read(size - offset)
    end = data.rfind(b"\n")
    if end < 0:
        return b"", offset
    return data[:end + 1], offset + end + 1


def tally(data, seen):
    """Per model: in, out and cost (None when unpriced). Adds new ids to seen."""
    totals = {}
    for raw in data.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        mid, model, usage = message.get("id"), message.get("model"), message.get("usage")
        if not mid or not model or model == "<synthetic>" or not isinstance(usage, dict):
            continue
        if mid in seen:
            continue        # one line per content block, each repeating the usage
        seen.append(mid)

        plain = num(usage.get("input_tokens"))
        write = num(usage.get("cache_creation_input_tokens"))
        read = num(usage.get("cache_read_input_tokens"))
        out = num(usage.get("output_tokens"))
        split = usage.get("cache_creation")
        write_1h = num(split.get("ephemeral_1h_input_tokens")) if isinstance(split, dict) else 0
        write_1h = min(write_1h, write)
        write_5m = write - write_1h          # unsplit cache writes are priced as 5-minute

        prompt = plain + write + read
        kinds = {"input": plain, "cache_write_5m": write_5m, "cache_write_1h": write_1h,
                 "cache_read": read, "output": out}
        cost = cost_of(model, kinds, prompt)      # each call at its own prompt's prices
        t = totals.setdefault(model, {"in": 0, "out": 0, "cost": 0.0})
        t["in"] += prompt
        t["out"] += out
        t["cost"] = None if cost is None or t["cost"] is None else t["cost"] + cost
    return totals


def cost_of(model, kinds, prompt=0):
    """One call's cost: its tokens of each kind at the prices for its prompt length."""
    prices = price_for(model, prompt)
    if prices is None:
        return None
    return sum(kinds[k] * prices[k] for k in KINDS) / 1e6


def load_state(path):
    try:
        with open(path) as f:
            state = json.load(f)
        if isinstance(state, dict) and isinstance(state.get("transcripts"), dict):
            return state
    except (OSError, ValueError):
        pass
    return {"transcripts": {}}


def save_state(path, state):
    items = sorted(state["transcripts"].items(), key=lambda kv: kv[1].get("updated", ""), reverse=True)
    state["transcripts"] = dict(items[:KEEP_TRANSCRIPTS])
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def record(model, t, cost, ref, log_path):
    cost_text = "%.9f" % cost if cost is not None else "-"
    try:
        with open(log_path, "a") as f:
            f.write("%s %s %d %d %s %s\n" % (now_iso(), model, t["in"], t["out"], cost_text, ref))
    except OSError as e:
        print("usage.py: could not append to usage.log: %s" % e, file=sys.stderr)
    argv = ["vfg", "usage", "add", "--component", "conversation", "--model", model,
            "--in", str(t["in"]), "--out", str(t["out"])]
    if cost is not None:
        argv += ["--cost", "%.9f" % cost]
    argv += ["--ref", ref]
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as e:
        print("usage.py: could not start vfg usage add: %s" % e, file=sys.stderr)


def main():
    hook = json.load(sys.stdin)
    transcript = hook.get("transcript_path")
    session = str(hook.get("session_id") or "")
    if not transcript or not os.path.isfile(transcript):
        return
    ref = session[:8] or "-"

    folder = state_dir()
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "usage.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = os.path.join(folder, "usage-state.json")
        state = load_state(state_path)
        entry = state["transcripts"].get(transcript, {})
        offset = entry.get("offset", 0) if isinstance(entry.get("offset"), int) else 0
        seen = [i for i in entry.get("ids", []) if isinstance(i, str)]

        data, offset = read_new(transcript, offset)
        totals = tally(data, seen)

        rows = []
        for model, t in sorted(totals.items()):
            if t["in"] or t["out"]:
                rows.append((model, t, t["cost"]))
        log_path = os.path.join(folder, "usage.log")
        state["transcripts"][transcript] = {"offset": offset, "ids": seen[-KEEP_IDS:], "updated": now_iso()}
        save_state(state_path, state)
        for model, t, cost in rows:
            record(model, t, cost, ref, log_path)


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # a hook must never fail the turn
        if not isinstance(e, SystemExit):
            print("usage.py: %s: %s" % (type(e).__name__, e), file=sys.stderr)
    sys.exit(0)
