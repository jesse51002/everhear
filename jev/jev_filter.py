#!/usr/bin/env python3
"""vfg-jev: asks Jev what should become of each line a player says or types —
it is said to the AI (to_ai: by name, a misheard name included, or answering a
question the AI asked), it is kept as context, or it is not relevant — and, for
a line to the AI, which of the speaker's earlier lines go with it; writes the verdict back to the
transcript history; feeds the lines it keeps to the brain in batches.

    python3 -u /opt/vfg/jev/jev_filter.py              the service (systemd unit vfg-jev)
    python3 /opt/vfg/jev/jev_filter.py --judge "<text>"  judge one text, write nothing
    python3 /opt/vfg/jev/jev_filter.py --judge "<text>" --raw   ... and print Jev's answer as sent
    python3 /opt/vfg/jev/jev_filter.py --cast "<text>" --player NAME   the cast check alone; casts nothing

Configuration is the environment (see `filter.Config`); the key is
`TYPESAFE_API_KEY`, from the environment or else `VFG_ENV_FILE`, never logged;
the cast check reaches the bridge on 127.0.0.1 with `VFG_CLIENT_TOKEN`, from the
same places. The journal is stdout.
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

import casting                                       # noqa: E402
from filter import Config, Filter                   # noqa: E402
from jev_api import Jev, JevDown, JevRefused          # noqa: E402
from jev_clock import SystemClock, iso                # noqa: E402
from prompt import QUESTION, Lookout, Menu, build_state, in_short, questions, route   # noqa: E402
from store import Store                               # noqa: E402

KEY_NAME = "TYPESAFE_API_KEY"
TOKEN_NAME = "VFG_CLIENT_TOKEN"   # the bridge's, for its cast path
log = logging.getLogger("vfg-jev")


def read_key(env_file, environ=None, name=KEY_NAME):
    """The key (or another secret, `name`) from the environment, else from `env_file`
    (`KEY=value` lines). Answers `(key, where)`; `key` is "" when there is none."""
    environ = os.environ if environ is None else environ
    v = (environ.get(name) or "").strip()
    if v:
        return v, "the environment"
    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, eq, value = line.partition("=")
                if eq and key.strip() == name:
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
    lookout and menu, no history, no gather call. Prints the class with both
    answers — `to_ai 0.98 · by name 0.98, ignore 0.22 · 230ms · 7012tok`
    — and writes nothing. With `raw`, also prints the response body exactly as
    Jev sent it — the way to check, on the first live run, the answer's shape
    this code assumes."""
    jev = Jev(key, url=cfg.jev_url, model=cfg.model, timeout=cfg.timeout)
    row = {"id": "judge", "at": iso(time.time()), "player_name": "tester", "source": "chat",
           "text": text, "peak_dbfs": None}
    state, _ = build_state(Lookout(cfg.lookout).text(), [(1, row)], [], cfg.state_tokens,
                           menu=Menu(cfg.menu).text())
    try:
        result = jev.ask(state, questions(row), required=QUESTION)
    except (JevDown, JevRefused) as e:
        print("jev %s: %s" % ("refused" if isinstance(e, JevRefused) else "failed", e), file=sys.stderr)
        if raw and jev.last_body is not None:
            print(jev.last_body.decode("utf-8", errors="replace").replace(key, "***"), file=sys.stderr)
        return 1
    if raw:
        print(jev.last_body.decode("utf-8", errors="replace").replace(key, "***"))
    cls, conf = route(result.answers)
    tokens = "?" if result.input_tokens is None else result.input_tokens
    print("%s %.2f · %s · %dms · %stok" % (cls, conf, in_short(result.answers), result.ms, tokens))
    return 0


def cast_once(cfg, key, text, player, raw=False):
    """`--cast`: the cast check on one text as a voice line `player` says now, with the
    current store and menu and no recent lines. Prints the answer —
    `hot fireball p 0.92 → a cast · 210ms · 900tok`, or `no p 0.88 …`, or `… has no incantations` —
    and writes nothing, casts nothing."""
    entries = casting.Incantations(cfg.incantations).for_player(player)
    if not entries:
        print("%s has no incantations in %s: no call" % (player, cfg.incantations))
        return 0
    jev = Jev(key, url=cfg.jev_url, model=cfg.model, timeout=cfg.timeout)
    row = {"id": "cast", "at": iso(time.time()), "player_name": player, "source": "voice", "text": text,
           "peak_dbfs": None}
    asked, picked = casting.questions(row, entries, Menu(cfg.menu).text())
    try:
        result = jev.ask(casting.state(row, []), asked, required=casting.QUESTION)
    except (JevDown, JevRefused) as e:
        print("jev %s: %s" % ("refused" if isinstance(e, JevRefused) else "failed", e), file=sys.stderr)
        if raw and jev.last_body is not None:
            print(jev.last_body.decode("utf-8", errors="replace").replace(key, "***"), file=sys.stderr)
        return 1
    if raw:
        print(jev.last_body.decode("utf-8", errors="replace").replace(key, "***"))
    entry, name, p = casting.answer(result, picked)
    tokens = "?" if result.input_tokens is None else result.input_tokens
    verdict = ("" if entry is None else " → no probability" if p is None
               else " → a cast" if p >= cfg.cast_min else " → under %.2f" % cfg.cast_min)
    print("%s p %s%s · %dms · %stok" % (casting.short(name), "-" if p is None else "%.2f" % p, verdict,
                                          result.ms, tokens))
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


def make_bridge(cfg):
    """The bridge's cast path for the cast check, or None without the token, saying so. Whether
    the check is on is the Filter's to say (VFG_JEV_CAST_MIN over 1 turns it off)."""
    token, where = read_key(cfg.env_file, name=TOKEN_NAME)
    if not token:
        log.error("no %s in the environment or %s — the cast check is off, and incantations Jev "
                  "would have caught go to the routing call", TOKEN_NAME, where)
        return None
    return casting.Bridge("http://127.0.0.1:%d" % cfg.bridge_port, token)


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
    flt = Filter(store, jev, feed, SystemClock(), cfg, bridge=make_bridge(cfg))
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
    ap.add_argument("--cast", metavar="TEXT", help="the cast check alone on one text as a voice line of "
                                                   "--player; writes nothing, casts nothing")
    ap.add_argument("--player", metavar="NAME", help="with --cast: whose line, and so whose incantations")
    ap.add_argument("--raw", action="store_true", help="with --judge or --cast: also print Jev's response body")
    args = ap.parse_args(argv)
    if args.cast is not None and not args.player:
        ap.error("--cast needs --player")
    setup_logging()
    cfg, problems = Config.from_env()
    for p in problems:
        log.warning(p)
    key, where = read_key(cfg.env_file)
    if args.judge is not None or args.cast is not None:
        if not key:
            print("no %s in the environment or %s" % (KEY_NAME, where), file=sys.stderr)
            return 1
        if args.cast is not None:
            return cast_once(cfg, key, args.cast, args.player, raw=args.raw)
        return judge_once(cfg, key, args.judge, raw=args.raw)
    return serve(cfg, key, where)


if __name__ == "__main__":
    sys.exit(main())
