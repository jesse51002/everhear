#!/usr/bin/env python3
"""vfg-jev: asks Jev whether each line a player says or types is a direct ask
to the AI, context, or not useful; writes the verdict back to the transcript
history; feeds the lines it keeps to the conversation in batches.

    python3 -u /opt/vfg/jev/jev_filter.py              the service (systemd unit vfg-jev)
    python3 /opt/vfg/jev/jev_filter.py --judge "<text>"  judge one text, write nothing
    python3 /opt/vfg/jev/jev_filter.py --judge "<text>" --raw   ... and print Jev's answer as sent

Configuration is the environment (see `filter.Config`); the key is
`TYPESAFE_API_KEY`, from the environment or else `VFG_ENV_FILE`, never logged.
The journal is stdout.
"""
import argparse
import importlib
import logging
import os
import signal
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from filter import Config, Filter                   # noqa: E402
from jev_api import Jev, JevDown, JevRefused          # noqa: E402
from jev_clock import SystemClock, iso                # noqa: E402
from prompt import Lookout, build_state, questions    # noqa: E402
from store import Store                               # noqa: E402

KEY_NAME = "TYPESAFE_API_KEY"
log = logging.getLogger("vfg-jev")


def read_key(env_file, environ=None):
    """The key from the environment, else from `env_file` (`KEY=value` lines).
    Answers `(key, where)`; `key` is "" when there is none."""
    environ = os.environ if environ is None else environ
    v = (environ.get(KEY_NAME) or "").strip()
    if v:
        return v, "the environment"
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                name, eq, value = line.partition("=")
                if eq and name.strip() == KEY_NAME:
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                        value = value[1:-1]
                    return value.strip(), env_file
    except OSError as e:
        return "", "%s (unreadable: %s)" % (env_file, e.strerror or type(e).__name__)
    return "", env_file


def setup_logging():
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")


def judge_once(cfg, key, text, raw=False):
    """`--judge`: one text as a chat line from `tester` said now, the current
    lookout, no history, nothing asked again. Prints, writes nothing. With
    `raw`, also prints the response body exactly as Jev sent it — the way to
    check, on the first live run, the answer's shape this code assumes."""
    jev = Jev(key, url=cfg.jev_url, model=cfg.model, timeout=cfg.timeout)
    row = {"id": "judge", "at": iso(time.time()), "player_name": "tester", "source": "chat",
           "text": text, "peak_dbfs": None}
    state, _ = build_state(Lookout(cfg.lookout).text(), [row], [], cfg.state_tokens)
    try:
        result = jev.ask(state, questions([row]))
    except (JevDown, JevRefused) as e:
        print("jev %s: %s" % ("refused" if isinstance(e, JevRefused) else "failed", e), file=sys.stderr)
        if raw and jev.last_body is not None:
            print(jev.last_body.decode("utf-8", errors="replace").replace(key, "***"), file=sys.stderr)
        return 1
    if raw:
        print(jev.last_body.decode("utf-8", errors="replace").replace(key, "***"))
    cls, conf = result.answers["line_1"]
    tokens = "?" if result.input_tokens is None else result.input_tokens
    print("%s %.2f · %dms · %stok" % (cls, conf, result.ms, tokens))
    return 0


class Stop:
    def __init__(self):
        self.now = False
        self.filter = None

    def __call__(self, signum, _frame):
        self.now = True
        if self.filter is not None:
            self.filter.stopping = True


def nap(seconds, stop):
    """Sleep, waking within 50 ms of a stop."""
    end = time.monotonic() + seconds
    while not stop.now:
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.05))


def import_feed(lib, stop):
    """`vfg_feed` from `VFG_LIB`, first on the path. Waits for it, saying so
    once, rather than crash-looping out of sight."""
    if lib not in sys.path:
        sys.path.insert(0, lib)
    said = False
    while not stop.now:
        try:
            return importlib.import_module("vfg_feed")
        except ImportError as e:
            if not said:
                log.warning("cannot import vfg_feed from %s (%s); looking again every 2 s", lib, e)
                said = True
        nap(2.0, stop)
    return None


def serve(cfg, key, where):
    stop = Stop()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if key:
        log.info("key from %s", where)
    else:
        log.error("no %s in the environment or %s — every call will fail, and the filter will "
                  "fail open and say so in the feed", KEY_NAME, where)
    feed = import_feed(cfg.lib, stop)
    if feed is None:
        return 0
    store = Store(cfg.db)
    jev = Jev(key, url=cfg.jev_url, model=cfg.model, timeout=cfg.timeout)
    flt = Filter(store, jev, feed, SystemClock(), cfg)
    stop.filter = flt
    flt.stopping = stop.now
    log.info("vfg-jev up · model %s · poll %dms · batch %gs · state %d tokens · history %gh",
             cfg.model, cfg.poll_ms, cfg.batch_seconds, cfg.state_tokens, cfg.history_hours)
    poll = cfg.poll_ms / 1000.0
    while not stop.now:
        began = time.monotonic()
        try:
            pause = flt.step()
        except sqlite3.Error as e:
            log.warning("database: %s — trying the step again", e)
            pause = None
        except Exception:
            log.exception("step failed — trying again in 2 s")
            pause = 2.0
        if pause is None:
            pause = poll - (time.monotonic() - began)   # straight away again if the step ran long
        nap(pause, stop)
    try:
        flt.flush_now()
    except Exception as e:
        log.warning("final flush failed: %s", e)
    store.close()
    log.info("vfg-jev stopped")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="vfg-jev: the Jev filter")
    ap.add_argument("--judge", metavar="TEXT", help="judge one text as a chat line from `tester`; writes nothing")
    ap.add_argument("--raw", action="store_true", help="with --judge: also print Jev's response body as sent")
    args = ap.parse_args(argv)
    setup_logging()
    cfg, problems = Config.from_env()
    for p in problems:
        log.warning(p)
    key, where = read_key(cfg.env_file)
    if args.judge is not None:
        if not key:
            print("no %s in the environment or %s" % (KEY_NAME, where), file=sys.stderr)
            return 1
        return judge_once(cfg, key, args.judge, raw=args.raw)
    return serve(cfg, key, where)


if __name__ == "__main__":
    sys.exit(main())
