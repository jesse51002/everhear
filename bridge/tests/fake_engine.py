"""The game engine as the bridge's tests see it: its incantation export, written as the engine
writes it, and its control socket, answering each request with ``answer(request)``. CastCase: a
bridge on port 0 with casts against them, a FakeClock, and the requests the mod sends."""

import datetime as _dt
import http.client
import json
import os
import socketserver
import threading
import time
import uuid

import vfg_feed

from vfg_bridge import api, casts as castsmod, engine as enginemod, timeutil

from helpers import Box, FakeClock


def binding(player, phrase, ability="zap", **more):
    """An entry of the engine's export: `player`'s `phrase` bound to `ability`."""
    e = {"id": "%s.%s" % (player.lower(), "_".join(phrase.lower().split())), "player": player, "phrase": phrase,
         "aliases": [], "everyday_aliases": [], "ability": ability, "cooldown_s": 3.0}
    e.update(more)
    return e


class FakeEngine:
    """The engine's control socket: one JSON line in, ``answer(request)``'s reply out. ``answer``
    may return None to close without a reply, or sleep first. Every request is kept."""

    def __init__(self, path):
        self.path = path
        self.requests = []
        self.answer = lambda req: {"ok": True, "status": "fired"}
        fake = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                req = json.loads(self.rfile.readline())
                fake.requests.append(req)
                reply = fake.answer(req)
                if reply is not None:
                    try:
                        self.wfile.write((json.dumps(reply) + "\n").encode())
                    except OSError:  # the bridge gave up waiting: as the engine, carry on
                        pass

        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.server = socketserver.ThreadingUnixStreamServer(path, Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def casts(self):
        return [r for r in self.requests if r["op"] == "cast"]


class CastCase(Box):
    """The engine binds Steve's "fire ball" and "ice wall" and Alex's "rock slide" (ability zap)."""

    def setUp(self):
        super().setUp()
        self.engine_dir = os.path.join(self.tmp, "vfg", "engine")
        self.export_path = os.path.join(self.engine_dir, "incantations.json")
        self.fake = FakeEngine(os.path.join(self.engine_dir, "engine.sock"))
        self.addCleanup(self.fake.close)
        self.db = self.make_db()
        self.clock = FakeClock(timeutil.utcnow())
        self.casts = castsmod.Casts(self.db, vfg_feed, enginemod.Engine(self.fake.path, self.export_path),
                                    clock=self.clock, engine_wait_s=0.5)
        self.casts.LIST_POLL_S = 0.01
        self.write_export(binding("Steve", "fire ball"), binding("Steve", "ice wall"), binding("Alex", "rock slide"))
        self.server = api.make_server("127.0.0.1", 0, self.db, "test", self.clock, self.casts)
        thread = threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)
        self.addCleanup(stop)
        self.port = self.server.server_address[1]

    def write_export(self, *entries, version=1):
        os.makedirs(self.engine_dir, exist_ok=True)
        tmp = self.export_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"version": version, "incantations": list(entries)}, f)
        os.replace(tmp, self.export_path)

    @staticmethod
    def eventually(check):
        """Whether `check()` holds within 5 s: for the record written after the answer."""
        deadline = time.monotonic() + 5
        while not check() and time.monotonic() < deadline:
            time.sleep(0.01)
        return bool(check())

    def events(self, n):
        """Whether n events rows are written, which comes after the answer."""
        return self.eventually(lambda: self.rows("SELECT count(*) FROM events")[0][0] >= n)

    def fed(self, n):
        return self.eventually(lambda: len(self.feed_lines()) >= n)

    def request(self, method, path, body=None, token="test"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Authorization": "Bearer " + token} if token else {}
        payload = None if body is None else json.dumps(body).encode()
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if data else None)

    def window(self, start=-2.0, length=0.6):
        """A window `start` s from the clock's now, as the mod's audio clock gives it."""
        began = self.clock.now + _dt.timedelta(seconds=start)
        return (timeutil.format_time(began),
                timeutil.format_time(began + _dt.timedelta(seconds=length)))

    def cast(self, phrase="fire ball", player="Steve", start=-2.0, length=0.6, changes=None):
        started, ended = self.window(start, length)
        body = {"client_cast_id": str(uuid.uuid4()), "player_uuid": None, "player_name": player,
                "phrase": phrase, "heard": phrase.replace(" ", ""), "started_at": started,
                "ended_at": ended}
        body.update(changes or {})
        return self.request("POST", "/v1/casts", body)

    def line(self, text, player="Steve", start=-2.5, length=1.5):
        started, ended = self.window(start, length)
        body = {"client_line_id": str(uuid.uuid4()), "player_uuid": None, "player_name": player,
                "text": text, "started_at": started, "ended_at": ended, "peak_dbfs": -8.0,
                "mean_dbfs": -20.0}
        return self.request("POST", "/v1/lines", body)

    def rows(self, sql, args=()):
        return self.query(sql, args)

    def cast_rows(self):
        return self.rows("SELECT layer, incantation_id, action, copy, status, error FROM casts ORDER BY at, id")
