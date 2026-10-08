"""Shared test plumbing: every VFG_* path in a temporary folder, VFG_CLIENT_TOKEN=test."""

import datetime as _dt
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import vfg_feed
import vfg_bridge

UTC = _dt.timezone.utc
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
FEED_PY = os.path.abspath(vfg_feed.__file__)
LIB_DIR = os.path.dirname(FEED_PY)
BRIDGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(vfg_bridge.__file__)))

logging.getLogger("vfg_bridge").addHandler(logging.NullHandler())
logging.getLogger("vfg_bridge").propagate = False


def subprocess_env(extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([LIB_DIR, BRIDGE_DIR])
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    if extra:
        env.update(extra)
    return env


def run(args, env=None, timeout=60, **kw):
    return subprocess.run([sys.executable] + list(args), env=env or subprocess_env(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=timeout, **kw)


def at(text):
    return vfg_feed.parse_time(text)


class FakeClock:
    def __init__(self, now):
        self.now = now if isinstance(now, _dt.datetime) else at(now)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += _dt.timedelta(seconds=seconds)


class Box(unittest.TestCase):
    """A temporary /opt/vfg: VFG_DB, VFG_FEED, VFG_WATCH, VFG_STATE and VFG_CONSOLE_LOG point into it."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="vfg-bridge-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = dict(os.environ)

        def restore():
            os.environ.clear()
            os.environ.update(saved)
        self.addCleanup(restore)
        self.db_path = os.path.join(self.tmp, "vfg.db")
        self.feed_path = os.path.join(self.tmp, "feed.log")
        self.watch_path = os.path.join(self.tmp, "watch.json")
        self.state_dir = os.path.join(self.tmp, "state")
        self.console_path = os.path.join(self.tmp, "console.log")
        os.environ.update({
            "VFG_DB": self.db_path,
            "VFG_FEED": self.feed_path,
            "VFG_WATCH": self.watch_path,
            "VFG_STATE": self.state_dir,
            "VFG_CONSOLE_LOG": self.console_path,
            "VFG_CLIENT_TOKEN": "test",
            "VFG_BRIDGE_BIND": "127.0.0.1",
            "VFG_BRIDGE_PORT": "0",
        })

    # -- the database --------------------------------------------------------

    def make_db(self):
        from vfg_bridge.db import DB
        db = DB(self.db_path)
        db.init()
        self.addCleanup(db.close)
        return db

    def query(self, sql, args=()):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    # -- the feed ------------------------------------------------------------

    def feed_lines(self):
        try:
            with open(self.feed_path, encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def feed_rows(self):
        return self.query("SELECT seq, at, line FROM feed ORDER BY seq")

    # -- the console ---------------------------------------------------------

    def console(self, *lines):
        with open(self.console_path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def write_watch(self, text):
        tmp = self.watch_path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self.watch_path)
