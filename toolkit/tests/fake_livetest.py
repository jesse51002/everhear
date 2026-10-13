"""A stand-in for the engine's per-test runner, `python -m vfg_engine.livetest` (engine/vfg_engine/
livetest.py), for vfg test's tests: installed as a test server's <vfg>/engine/.venv/bin/python, it
takes livetest's command line, notes it in <state-dir>/livetest-call.json, and plays what the test
file asks of it.

The test file is run as a module. Its FAKE dict, when it has one, says what livetest would have
done: {"code": 0, "lines": [report lines], "checks": [{"item", "ok", "why"}], "errors": [load
errors], "error": "why it did not run to the end", "sleep": seconds, "log": [engine log lines]};
with none, one check passes. It prints the lines, then the one JSON line livetest ends with, writes
the engine's log to <state-dir>/engine.log, and exits with the code.
"""
import json
import os
import runpy
import sys
import time


def main(argv):
    if argv[:2] != ["-m", "vfg_engine.livetest"]:
        print("fake python: only -m vfg_engine.livetest", file=sys.stderr)
        return 2
    args, opts, contents = argv[2:], {}, []
    i = 0
    while i < len(args):
        if args[i] == "--content":
            contents.append(args[i + 1])
        else:
            opts[args[i].lstrip("-")] = args[i + 1]
        i += 2
    state = opts["state-dir"]
    os.makedirs(state, exist_ok=True)
    bindings = None
    if opts.get("bindings"):
        with open(opts["bindings"]) as f:
            bindings = json.load(f)
    with open(os.path.join(state, "livetest-call.json"), "w") as f:
        json.dump({"argv": args, "content": contents, "options": opts, "bindings": bindings, "cwd": os.getcwd(),
                   "pythonpath": os.environ.get("PYTHONPATH")}, f)
    fake = runpy.run_path(opts["test"]).get("FAKE") or {}
    with open(os.path.join(state, "engine.log"), "w") as f:
        f.write("".join(line + "\n" for line in fake.get("log") or ["the engine started", "loaded its content"]))
    for line in fake.get("lines") or []:
        print(line, flush=True)
    time.sleep(float(fake.get("sleep") or 0))
    checks = fake.get("checks", [{"item": "it works", "ok": True, "why": "it did"}])
    end = {"code": fake.get("code", 0), "passed": sum(1 for c in checks if c["ok"] is True),
           "failed": sum(1 for c in checks if c["ok"] is False),
           "cant_tell": sum(1 for c in checks if c["ok"] is None), "checks": checks,
           "engine": {"loaded": ["zap_steve"], "errors": fake.get("errors") or [], "boot_s": 0.4, "exit": 0},
           "seconds": 1.5}
    if fake.get("error"):
        end["error"] = fake["error"]
    print(json.dumps(end), flush=True)
    return end["code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
