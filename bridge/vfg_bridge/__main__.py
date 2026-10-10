"""python3 -m vfg_bridge [--init-db | --parse FILE]

With no option: apply the schema, load watch.json, tail the console and serve
the speech endpoint until SIGTERM.  ``--init-db`` applies the schema and exits;
``--parse FILE`` prints each recognised line of a console log (plain or .gz)
as one JSON object and touches nothing.
"""

import argparse
import logging
import os
import signal
import sys
import threading

EX_CONFIG = 78  # systemd does not restart on it (RestartPreventExitStatus=78)
HTTP_POLL_SECONDS = 0.05  # how soon the server sees a stop

log = logging.getLogger("vfg_bridge")


def _fatal(where):
    log.exception("%s: an exception escaped; exiting so systemd restarts the whole bridge", where)
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)


def _guarded(where, fn):
    def run():
        try:
            fn()
        except BaseException:
            _fatal(where)
    return run


def init_db(cfg):
    from .db import DB
    db = DB(cfg.db)
    try:
        db.init()
    finally:
        db.close()


def serve(cfg):
    import vfg_feed

    from .api import make_server
    from .console import Tail
    from .db import DB
    from .watch import WatchFile

    db = DB(cfg.db)
    db.init()
    watch = WatchFile(cfg.watch, vfg_feed)
    watch.ensure()
    watch.check()
    tail = Tail(cfg.console_log, cfg.state, db, watch, vfg_feed)
    httpd = make_server(cfg.bind, cfg.port, db, cfg.client_token)

    stop = threading.Event()

    def on_signal(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    tail_thread = threading.Thread(target=_guarded("console tail", lambda: tail.run(stop)),
                                   name="tail", daemon=True)
    http_thread = threading.Thread(target=_guarded("http", lambda: httpd.serve_forever(HTTP_POLL_SECONDS)),
                                   name="http", daemon=True)
    tail_thread.start()
    http_thread.start()
    host, port = httpd.server_address[:2]
    log.info("vfg-bridge serving on %s:%d, db %s, console %s", host, port, cfg.db,
             cfg.console_log)

    while not stop.wait(0.5):
        pass
    log.info("stopping")
    httpd.shutdown()
    httpd.server_close()
    tail_thread.join(10)  # the tail saves its offset on the way out
    db.close()
    log.info("stopped")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m vfg_bridge", description=__doc__.split("\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--init-db", action="store_true", help="apply the schema and exit")
    group.add_argument("--parse", metavar="FILE",
                       help="print each recognised console line of FILE as JSON and exit")
    args = parser.parse_args(argv)

    if args.parse:
        from .console import parse_file
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        try:
            parse_file(args.parse, sys.stdout)
        except OSError as exc:
            sys.stderr.write("vfg-bridge: %s\n" % exc)
            return 1
        return 0

    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s %(message)s")
    from .config import load
    cfg = load()

    if args.init_db:
        init_db(cfg)
        log.info("schema applied to %s", cfg.db)
        return 0

    if not cfg.client_token:
        sys.stderr.write("vfg-bridge: VFG_CLIENT_TOKEN is empty; set it in /opt/vfg/.env\n")
        return EX_CONFIG
    return serve(cfg)


if __name__ == "__main__":
    sys.exit(main())
