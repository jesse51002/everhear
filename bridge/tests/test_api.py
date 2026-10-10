"""The speech endpoint on port 0: 202, repeats, 401, every 400 rule, the clock guard, 503, health."""

import datetime as _dt
import http.client
import io
import json
import logging
import sqlite3
import threading
import time
import unittest
import uuid

from vfg_bridge import api, db as dbmod, timeutil

from helpers import Box

CROCKFORD = r"^[0-9A-HJKMNP-TV-Z]{26}$"


class ApiCase(Box):
    token = "test"

    def setUp(self):
        super().setUp()
        self.db = self.make_db()
        self.start_server(self.db)

    def start_server(self, database):
        self.server = api.make_server("127.0.0.1", 0, database, self.token)
        thread = threading.Thread(target=self.server.serve_forever, args=(0.002,), daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(5)
        self.addCleanup(stop)
        self.port = self.server.server_address[1]

    def request(self, method, path, body=None, token="test", raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        sent = dict(headers or {})
        if token is not None:
            sent["Authorization"] = "Bearer " + token
        payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        if payload is not None:
            sent.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=sent)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, (json.loads(data) if data else None)

    def body(self, **changes):
        now = timeutil.utcnow()
        doc = {
            "client_line_id": str(uuid.uuid4()),
            "player_uuid": "00000000-0000-4000-8000-000000000001",
            "player_name": "Steve",
            "text": "build me a castle",
            "started_at": timeutil.format_time(now - _dt.timedelta(seconds=3)),
            "ended_at": timeutil.format_time(now - _dt.timedelta(seconds=1)),
            "peak_dbfs": -8.2,
            "mean_dbfs": -23.5,
        }
        for key, value in changes.items():
            if value is KeyError:
                doc.pop(key, None)
            else:
                doc[key] = value
        return doc

    def lines(self):
        return self.query("SELECT id, at, ended_at, player_uuid, player_name, source, text,"
                          " peak_dbfs, mean_dbfs, client_line_id, jev_class, fed_at FROM lines")


class TestSpeech(ApiCase):
    def test_stored_with_a_ulid(self):
        body = self.body(text="  build me a castle \n")
        status, doc = self.request("POST", "/v1/lines", body)
        self.assertEqual(status, 202)
        self.assertRegex(doc["id"], CROCKFORD)
        rows = self.lines()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row[0], doc["id"])
        self.assertEqual(row[1], timeutil.format_time(timeutil.parse_time(body["started_at"])))
        self.assertEqual(row[2], timeutil.format_time(timeutil.parse_time(body["ended_at"])))
        self.assertEqual(row[3:], ("00000000-0000-4000-8000-000000000001", "Steve", "voice",
                                   "build me a castle", -8.2, -23.5, body["client_line_id"],
                                   None, None))

    def test_repeat_answers_the_first_id_and_stores_nothing(self):
        body = self.body()
        first = self.request("POST", "/v1/lines", body)
        second = self.request("POST", "/v1/lines", dict(body, text="different words"))
        self.assertEqual(first[0], 202)
        self.assertEqual(second, first)
        self.assertEqual(len(self.lines()), 1)
        self.assertEqual(self.lines()[0][6], "build me a castle")

    def test_optional_fields(self):
        status, _ = self.request("POST", "/v1/lines", self.body(
            player_uuid=None, peak_dbfs=KeyError, mean_dbfs=None, extra="ignored"))
        self.assertEqual(status, 202)
        status, _ = self.request("POST", "/v1/lines", self.body(player_uuid=KeyError, peak_dbfs=-3))
        self.assertEqual(status, 202)
        rows = sorted(self.lines(), key=lambda r: r[0])
        self.assertEqual([(r[3], r[7], r[8]) for r in rows],
                         [(None, None, None), (None, -3.0, -23.5)])

    def test_empty_or_blank_uuid_is_stored_as_null(self):
        for blank in ("", "   "):
            status, doc = self.request("POST", "/v1/lines", self.body(player_uuid=blank))
            self.assertEqual(status, 202)
            self.assertEqual(self.query("SELECT player_uuid FROM lines WHERE id = ?", (doc["id"],)),
                             [(None,)])

    def test_times_in_other_forms_are_read_as_utc(self):
        now = timeutil.utcnow().replace(microsecond=123456)
        plus2 = _dt.timezone(_dt.timedelta(hours=2))
        started = (now - _dt.timedelta(seconds=2)).astimezone(plus2)
        body = self.body(started_at=started.strftime("%Y-%m-%dT%H:%M:%S.%f") + "789+02:00",
                         ended_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        status, _ = self.request("POST", "/v1/lines", body)
        self.assertEqual(status, 202)
        row = self.lines()[0]
        self.assertEqual(row[1], timeutil.format_time(now - _dt.timedelta(seconds=2)))
        self.assertEqual(row[2], timeutil.format_time(now.replace(microsecond=0)))


class TestAuth(ApiCase):
    def test_no_token_and_wrong_token(self):
        self.assertEqual(self.request("POST", "/v1/lines", self.body(), token=None),
                         (401, {"error": "unauthorized"}))
        self.assertEqual(self.request("POST", "/v1/lines", self.body(), token="wrong")[0], 401)
        self.assertEqual(self.request("POST", "/v1/lines", self.body(), token="test2")[0], 401)
        self.assertEqual(self.request("POST", "/v1/lines", self.body(), token=None,
                                      headers={"Authorization": "test"})[0], 401)
        self.assertEqual(self.lines(), [])

    def test_token_is_checked_before_the_body(self):
        self.assertEqual(self.request("POST", "/v1/lines", {}, token="wrong")[0], 401)
        self.assertEqual(self.request("POST", "/v1/lines", {})[0], 400)
        self.assertEqual(self.request("POST", "/v1/lines", raw=b"not json", token="wrong")[0], 401)

    def test_health_needs_no_token(self):
        self.assertEqual(self.request("GET", "/v1/health", token=None), (200, {"ok": True}))

    def test_other_paths_are_404(self):
        self.assertEqual(self.request("GET", "/v1/lines")[0], 404)
        self.assertEqual(self.request("POST", "/v1/health", {})[0], 404)
        self.assertEqual(self.request("GET", "/", token=None)[0], 404)
        self.assertEqual(self.request("PUT", "/v1/lines", self.body())[0], 404)


class TestBadRequests(ApiCase):
    def bad(self, field, **changes):
        status, doc = self.request("POST", "/v1/lines", self.body(**changes))
        self.assertEqual(status, 400, changes)
        self.assertTrue(doc["error"].startswith(field + ": "), doc)

    def test_each_rule(self):
        self.bad("client_line_id", client_line_id=KeyError)
        self.bad("client_line_id", client_line_id="")
        self.bad("client_line_id", client_line_id="x" * 65)
        self.bad("client_line_id", client_line_id=12)
        self.bad("player_uuid", player_uuid="u" * 65)
        self.bad("player_uuid", player_uuid=7)
        self.bad("player_name", player_name=KeyError)
        self.bad("player_name", player_name="")
        self.bad("player_name", player_name="Steve!")
        self.bad("player_name", player_name="Abcdefghijklmnopq")
        self.bad("player_name", player_name="Steve\n")
        self.bad("text", text=KeyError)
        self.bad("text", text=5)
        self.bad("text", text="   \n ")
        self.bad("text", text="x" * 2001)
        self.bad("started_at", started_at=KeyError)
        self.bad("started_at", started_at="2026-10-07 18:22:29Z")
        self.bad("started_at", started_at="2026-10-07T18:22:29.100")
        self.bad("started_at", started_at="2026-10-07T18:22:29.1234567890Z")
        self.bad("started_at", started_at=1696700000)
        self.bad("ended_at", ended_at="2026-02-30T00:00:00Z")
        now = timeutil.utcnow()
        self.bad("ended_at", started_at=timeutil.format_time(now),
                 ended_at=timeutil.format_time(now - _dt.timedelta(milliseconds=1)))
        self.bad("peak_dbfs", peak_dbfs=True)
        self.bad("peak_dbfs", peak_dbfs="-8")
        self.bad("mean_dbfs", mean_dbfs=[1])
        self.assertEqual(self.lines(), [])

    def test_text_at_the_limits(self):
        self.assertEqual(self.request("POST", "/v1/lines", self.body(text="x" * 2000))[0], 202)
        self.assertEqual(self.request("POST", "/v1/lines", self.body(text=" y "))[0], 202)

    def test_non_finite_numbers(self):
        for literal in (b"NaN", b"Infinity", b"-Infinity", b"1e999", b"1" + b"0" * 400):
            raw = json.dumps(self.body(peak_dbfs=0)).encode().replace(
                b'"peak_dbfs": 0', b'"peak_dbfs": ' + literal)
            status, doc = self.request("POST", "/v1/lines", raw=raw)
            self.assertEqual(status, 400, literal)
            self.assertTrue(doc["error"].startswith("peak_dbfs: "))

    def test_body_rules(self):
        self.assertEqual(self.request("POST", "/v1/lines", raw=b"{nope")[0], 400)
        self.assertEqual(self.request("POST", "/v1/lines", raw=b"[1, 2]")[0], 400)
        self.assertEqual(self.request("POST", "/v1/lines", raw=b"\xff\xfe")[0], 400)
        self.assertEqual(self.request("POST", "/v1/lines", raw=b"")[0], 400)
        big = self.body(text="x" * 100, pad="p" * (16 * 1024))
        status, doc = self.request("POST", "/v1/lines", big)
        self.assertEqual(status, 400)
        self.assertIn("16 KiB", doc["error"])
        self.assertEqual(self.lines(), [])

    def test_missing_content_length_is_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.putrequest("POST", "/v1/lines")
        conn.putheader("Authorization", "Bearer test")
        conn.endheaders()
        resp = conn.getresponse()
        self.assertEqual(resp.status, 400)
        conn.close()


class TestClockGuard(ApiCase):
    def test_a_day_old_line_is_moved_to_now(self):
        now = timeutil.utcnow()
        started = now - _dt.timedelta(days=1, seconds=2)
        ended = now - _dt.timedelta(days=1)
        status, _ = self.request("POST", "/v1/lines", self.body(
            started_at=timeutil.format_time(started), ended_at=timeutil.format_time(ended)))
        self.assertEqual(status, 202)
        row = self.lines()[0]
        stored_end = timeutil.parse_time(row[2])
        stored_start = timeutil.parse_time(row[1])
        self.assertLess(abs((stored_end - timeutil.utcnow()).total_seconds()), 5)
        self.assertAlmostEqual((stored_end - stored_start).total_seconds(), 2, delta=0.002)

    def test_an_hour_ahead_is_moved_to_now(self):
        now = timeutil.utcnow()
        status, _ = self.request("POST", "/v1/lines", self.body(
            started_at=timeutil.format_time(now + _dt.timedelta(hours=1)),
            ended_at=timeutil.format_time(now + _dt.timedelta(hours=1, seconds=1))))
        self.assertEqual(status, 202)
        stored_end = timeutil.parse_time(self.lines()[0][2])
        self.assertLess(abs((stored_end - timeutil.utcnow()).total_seconds()), 5)

    def test_within_the_window_is_kept(self):
        now = timeutil.utcnow()
        body = self.body(started_at=timeutil.format_time(now - _dt.timedelta(minutes=9, seconds=2)),
                         ended_at=timeutil.format_time(now - _dt.timedelta(minutes=9)))
        self.assertEqual(self.request("POST", "/v1/lines", body)[0], 202)
        self.assertEqual(self.lines()[0][2], body["ended_at"])


class TestBusy(ApiCase):
    def test_503_while_another_connection_holds_the_database(self):
        self.server.shutdown()
        self.server.server_close()
        quick = dbmod.DB(self.db_path, busy_timeout_ms=20)
        self.addCleanup(quick.close)
        self.start_server(quick)
        holder = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        try:
            self.assertEqual(self.request("POST", "/v1/lines", self.body()),
                             (503, {"error": "busy"}))
        finally:
            holder.execute("ROLLBACK")
        self.assertEqual(self.request("POST", "/v1/lines", self.body())[0], 202)


class TestLogging(ApiCase):
    token = "s3cr3t-token-for-the-log-test"

    def test_one_line_per_request_without_text_or_token(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("vfg_bridge")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            status, doc = self.request("POST", "/v1/lines",
                                       self.body(text="a very private sentence"), token=self.token)
            self.request("POST", "/v1/lines", self.body(text="another private one"), token="nope")
            deadline = time.monotonic() + 2  # the server logs after the client has its answer
            while len(stream.getvalue().strip().splitlines()) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        out = stream.getvalue()
        self.assertEqual(status, 202)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2, out)
        self.assertRegex(lines[0], r"^POST /v1/lines 202 %s \d+ms$" % doc["id"])
        self.assertRegex(lines[1], r"^POST /v1/lines 401 - \d+ms$")
        self.assertNotIn("private", out)
        self.assertNotIn(self.token, out)
        self.assertNotIn("127.0.0.1", out)


if __name__ == "__main__":
    unittest.main()
