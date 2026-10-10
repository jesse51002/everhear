#!/usr/bin/env bash
# Runs the decision cases through `claude -p` in this folder (so CLAUDE.md and the
# settings load) against the fake vfg, then checks the calls it recorded.
#
#   test/decide.sh                 every case in test/cases/
#   test/decide.sh join done       only these cases
#
#   VFG_TEST_MODEL   model for the runs (default claude-haiku-5-5, as the session runs)
#   VFG_TEST_EFFORT  effort (default low)
#
# The settings are also passed with --settings: until the owner has trusted this
# folder once (the first ./start asks), Claude Code ignores the project file's
# allow rules, and every vfg call would be denied. --no-session-persistence keeps
# test runs out of `./start --continue`. --setting-sources and --strict-mcp-config
# isolate the run from the owner's global setup, as ./start does; leaving `local`
# out also skips the owner's CLAUDE.local.md, and the cases' owner, Sunny (TEST_OWNER),
# is named with --append-system-prompt instead.
#
# Prints PASS or FAIL per case; a FAIL is rerun once before it counts, since a
# model judges each case. Exits non-zero on any FAIL. Each case's calls, and what
# claude printed, are left in test/out/<case>/.
#
# A case file has optional `task_start: N` (the fake's next T<n>),
# `start_offset: S` (start was S seconds ago, default 1200), `say_fails: <message>`
# (every `vfg say` fails with it), `raw: yes` (the prompt is sent as written, with
# no TEST preamble — for the start flow) and `timeout: S` (claude is stopped after
# S seconds, default 300; a started session keeps its watch open), then a `state:`
# section, a `prompt:` section (the feed lines of the notification) and a
# `check:` section. {t-N} in a line is the UTC time N seconds ago, as HH:MM:SS.
# Checks, one per line:
#   say|say_public|send <op> N     count of `vfg say` / public `vfg say` / `vfg orch send` (op: == >= <=)
#   say_to <player> >= N           whispers to that player
#   says_mention <text>            some `vfg say` names it (case-insensitive)
#   told <player>                  a whisper to them, or a `vfg say` naming them
#   send_mentions <text>           some send's task or context contains it
#   context_mentions <text>        some send's context contains it
#   no_task_mentions <regex>       no send's task matches it (case-insensitive)
#   calls <words…> <op> N          count of calls starting with those words (e.g. `calls orch start >= 1`)
# Every case also fails on any call to `cmd`, `script`, `orch reply` or `feed`.

set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT"
MODEL="${VFG_TEST_MODEL:-claude-haiku-5-5}"
TEST_OWNER=Sunny
EFFORT="${VFG_TEST_EFFORT:-low}"
unset ANTHROPIC_API_KEY
command -v claude >/dev/null || { echo "claude is not on PATH" >&2; exit 1; }

read -r -d '' CHECKER <<'PY'
import json, re, sys, time
from datetime import datetime, timedelta, timezone

PREAMBLE = ("TEST: what follows is a notification from your watch, not a message from the owner. "
            "Start is done and your watch is task w1.")

def parse(path):
    case = {"task_start": "1", "start_offset": "1200", "raw": "no", "state": [], "prompt": [], "check": []}
    section = None
    for line in open(path, encoding="utf-8").read().splitlines():
        m = re.match(r"^(task_start|start_offset|raw|timeout|say_fails):\s*(.*?)\s*$", line)
        if m:
            case[m.group(1)] = m.group(2)
        elif line in ("state:", "prompt:", "check:"):
            section = line[:-1]
        elif section and line.strip():
            case[section].append(line)
    return case

def clock(text):
    now = datetime.now(timezone.utc)
    return re.sub(r"\{t(?:-(\d+))?\}",
                  lambda m: (now - timedelta(seconds=int(m.group(1) or 0))).strftime("%H:%M:%S"), text)

def build(path):
    case = parse(path)
    if case["raw"] == "yes":
        print(clock("\n".join(case["prompt"])))
        return
    head = PREAMBLE + " Start time was {t-%s}." % case["start_offset"]
    if case["state"]:
        head += " " + " ".join(case["state"])
    print(clock(head + "\n\n" + "\n".join(case["prompt"])))

def words(argv):
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] == "--timeout" else 1
    return argv[i:]

