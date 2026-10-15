"""The Jev call, against the local stand-in for /v1/systemone."""
import json
import os
import socket
import unittest

import support  # noqa: F401  (puts the service folder on the path)
import fake_jev
from jev_api import Jev, JevDown, JevRefused, parse_answer
from prompt import ROUTING, gather_questions, questions
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
        cls.server = fake_jev.serve(hang_seconds=0.2)

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
        qs = questions(row(1, "build me a castle"))
        self.jev.ask({"rules": "R", "look_out_for": "L", "history": "H ü"}, qs, required="to_the_ai")
        self.assertEqual(len(self.server.calls), 1)
        call = self.server.calls[0]
        headers = {k.lower(): v for k, v in call["headers"].items()}
        self.assertEqual(headers["authorization"], "Bearer " + KEY)
        self.assertEqual(headers["content-type"], "application/json")
        body = call["body"]
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], {"rules": "R", "look_out_for": "L", "history": "H ü"})
        self.assertEqual(list(body["questions"]), ["to_the_ai", "keep"])
        self.assertEqual(body["questions"]["to_the_ai"]["type"], "choice")
        self.assertEqual(body["questions"]["to_the_ai"]["criteria"], ROUTING["to_the_ai"][1])

    def test_answers_and_tokens(self):
        res = self.jev.ask({"t": "x" * 391}, questions(row(1, "build me a castle")), required="to_the_ai")   # 400 bytes
        self.assertEqual(res.answers, {"to_the_ai": ("says_its_name", 0.9),
                                       "keep": ("ignore_it", 0.6)})
        self.assertEqual(res.input_tokens, 100)
        self.assertAlmostEqual(res.cost_usd, 100 * 0.000000042)
        self.assertGreaterEqual(res.ms, 0)
        qs = gather_questions(row(9, "make it bigger"), [row(1, "the castle"), row(2, "the big one")])
        res = self.jev.ask({"t": "s"}, qs, required=None)
        self.assertEqual(res.answers, {"line_1": ("not_part_of_it", 0.8), "line_2": ("part_of_the_ask", 0.9)})

    def test_confidence_falls_back_to_probabilities(self):
        self.canned({"answers": {"to_the_ai": {"type": "choice", "choice": "neither",
                                            "probabilities": {"says_its_name": 0.1, "neither": 0.66, "answers_its_question": 0.24}}},
                     "usage": {"input_tokens": 7}})
        res = self.jev.ask({"t": "s"}, questions(row(1, "hm")), required="to_the_ai")
        self.assertEqual(res.answers["to_the_ai"], ("neither", 0.66))
        self.assertEqual(res.input_tokens, 7)

    def test_probabilities_of_the_questions_own_labels(self):
        self.canned({"answers": {"to_the_ai": {"type": "choice", "choice": "neither", "confidence": 0.4,
                                            "probabilities": {"says_its_name": 0.1, "neither": 0.66,
                                                              "answers_its_question": "x", "made_up": 0.2}},
                                 "keep": {"type": "choice", "choice": "remember_it", "confidence": 0.5}},
                     "usage": {"input_tokens": 7}})
        res = self.jev.ask({"t": "s"}, questions(row(1, "hm")), required="to_the_ai")
        self.assertEqual(res.answers["to_the_ai"], ("neither", 0.4))
        self.assertEqual(res.probabilities, {"to_the_ai": {"says_its_name": 0.1, "neither": 0.66}})

    def test_missing_usage_is_none_not_zero(self):
        self.canned({"answers": {"to_the_ai": {"choice": "neither", "confidence": 0.5}}})
        res = self.jev.ask({"t": "s"}, questions(row(1, "hm")), required="to_the_ai")
        self.assertIsNone(res.input_tokens)
        self.assertIsNone(res.cost_usd)

    def test_failures_are_down(self):
        qs = questions(row(1, "build"))
        self.server.mode = "fail"
        with self.assertRaises(JevDown):
            self.jev.ask({"t": "s"}, qs, required="to_the_ai")
        self.server.mode = "hang"
        with self.assertRaises(JevDown):
            Jev(KEY, url=self.server.url, timeout=0.02).ask({"t": "s"}, qs, required="to_the_ai")

    def test_refused(self):
        self.server.mode = "refuse"
        with self.assertRaises(JevRefused) as e:
            self.jev.ask({"t": "s"}, questions(row(1, "build")), required="to_the_ai")
        self.assertIn("400", str(e.exception))

    def test_unknown_label_in_the_required_answer_is_down(self):
        self.canned({"answers": {"to_the_ai": {"choice": "maybe", "confidence": 0.9}}, "usage": {"input_tokens": 1}})
        with self.assertRaises(JevDown):
            self.jev.ask({"t": "s"}, questions(row(1, "build")), required="to_the_ai")

    def test_missing_required_answer_or_no_confidence_is_down(self):
        for payload in ({"answers": {}}, {"answers": {"to_the_ai": {"choice": "neither"}}}, {"nothing": 1},
                        {"answers": {"to_the_ai": {"choice": "neither", "confidence": "high"}}},
                        {"answers": {"keep": {"choice": "remember_it", "confidence": 0.9}}}, []):
            self.canned(payload)
            with self.assertRaises(JevDown, msg=payload):
                self.jev.ask({"t": "s"}, questions(row(1, "build")), required="to_the_ai")

    def test_a_malformed_answer_dropped_the_rest_kept(self):
        self.canned({"answers": {
            "to_the_ai": {"choice": "says_its_name", "confidence": 0.93},
            "keep": {"choice": "banana", "confidence": 0.5}}, "usage": {"input_tokens": 3}})
        res = self.jev.ask({"t": "s"}, questions(row(1, "make it bigger")), required="to_the_ai")
        self.assertEqual(res.answers, {"to_the_ai": ("says_its_name", 0.93)})

    def test_parse_answer(self):
        q = {"type": "choice", "criteria": ROUTING["to_the_ai"][1]}
        self.assertEqual(parse_answer(q, {"choice": "neither", "confidence": 1}), ("neither", 1.0))
        self.assertIsNone(parse_answer(q, {"choice": "neither", "confidence": 1.5}))
        self.assertIsNone(parse_answer(q, {"choice": "neither", "confidence": True}))
        self.assertIsNone(parse_answer(q, "neither"))
        self.assertEqual(parse_answer({"type": "noul"}, {"noul": 0.2}), (None, 0.2))

    def test_statuses(self):
        for status, kind in ((400, JevRefused), (413, JevRefused), (422, JevRefused), (401, JevDown),
                             (403, JevDown), (429, JevDown), (500, JevDown), (503, JevDown)):
            jev = Jev(KEY, url=self._status_url(status), timeout=0.5)
            with self.assertRaises(kind, msg=status):
                jev.ask({"t": "s"}, questions(row(1, "build")), required="to_the_ai")

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
            jev.ask({"history": "THE STATE"}, questions(row(1, "build")), required="to_the_ai")
        self.assertNotIn(KEY, str(e.exception))
        self.assertNotIn("THE STATE", str(e.exception))
        self.assertIn("field required", str(e.exception))

    def test_no_connection_is_down(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(JevDown):
            Jev(KEY, url="http://127.0.0.1:%d/v1/systemone" % port, timeout=0.5).ask({"t": "s"}, questions(row(1, "x")), required="to_the_ai")

    def test_no_key_is_down_without_a_call(self):
        with self.assertRaises(JevDown):
            Jev("", url=self.server.url).ask({"t": "s"}, questions(row(1, "build")), required="to_the_ai")
        self.assertEqual(self.server.calls, [])

    def test_a_bare_string_state_is_refused_by_the_stand_in(self):
        with self.assertRaises(JevRefused):
            self.jev.ask("a bare string", questions(row(1, "build")), required="to_the_ai")

    def test_body_is_utf8_json_as_counted(self):
        from prompt import state_json
        state = {"history": "ça va 🙂 \"q\""}
        self.jev.ask(state, questions(row(1, "build")), required="to_the_ai")
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
