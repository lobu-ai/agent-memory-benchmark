"""Boundary tests for benchmark scoring and loopback HTTP handling.

Native ontology behavior is exercised by ontology-benchmark.py against real
Postgres, not simulated here. These tests guard the measurement trust boundary.
"""
import importlib.util
import json
import pathlib
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

path = pathlib.Path(__file__).with_name("ontology-benchmark.py")
spec = importlib.util.spec_from_file_location("ontology_benchmark", path)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class BoundaryTests(unittest.TestCase):
    def test_loopback_scope_rejects_cloud_urls_and_embedded_credentials(self):
        for value in ["https://app.lobu.ai", "http://example.com", "http://localhost.evil.test",
                      "http://user:secret@localhost", "http://localhost/?redirect=cloud", "http://localhost/api"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                bench.loopback_url(value)
        self.assertEqual(bench.loopback_url("http://127.0.0.1:1234/"), "http://127.0.0.1:1234")

    def test_missing_and_blocked_cases_cannot_inflate_the_score(self):
        one = [{"status": "passed"}]
        self.assertEqual(bench.summary(one)["score"], 1 / len(bench.CASES))
        self.assertEqual(bench.summary([{"status": "blocked"}] * len(bench.CASES))["score"], 0)
        suite = bench.Suite(None)
        suite.check("automation-save", lambda: self.fail("blocked dependency was executed"), requires=("automation-schema",))
        self.assertEqual(suite.rows[0]["status"], "blocked")

    def test_ref_scope_excludes_unused_definitions_and_handles_cycles(self):
        root = {"$defs": {"live": {"properties": {"decision": {"enum": ["hold", "release"]}}, "$ref": "#/$defs/live"},
                           "unused": {"enum": ["secret-gold-label"]}}}
        schemas = list(bench.reachable_schemas(root, {"$ref": "#/$defs/live"}))
        self.assertTrue(any(s.get("properties", {}).get("decision", {}).get("enum") == ["hold", "release"] for s in schemas))
        self.assertFalse(any(s.get("enum") == ["secret-gold-label"] for s in schemas))

    def test_public_http_rejections_do_not_count_auth_or_outage_as_validation(self):
        class Handler(BaseHTTPRequestHandler):
            status = 400
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(self.status)
                if self.status == 302:
                    self.send_header("Location", "/must-not-follow")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "pending_approval"}).encode())

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            client = bench.Client.__new__(bench.Client)
            client.base = "http://127.0.0.1:" + str(server.server_port)
            client.org, client.token, client.calls = "synthetic", "synthetic-token", 0
            try:
                for status in [400, 422]:
                    Handler.status = status
                    self.assertEqual(client.reject_write("entities.create", {})["httpStatus"], status)
                for status in [401, 403, 404, 409, 500, 302]:
                    Handler.status = status
                    with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError):
                        client.reject_write("entities.create", {})
                Handler.status = 200
                with self.assertRaises(bench.ApiError) as captured:
                    client.reject_write("entities.create", {})
                self.assertEqual(captured.exception.error["code"], "PENDING_APPROVAL")
            finally:
                server.shutdown()
                worker.join()


if __name__ == "__main__":
    unittest.main()