def check(path, calls_path):
    case = parse(path)
    calls = []
    try:
        calls = [words(json.loads(l)["argv"]) for l in open(calls_path) if l.strip()]
    except OSError:
        pass
    says, sends, bad = [], [], []
    for c in calls:
        if c[:1] == ["say"]:
            text = c[1] if len(c) > 1 else ""
            to = c[c.index("--to") + 1] if "--to" in c and c.index("--to") + 1 < len(c) else None
            says.append((text, to))
        elif c[:2] == ["orch", "send"]:
            task = c[2] if len(c) > 2 else ""
            ctx = c[c.index("--context") + 1] if "--context" in c and c.index("--context") + 1 < len(c) else ""
            sends.append((task, ctx))
        elif c[:1] in (["cmd"], ["script"], ["feed"]) or c[:2] == ["orch", "reply"]:
            bad.append(" ".join(c))
    fails = ["forbidden call: vfg " + b for b in bad]
    ops = {"==": lambda a, b: a == b, ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}
    low = lambda s: s.lower()
    for chk in case["check"]:
        p = chk.split(None, 1)
        kind, arg = p[0], (p[1] if len(p) > 1 else "")
        ok = True
        if kind in ("say", "say_public", "send"):
            op, n = arg.split()
            count = {"say": len(says), "say_public": sum(1 for s in says if s[1] is None),
                     "send": len(sends)}[kind]
            ok = ops[op](count, int(n))
            detail = "%s is %d" % (kind, count)
        elif kind == "say_to":
            who, op, n = arg.split()
            count = sum(1 for s in says if s[1] and s[1].lower() == who.lower())
            ok = ops[op](count, int(n))
            detail = "say_to %s is %d" % (who, count)
        elif kind == "says_mention":
            ok = any(low(arg) in low(t) for t, _ in says)
            detail = "no say names %r" % arg
        elif kind == "told":
            ok = any((to and to.lower() == arg.lower()) or low(arg) in low(t) for t, to in says)
            detail = "%s was not told" % arg
        elif kind == "send_mentions":
            ok = any(low(arg) in low(t + " " + c) for t, c in sends)
            detail = "no send mentions %r" % arg
        elif kind == "context_mentions":
            ok = any(low(arg) in low(c) for _, c in sends)
            detail = "no send context mentions %r" % arg
        elif kind == "calls":
            parts = arg.split()
            want, op, n = parts[:-2], parts[-2], parts[-1]
            count = sum(1 for c in calls if c[:len(want)] == want)
            ok = ops[op](count, int(n))
            detail = "calls %s is %d" % (" ".join(want), count)
        elif kind == "no_task_mentions":
            hits = [t for t, _ in sends if re.search(arg, t, re.I)]
            ok = not hits
            detail = "task matches %r: %s" % (arg, hits)
        else:
            ok, detail = False, "unknown check %r" % kind
        if not ok:
            fails.append("%s (%s)" % (chk, detail))
    for f in fails:
        print("    " + f)
    print("    says: %s" % json.dumps(says, ensure_ascii=False))
    print("    sends: %s" % json.dumps(sends, ensure_ascii=False))
    sys.exit(1 if fails else 0)

if sys.argv[1] == "build":
    build(sys.argv[2])
else:
    check(sys.argv[2], sys.argv[3])
PY

if [ "$#" -gt 0 ]; then
  cases=()
  for name in "$@"; do cases+=("test/cases/${name%.case}.case"); done
else
  cases=(test/cases/*.case)
fi

rm -rf test/out
mkdir -p test/out
failed=0
for file in "${cases[@]}"; do
  name="$(basename "$file" .case)"
  [ -f "$file" ] || { echo "FAIL $name (no such case)"; failed=1; continue; }
  start=$(sed -n 's/^task_start:[[:space:]]*//p' "$file")
  limit=$(sed -n 's/^timeout:[[:space:]]*//p' "$file")
  sayfail=$(sed -n 's/^say_fails:[[:space:]]*//p' "$file")
  result=FAIL
  for attempt in 1 2; do
    out="$ROOT/test/out/$name"
    rm -rf "$out"
    mkdir -p "$out"
    prompt="$(python3 -c "$CHECKER" build "$file")"
    printf '%s\n' "$prompt" > "$out/prompt.txt"
    PATH="$ROOT/test/fake-vfg:$PATH" VFG_FAKE_DIR="$out" VFG_HOME="$out/vfg-home" \
      VFG_FAKE_TASK_START="${start:-1}" VFG_FAKE_SAY_FAIL="$sayfail" \
      timeout "${limit:-300}" claude -p --model "$MODEL" --effort "$EFFORT" --no-session-persistence \
      --setting-sources project --strict-mcp-config \
      --append-system-prompt "The owner's Minecraft name is $TEST_OWNER: a line from player $TEST_OWNER is the owner speaking in game." \
      --settings "$ROOT/.claude/settings.json" "$prompt" < /dev/null > "$out/claude.txt" 2>&1
    sleep 1   # the Stop hook's detached `vfg usage add` may still be landing
    if python3 -c "$CHECKER" check "$file" "$out/calls.jsonl" > "$out/check.txt"; then
      result=PASS
      break
    fi
    [ "$attempt" = 1 ] && echo "  $name: failed once, rerunning"
  done
  echo "$result $name"
  [ "$result" = PASS ] || { sed 's/^/  /' "$ROOT/test/out/$name/check.txt"; failed=1; }
done
exit "$failed"
