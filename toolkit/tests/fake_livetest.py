"""A stand-in for the engine's per-test runner, `python -m vfg_engine.livetest` (engine/vfg_engine/
livetest.py), for vfg test's tests: installed as the live box's <vfg>/engine/.venv/bin/python, which
the test server's remote program (remote/test.py) runs livetest on, it takes livetest's command
line, notes it (and the environment's VFG_ keys, USER and HOME) in <state-dir>/livetest-call.json,
and plays what the test file asks of it.

The test file is run as a module, as livetest loads it: its top-level code runs here, in the
test's sandbox. Its FAKE dict, when it has one, says what livetest would have done: {"code": 0,
"lines": [report lines], "checks": [{"item", "ok", "why"}], "errors": [load errors], "error": "why
it did not run to the end", "sleep": seconds, "log": [engine log lines]}; with none, one check
passes. It prints the lines, then (with --tail N, when the code is not 0) the log's last N lines
as LOG lines, then the one JSON line livetest ends with, and exits with the code. --timeout stops
it as livetest's own does: SIGTERM to itself, then exit 5 with "stopped" in its JSON.
"""
import json
import os
import runpy
import signal
import sys
import threading
import time


def end_line(code, checks, fake, **more):
    end = {"code": code, "passed": sum(1 for c in checks if c["ok"] is True),
           "failed": sum(1 for c in checks if c["ok"] is False),
           "cant_tell": sum(1 for c in checks if c["ok"] is None), "checks": checks,
           "engine": {"loaded": ["zap_steve"], "errors": fake.get("errors") or [], "boot_s": 0.4, "exit": 0},
           "seconds": 1.5, "timing": {}, "slow": False}
    if fake.get("error"):
        end["error"] = fake["error"]
    end.update(more)
    return end


def main(argv):
    if argv[:1] == ["-u"]:
        argv = argv[1:]
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
    env = dict((k, v) for k, v in os.environ.items() if k.startswith("VFG_") or k in ("USER", "HOME"))
    with open(os.path.join(state, "livetest-call.json"), "w") as f:
        json.dump({"argv": args, "content": contents, "options": opts, "bindings": bindings, "cwd": os.getcwd(),
                   "pythonpath": os.environ.get("PYTHONPATH"), "env": env}, f)

    def stopped(signum, frame):
        print(json.dumps(end_line(5, [], {}, stopped=True, error="timed out after %s s (--timeout)"
                                  % opts.get("timeout"))), flush=True)
        os._exit(5)
    signal.signal(signal.SIGTERM, stopped)
    if opts.get("timeout"):
        t = threading.Timer(float(opts["timeout"]), lambda: os.kill(os.getpid(), signal.SIGTERM))
        t.daemon = True
        t.start()
    fake = runpy.run_path(opts["test"]).get("FAKE") or {}
    log = fake.get("log") or ["the engine started", "loaded its content"]
    for line in fake.get("lines") or []:
        print(line, flush=True)
    time.sleep(float(fake.get("sleep") or 0))
    checks = fake.get("checks", [{"item": "it works", "ok": True, "why": "it did"}])
    code = fake.get("code", 0)
    if code != 0:
        for line in log[-int(opts.get("tail") or 0):] if int(opts.get("tail") or 0) else []:
            print("%-10s %s" % ("LOG", line), flush=True)
    print(json.dumps(end_line(code, checks, fake)), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
