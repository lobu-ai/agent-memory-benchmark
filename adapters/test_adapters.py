"""Source attribution and ranking transformations; no model/provider required."""
import unittest
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adapters.mem0_local_adapter import collapse_source_hits
from adapters._supermemory_v5 import Client, ReadinessError, SupermemoryError


class SourceHitTests(unittest.TestCase):
    def test_public_ranking_is_preserved_before_source_limit(self):
        # Current Mem0 uses higher-is-better similarity; the API owns ranking.
        hits = [
            {"id": "a", "memory": "relevant", "score": 0.9},
            {"id": "b", "memory": "unrelated", "score": 0.1},
        ]
        self.assertEqual([h["id"] for h in collapse_source_hits(hits, 1)], ["a"])
        # Rank must also survive hybrid scores or missing scores.
        hits[0].pop("score")
        self.assertEqual([h["id"] for h in collapse_source_hits(hits, 2)], ["a", "b"])

    def test_multiple_facts_keep_their_source_and_evidence(self):
        hits = [
            {"id": "a", "memory": "parent owns plant", "metadata": {"benchmark_id": "source-1"}},
            {"id": "b", "memory": "distractor", "metadata": {"benchmark_id": "source-2"}},
            {"id": "c", "memory": "parent is approved", "metadata": {"benchmark_id": "source-1"}},
            {"id": "d", "memory": "parent is approved", "metadata": {"benchmark_id": "source-1"}},
        ]
        result = collapse_source_hits(hits, 1)
        self.assertEqual(result[0]["id"], "source-1")
        self.assertEqual(result[0]["text"], "parent owns plant\n\nparent is approved")
        self.assertEqual(len(result), 1)

    def test_empty_results_and_nonobject_metadata(self):
        self.assertEqual(collapse_source_hits([], 4), [])
        self.assertEqual(collapse_source_hits([{"id": "fallback", "metadata": None}], 1)[0]["id"], "fallback")


class SupermemoryContractTests(unittest.TestCase):
    """Real HTTP/JSONL boundary, with a deterministic server contract fixture.

    This checks transport and lifecycle handling, not Supermemory extraction
    quality. A live binary conformance run is required separately.
    """
    def setUp(self):
        self.documents = {}
        self.pending = {}
        self.stuck = False
        self.old_build = False
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def handle_request(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
                if self.headers.get("Authorization") != "Bearer synthetic-test-key":
                    self.send(401, {"error": "credential must never be reflected"})
                    return
                doc_id = self.path.rsplit("/", 1)[-1]
                if self.command == "POST" and self.path.endswith("/document"):
                    case.documents[body["id"]] = {**body, "system": {"status": "done", "dreamingStatus": "dreaming"}}
                    self.send(200, {"id": body["id"], "status": "queued"})
                elif self.command == "PATCH":
                    # Deliberately expose an old done version for one read.
                    case.pending[doc_id] = body
                    self.send(200, {"id": doc_id, "status": "queued"})
                elif self.command == "GET":
                    doc = case.documents[doc_id]
                    self.send(200, {**doc, "system": {"status": "done"}} if case.old_build else doc)
                    if not case.stuck:
                        doc.update(case.pending.pop(doc_id, {}))
                        doc["system"] = {"status": "done", "dreamingStatus": "done"}
                elif "/list/documents?" in self.path:
                    if case.old_build and body.get("include", {}).get("content"):
                        self.send(400, {"error": "include.content unsupported"})
                    else:
                        self.send(200, {"documents": [{"id": key} for key in case.documents],
                                        "pagination": {"totalPages": 1}})
                elif self.path.endswith("/search"):
                    # API ranking intentionally differs from similarity order.
                    self.send(200, {"results": [{"id": "m2", "memory": "corrected", "similarity": .1,
                        "included": {"document": case.documents["a"]}},
                        {"id": "m1", "memory": "other", "similarity": .9,
                         "metadata": {"benchmark_id": "other-source"}}]})
                else:
                    self.send(404, {})

            def send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_POST = do_GET = do_PATCH = handle_request

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def test_jsonl_document_ingest_correction_readiness_and_ranked_source_attribution(self):
        payload = {"runId": "synthetic-run", "scenario": {"id": "synthetic-case", "steps": [
            {"id": "a", "content": "old"}, {"id": "b", "content": "corrected", "supersedes": "a"}]}}
        requests = [{"id": 1, "action": "ingest", "payload": payload},
                    {"id": 2, "action": "retrieve", "payload": {**payload, "prompt": "current?", "topK": 2}}]
        process = subprocess.run(["python3", str(Path(__file__).with_name("supermemory_v5_adapter.py"))],
            input="\n".join(json.dumps(r) for r in requests)+"\n", capture_output=True, text=True, timeout=8,
            env={**os.environ, "SUPERMEMORY_BASE_URL": self.base, "SUPERMEMORY_API_KEY": "synthetic-test-key",
                 "SUPERMEMORY_READY_TIMEOUT": "3", "SUPERMEMORY_RERANK": "order"})
        self.assertEqual(process.returncode, 0, process.stderr)
        ingested, retrieved = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertTrue(ingested["ok"], ingested)
        self.assertEqual(len(self.documents), 1)
        self.assertEqual(self.documents["a"]["content"], "corrected")
        self.assertEqual(ingested["result"]["ready"][-1]["system"]["dreamingStatus"], "done")
        self.assertEqual([i["id"] for i in retrieved["result"]["items"]], ["b", "other-source"])

    def test_missing_memory_readiness_fails_instead_of_scoring_empty_search(self):
        self.stuck = True
        api = Client(self.base, "synthetic-test-key", "bench_test", ready_timeout=.03)
        with self.assertRaises(ReadinessError):
            api.write("a", "content", {})

    def test_verified_old_build_can_list_metadata_but_cannot_claim_memory_readiness(self):
        self.old_build = True
        api = Client(self.base, "synthetic-test-key", "bench_test", list_content=False)
        with self.assertRaisesRegex(ReadinessError, "omits system.dreamingStatus"):
            api.write("a", "content", {})
        self.assertEqual(api.list()["documents"], [{"id": "a"}])
        self.assertEqual(api.get("a")["content"], "content")
        api.list_content = True
        with self.assertRaises(SupermemoryError):
            api.list()

    def test_typed_http_error_is_redacted_and_insecure_remote_origin_rejected(self):
        api = Client(self.base, "wrong-synthetic-key", "bench_test")
        with self.assertRaises(SupermemoryError) as error:
            api.search("test")
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(str(error.exception), "Supermemory HTTP 401")
        with self.assertRaises(ValueError):
            Client("http://remote.example", "synthetic-test-key", "bench_test")
