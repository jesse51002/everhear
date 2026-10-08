"""The Jev call, against the local stand-in for /v1/systemone."""
import json
import os
import socket
import unittest

import support  # noqa: F401  (puts the service folder on the path)
import fake_jev
from jev_api import Jev, JevDown, JevRefused, parse_answer
from prompt import CRITERIA, questions
from support import T0
from jev_clock import iso

os.environ.setdefault("no_proxy", "127.0.0.1,localhost")
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

KEY = "test-key-not-a-secret"


def row(n, text, player="Steve", source="voice", at=T0):
    return {"id": "L%d" % n, "at": iso(at), "player_name": player, "source": source, "text": text, "peak_dbfs": None}


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = fake_jev.serve(hang_seconds=1.5)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.server.mode = "ok"
        self.server.calls.clear()
        self.server.canned.clear()
        self.jev = Jev(KEY, url=self.server.url, model="jev-latest", timeout=0.5)

    def canned(self, payload):
        self.server.canned.append(json.dumps(payload).encode())

    def test_request_shape(self):
        qs = questions([row(1, "build me a castle"), row(2, "that cave by spawn", "Alex", "chat")])
        self.jev.ask({"rules": "R", "look_out_for": "L", "history": "H ü"}, qs)
        self.assertEqual(len(self.server.calls), 1)
        call = self.server.calls[0]
        headers = {k.lower(): v for k, v in call["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer " + KEY)
        self.assertEqual(headers["content-type"], "application/json")
        body = call["body"]
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], {"rules": "R", "look_out_for": "L", "history": "H ü"})
        self.assertEqual(list(body["questions"]), ["line_1", "line_2"])
        self.assertEqual(body["questions"]["line_1"]["type"], "choice")
        self.assertEqual(body["questions"]["line_1"]["criteria"], CRITERIA)

    def test_answers_and_tokens(self):
        qs = questions([row(1, "build me a castle"), row(2, "that cave by spawn"), row(3, "lol")])
        res = self.jev.ask({"t": "x" * 391}, qs)     # 400 bytes encoded
        self.assertEqual(res.answers, {"line_1": ("direct_ask", 0.9), "line_2": ("context", 0.7),
                                       "line_3": ("not_useful", 0.8)})
        self.assertEqual(res.input_tokens, 100)
        self.assertAlmostEqual(res.cost_usd, 100 * 0.000000042)
        self.assertGreaterEqual(res.ms, 0)

    def test_confidence_falls_back_to_probabilities(self):
        self.canned({"answers": {"line_1": {"type": "choice", "choice": "context",
                                            "probabilities": {"direct_ask": 0.1, "context": 0.66, "not_useful": 0.24}}},
                     "usage": {"input_tokens": 7}})
        res = self.jev.ask({"t": "s"}, questions([row(1, "hm")]))
        self.assertEqual(res.answers["line_1"], ("context", 0.66))
        self.assertEqual(res.input_tokens, 7)

    def test_missing_usage_is_none_not_zero(self):
        self.canned({"answers": {"line_1": {"choice": "context", "confidence": 0.5}}})
        res = self.jev.ask({"t": "s"}, questions([row(1, "hm")]))
        self.assertIsNone(res.input_tokens)
        self.assertIsNone(res.cost_usd)

    def test_failures_are_down(self):
        qs = questions([row(1, "build")])
        self.server.mode = "fail"
        with self.assertRaises(JevDown):
            self.jev.ask({"t": "s"}, qs)
        self.server.mode = "hang"
        with self.assertRaises(JevDown):
            self.jev.ask({"t": "s"}, qs)

    def test_refused(self):
        self.server.mode = "refuse"
        with self.assertRaises(JevRefused) as e:
            self.jev.ask({"t": "s"}, questions([row(1, "build")]))
        self.assertIn("400", str(e.exception))

    def test_unknown_label_in_line_1_is_down(self):
        self.canned({"answers": {"line_1": {"choice": "maybe", "confidence": 0.9}}, "usage": {"input_tokens": 1}})
        with self.assertRaises(JevDown):
            self.jev.ask({"t": "s"}, questions([row(1, "build")]))

    def test_missing_line_1_or_no_confidence_is_down(self):
        for payload in ({"answers": {}}, {"answers": {"line_1": {"choice": "context"}}}, {"nothing": 1},
                        {"answers": {"line_1": {"choice": "context", "confidence": "high"}}}, []):
            self.canned(payload)
            with self.assertRaises(JevDown, msg=payload):
                self.jev.ask({"t": "s"}, questions([row(1, "build")]))

    def test_malformed_line_3_dropped_rest_kept(self):
        self.canned({"answers": {
            "line_1": {"choice": "direct_ask", "confidence": 0.93},
            "line_2": {"choice": "context", "confidence": 0.71},
            "line_3": {"choice": "banana", "confidence": 0.5},
            "line_4": {"choice": "not_useful", "confidence": 0.6}}, "usage": {"input_tokens": 3}})
        res = self.jev.ask({"t": "s"}, questions([row(n, "t%d" % n) for n in range(1, 5)]))
        self.assertEqual(set(res.answers), {"line_1", "line_2", "line_4"})

    def test_parse_answer(self):
        q = {"type": "choice", "criteria": CRITERIA}
        self.assertEqual(parse_answer(q, {"choice": "context", "confidence": 1}), ("context", 1.0))
        self.assertIsNone(parse_answer(q, {"choice": "context", "confidence": 1.5}))
        self.assertIsNone(parse_answer(q, {"choice": "context", "confidence": True}))
        self.assertIsNone(parse_answer(q, "context"))
        self.assertEqual(parse_answer({"type": "noul"}, {"noul": 0.2}), (None, 0.2))

    def test_statuses(self):
        for status, kind in ((400, JevRefused), (413, JevRefused), (422, JevRefused), (401, JevDown),
                             (403, JevDown), (429, JevDown), (500, JevDown), (503, JevDown)):
            jev = Jev(KEY, url=self._status_url(status), timeout=0.5)
            with self.assertRaises(kind, msg=status):
                jev.ask({"t": "s"}, questions([row(1, "build")]))

    def _status_url(self, status):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.dumps({"detail": [{"msg": "field required", "input": "THE STATE " + KEY}]}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        self.addCleanup(srv.server_close)
        return "http://127.0.0.1:%d/v1/systemone" % srv.server_address[1]

    def test_errors_never_carry_the_key_or_the_state(self):
        jev = Jev(KEY, url=self._status_url(422), timeout=0.5)
        with self.assertRaises(JevRefused) as e:
            jev.ask({"history": "THE STATE"}, questions([row(1, "build")]))
        self.assertNotIn(KEY, str(e.exception))
        self.assertNotIn("THE STATE", str(e.exception))
        self.assertIn("field required", str(e.exception))

    def test_no_connection_is_down(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(JevDown):
            Jev(KEY, url="http://127.0.0.1:%d/v1/systemone" % port, timeout=0.5).ask({"t": "s"}, questions([row(1, "x")]))

    def test_no_key_is_down_without_a_call(self):
        with self.assertRaises(JevDown):
            Jev("", url=self.server.url).ask({"t": "s"}, questions([row(1, "build")]))
        self.assertEqual(self.server.calls, [])

    def test_a_bare_string_state_is_refused_by_the_stand_in(self):
        with self.assertRaises(JevRefused):
            self.jev.ask("a bare string", questions([row(1, "build")]))

    def test_body_is_utf8_json_as_counted(self):
        from prompt import state_json
        state = {"history": "ça va 🙂 \"q\""}
        self.jev.ask(state, questions([row(1, "build")]))
        self.assertEqual(self.server.calls[0]["body"]["state"], state)
        self.assertIn(state_json(state), json.dumps(self.server.calls[0]["body"], ensure_ascii=False))

    def test_probe(self):
        res = self.jev.probe()
        self.assertEqual(res.answers["ping"], (None, 0.9))
        body = self.server.calls[0]["body"]
        self.assertEqual(body["state"], {"text": "ping"})
        self.assertEqual(body["questions"]["ping"]["type"], "noul")
        self.server.mode = "fail"
        with self.assertRaises(JevDown):
            self.jev.probe()


if __name__ == "__main__":
    unittest.main()
