"""Boundary and independent proof checks for the experimental entrypoints."""
import copy
import json
from pathlib import Path
import sys
import threading
import tempfile
import hashlib
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adapters import loopback, post, SqlError
from grade import grade_answer, observed_facts, coverage_receipts
from reference import (worlds, path_oracle, relational_oracle, make_frontier_certificate,
                       verify_exact_set, sufficient_positive_proof)
from run import parse_reply, episode, BudgetExhausted
from generate import generate, verify
from generate_holdout import generate_holdout
from report import load_retrieval
from supermemory import with_source_records, source_content
from muse_bridge import make_server, model_message
import urllib.request


class GraphAgentTests(unittest.TestCase):
    def test_coverage_requires_public_enumeration_and_all_source_reads(self):
        listing = {"tool": "list_documents", "result": {"documents": [{"id": "one"}, {"id": "two"}],
                   "pagination": {"currentPage": 1, "totalPages": 1, "totalItems": 2}}}
        reads = [{"tool": "get_document", "result": {"id": key, "source_records": {"assertions": []}}}
                 for key in ["one", "two"]]
        self.assertTrue(coverage_receipts("supermemory", [listing]+reads, "invoice", {}))
        self.assertFalse(coverage_receipts("supermemory", reads, "invoice", {}))
        self.assertFalse(coverage_receipts("supermemory", [listing]+reads[:1], "invoice", {}))
        incomplete = copy.deepcopy(listing)
        incomplete["result"]["pagination"]["totalPages"] = 2
        self.assertFalse(coverage_receipts("supermemory", [incomplete]+reads, "invoice", {}))

    def test_coverage_requires_node_metadata_and_terminal_link_pages(self):
        nodes = {"tool": "list_nodes", "result": {"nodes": [
            {"entity_id": 1, "node_ref": "invoice"}, {"entity_id": 2, "node_ref": "issuer"}]}}
        links = [{"tool": "list_links", "arguments": {"entity_id": entity},
                  "result": {"links": [], "metadata": {"has_more": False}}} for entity in [1, 2]]
        cert = {"frontier": {"issuer": {}}}
        self.assertTrue(coverage_receipts("lobu", [nodes]+links, "invoice", cert))
        self.assertFalse(coverage_receipts("lobu", [nodes]+links[:1], "invoice", cert))
        self.assertFalse(coverage_receipts("lobu", links, "invoice", cert))
        partial = copy.deepcopy(links)
        partial[-1]["arguments"]["offset"] = 100
        self.assertFalse(coverage_receipts("lobu", [nodes]+partial, "invoice", cert))

    def test_extractor_bridge_supports_native_tool_round_trip_plain_final_and_activity(self):
        class Model(BaseHTTPRequestHandler):
            calls = 0
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                Model.calls += 1
                answer = ('{"tool_calls":[{"name":"save","arguments":{"fact":"synthetic"}}]}'
                          if Model.calls == 1 else "Completed the requested extraction.")
                data = json.dumps({"data": {"text": answer}}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threads = [threading.Thread(target=upstream.serve_forever, daemon=True)]
        threads[0].start()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp)/"ledger.jsonl"
            bridge = make_server({"opencode_port": upstream.server_port, "password": "synthetic-password",
                "observer_token": "synthetic-observer", "extractor_token": "synthetic-extractor", "extraction_call_limit": 2}, ledger)
            threads.append(threading.Thread(target=bridge.serve_forever, daemon=True))
            threads[-1].start()
            base = f"http://127.0.0.1:{bridge.server_port}"
            tools = [{"type": "function", "function": {"name": "save", "parameters": {"type": "object"}}}]
            try:
                headers = {"Authorization": "Bearer synthetic-extractor"}
                first = post(base+"/v1/chat/completions", {"messages": [], "tools": tools}, headers)
                self.assertEqual(first["choices"][0]["finish_reason"], "tool_calls")
                call = first["choices"][0]["message"]["tool_calls"][0]
                self.assertEqual(json.loads(call["function"]["arguments"]), {"fact": "synthetic"})
                req = urllib.request.Request(base+"/v1/chat/completions",
                    data=json.dumps({"messages": [{"role": "tool", "content": "saved", "tool_call_id": call["id"]}],
                                     "tools": tools, "stream": True}).encode(), headers=headers)
                with urllib.request.urlopen(req) as response:
                    stream = response.read().decode()
                self.assertIn('"finish_reason": "stop"', stream)
                self.assertIn("Completed the requested extraction.", stream)
                with urllib.request.urlopen(urllib.request.Request(base+"/activity",
                        headers={"Authorization": "Bearer synthetic-observer"})) as response:
                    activity = json.load(response)
                self.assertEqual((activity["seq"], activity["in_flight"], activity["last_agent_final_seq"]), (2, 0, 2))
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    post(base+"/v1/chat/completions", {"messages": [], "tools": tools}, headers)
                failure.exception.close()
                self.assertEqual(Model.calls, 2)
                self.assertNotIn("synthetic-password", ledger.read_text())
                self.assertNotIn("synthetic-extractor", ledger.read_text())
            finally:
                bridge.shutdown()
                bridge.server_close()
                upstream.shutdown()
                upstream.server_close()
                for thread in threads:
                    thread.join()

    def test_extractor_bridge_rejects_unknown_calls_and_ambiguous_json(self):
        tools = [{"function": {"name": "save"}}]
        batch = {"tool_calls": [{"name": "save", "arguments": {"fact": str(i)}} for i in range(10)]}
        message, finish = model_message(json.dumps(batch), tools)
        self.assertEqual((len(message["tool_calls"]), finish), (10, "tool_calls"))
        for text in ['{"tool_calls":[{"name":"shell","arguments":{}}]}',
                     '{"tool_calls":[]}','{"content":"done"} {"tool_calls":[]}']:
            with self.assertRaises(ValueError):
                model_message(text, tools)

    def test_missing_v5_endpoint_is_a_redacted_setup_failure_with_zero_reader_calls(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"untrusted response body"}')
            def do_DELETE(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                state = folder/"state.json"
                state.write_text(json.dumps({"supermemory": {"base_url": f"http://127.0.0.1:{server.server_port}",
                    "api_key": "synthetic-private-key", "version": "test-build", "extraction_model": "test-model"}}))
                output = folder/"run"
                world, _ = worlds()[0]
                process = subprocess.run([sys.executable, str(Path(__file__).with_name("run.py")), "--state", str(state),
                    "--systems", "supermemory", "--episodes", world["id"], "--output", str(output)], capture_output=True, text=True)
                self.assertEqual(process.returncode, 1, process.stderr)
                result = json.loads((output/"results.json").read_text())
                self.assertEqual(result["rows"][0]["status"], "setup_failed")
                self.assertEqual(result["rows"][0]["error"], "Supermemory HTTP 404")
                trace = (output/"trace.jsonl").read_text()
                self.assertNotIn('"kind": "model"', trace)
                self.assertNotIn("synthetic-private-key", trace)
                graded = subprocess.run([sys.executable, str(Path(__file__).with_name("grade.py")), str(output)], capture_output=True, text=True)
                self.assertEqual(graded.returncode, 0, graded.stderr)
                summary = json.loads((output/"graded.json").read_text())["summary"]["supermemory"]
                self.assertEqual((summary["denominator"], summary["answered"]), (3, 0))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_only_returned_source_document_content_is_promoted_to_grounded_records(self):
        fact = {"id": "assertion-test", "subject": "node-a", "predicate": "bench:risk", "object": "open"}
        response = with_source_records({"documents": [{"id": "source-test", "content": json.dumps({"assertions": [fact]})}],
                                      "memory": "assertion-unread implies node-b risk"})
        self.assertEqual(observed_facts(response), [fact])
        rendered = source_content({"assertions": [fact], "names": []})
        self.assertEqual(observed_facts(with_source_records({"content": rendered})), [fact])
        self.assertEqual(observed_facts(with_source_records({"content": "not JSON", "memory": "assertion-unread"})), [])

    def test_expansion_is_reproducible_and_all_24_queries_have_two_agreeing_references(self):
        suite = generate()
        self.assertEqual(suite, generate())
        self.assertNotEqual(suite, generate(2026110207))
        rows = verify(suite)
        self.assertEqual((len(suite["episodes"]), len({w["pair_id"] for w in suite["episodes"]}), len(rows)), (8, 4, 24))
        self.assertTrue(all(row["references_agree"] for row in rows))
        for i in range(0, 8, 2):
            first, second = suite["episodes"][i:i+2]
            self.assertNotEqual([r["expected"] for r in rows if r["episode"] == first["id"]],
                                [r["expected"] for r in rows if r["episode"] == second["id"]])
            left = {f["id"]: f for f in first["facts"]}
            right = {f["id"]: f for f in second["facts"]}
            self.assertEqual(left.keys(), right.keys())
            self.assertEqual(sum(left[k] != right[k] for k in left), 1)

    def test_holdout_uses_new_structures_and_independent_reference_agreement(self):
        suite = generate_holdout()
        self.assertEqual(suite, generate_holdout())
        self.assertEqual(len(verify(suite)), 24)
        def shape(world):
            # An isomorphism-invariant degree profile; differing profiles prove
            # these are more than renamed versions of a development template.
            nodes = {f["subject"] for f in world["facts"]} | {
                f["object"] for f in world["facts"] if f["predicate"] != "bench:risk"}
            return sorted((sum(f["subject"] == node and f["predicate"] == "bench:issuer" for f in world["facts"]),
                           sum(f["subject"] == node and f["predicate"] == "org:subOrganizationOf" for f in world["facts"]),
                           sum(f["object"] == node and f["predicate"] == "org:subOrganizationOf" for f in world["facts"]))
                          for node in nodes)
        old = [shape(w) for w in generate()["episodes"]]
        for index in range(0, 8, 2):
            left, right = suite["episodes"][index:index+2]
            self.assertNotIn(shape(left), old)
            left_facts, right_facts = [{f["id"]: f for f in w["facts"]} for w in [left, right]]
            self.assertEqual(sum(left_facts[key] != right_facts[key] for key in left_facts), 1)

    def test_custom_suite_grading_preserves_selected_denominator_and_rejects_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            suite = folder/"suite.json"
            data = generate()
            suite.write_text(json.dumps(data))
            selected = data["episodes"][0]
            results = {"fixture_sha256": hashlib.sha256(suite.read_bytes()).hexdigest(),
                       "selected_episode_ids": [selected["id"]],
                       "rows": [{"system": "lobu", "episode": selected["id"], "status": "setup_failed"}]}
            (folder/"results.json").write_text(json.dumps(results))
            (folder/"trace.jsonl").write_text("")
            command = [sys.executable, str(Path(__file__).with_name("grade.py")), str(folder), "--suite", str(suite)]
            done = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            graded = json.loads((folder/"graded.json").read_text())
            self.assertEqual(graded["summary"]["lobu"]["denominator"], 3)
            self.assertEqual(graded["summary"]["lobu"]["answered"], 0)
            suite.write_text(json.dumps({**data, "seed": "modified"}))
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_report_loader_checks_source_hashes_and_retains_strict_semantic_distinction(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            scenario = {"id": "synthetic", "steps": [{"kind": "memory", "id": "r1", "content": json.dumps({"name": "Synthetic root"})}]}
            (folder/"suite.json").write_text(json.dumps({"scenarios": [scenario]}))
            suite_hash = hashlib.sha256((folder/"suite.json").read_bytes()).hexdigest()
            systems, tasks = [], []
            for name in ["lobu", "supermemory", "full-context-control"]:
                row = {"scenarioId": "synthetic", "questionId": "q", "category": "comparison", "prompt": "Synthetic question?",
                       "expectedAnswers": ["R"], "answer": "Root R", "score": {"answerCorrect": 0, "retrievalRecall": 1},
                       "retrievedIds": ["r1"], "expectedSourceStepIds": ["r1"], "citedIds": ["r1"], "latencyMs": 1}
                source = folder/(name+".json")
                source.write_text(json.dumps({"provenance": {"suiteSha256": suite_hash}, "systems": [{"trials": [{"questions": [row]}]}]}))
                source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
                systems.append({"system": name, "sourceReport": source.name, "sourceSha256": source_hash, "semanticCorrect": 1})
                tasks.append({"system": name, "question_id": "q", "source_sha256": source_hash, "source_file": source.name,
                              "answer": "Root R", "expected": ["R"], "semantic_correct": True, "reason": "Synthetic alias"})
            (folder/"comparison.json").write_text(json.dumps({"suiteSha256": suite_hash, "systems": systems}))
            (folder/"semantic-review.json").write_text(json.dumps({"tasks": tasks, "model": "synthetic", "judge": "same-reader"}))
            result = load_retrieval(folder)
            self.assertEqual(result["systems"][0]["strictCorrect"], 0)
            self.assertTrue(result["questions"][0]["systems"]["lobu"]["semantic"])
            (folder/"lobu.json").write_text('{}')
            with self.assertRaises(ValueError):
                load_retrieval(folder)

    def test_both_worlds_and_all_checkpoints_have_independent_reference_agreement(self):
        for world, expected in worlds():
            for cp, want in zip(world["checkpoints"], expected):
                with self.subTest(world=world["id"], cp=cp["id"]):
                    withdrawn = set(cp["withdraw"])
                    self.assertEqual(sorted(path_oracle(world, withdrawn)), want["ids"])
                    self.assertEqual(relational_oracle(world, withdrawn), want["ids"])
                    self.assertTrue(verify_exact_set(world, cp, want["ids"], make_frontier_certificate(world, cp)))

    def test_valid_alternative_proofs_and_stale_unread_fabricated_evidence(self):
        world, _ = worlds()[0]
        q0, q1, _ = world["checkpoints"]
        root, paths = next(iter(path_oracle(world, set()).items()))
        reads = world["facts"]
        for proof in paths:
            final = {"answer": [root], "proof": proof, "certificate": make_frontier_certificate(world, q0)}
            self.assertTrue(all(grade_answer(world, q0, final, reads)[k] for k in ["answer_correct", "proof_grounded", "exact_set_certified"]))
            self.assertFalse(grade_answer(world, q0, final, [])["exact_set_certified"])
            self.assertFalse(sufficient_positive_proof(world, set(), root, proof[:-1]))
            self.assertFalse(sufficient_positive_proof(world, set(), root, proof+["fabricated"]))
        stale = {"answer": [root], "proof": paths[0], "certificate": make_frontier_certificate(world, q0)}
        grades = grade_answer(world, q1, stale, reads)
        self.assertTrue(grades["answer_correct"])
        self.assertFalse(grades["proof_grounded"])
        self.assertFalse(grades["exact_set_certified"])

    def test_absence_requires_closed_frontier_and_snapshot_receipts(self):
        world, _ = worlds()[1]
        cp = world["checkpoints"][1]
        cert = make_frontier_certificate(world, cp)
        final = {"answer": [], "proof": [], "certificate": cert}
        self.assertTrue(grade_answer(world, cp, final, world["facts"])["exact_set_certified"])
        for field, value in [("snapshot_id", "old"), ("required_watermark", -1), ("principal", "other"),
                             ("authorization_scope", "other"), ("source_id", "other"), ("health", "partial"),
                             ("issuer_pages_complete", False), ("issuer_terminal_cursor", "next")]:
            bad = {**cert, field: value}
            self.assertFalse(grade_answer(world, cp, {**final, "certificate": bad}, world["facts"])["exact_set_certified"])
        for change in ["missing_node", "missing_edge", "next_page"]:
            bad = copy.deepcopy(cert)
            node = next(iter(bad["frontier"]))
            if change == "missing_node":
                del bad["frontier"][node]
            elif change == "missing_edge":
                bad["frontier"][node]["outbound_assertions"].pop()
            else:
                bad["frontier"][node]["terminal_cursor"] = "next"
            self.assertFalse(grade_answer(world, cp, {**final, "certificate": bad}, world["facts"])["exact_set_certified"])

    def test_fixture_bytes_match_generated_worlds_and_no_gold_is_serialized(self):
        fixture = Path(__file__).resolve().parents[2]/"fixtures/graph-agent/worlds.json"
        self.assertEqual(json.loads(fixture.read_text())["episodes"], [w for w, _ in worlds()])

    def test_bad_answers_and_malformed_citations_never_pass_or_crash_grading(self):
        world, _ = worlds()[0]
        cp = world["checkpoints"][0]
        for final in [{}, {"answer": [], "proof": [], "certificate": {}},
                      {"answer": [], "proof": [], "certificate": {"issuer_assertions": [{}]}},
                      {"answer": [world["names"][1]["id"]], "proof": [], "certificate": {}},
                      {"answer": [], "proof": [], "certificate": {"frontier": {"node": []}}}]:
            grade = grade_answer(world, cp, final, world["facts"])
            self.assertFalse(grade["answer_correct"])
            self.assertFalse(grade["proof_grounded"])
            self.assertFalse(grade["exact_set_certified"])

    def test_final_can_follow_last_permitted_tool_and_future_updates_stay_hidden(self):
        world, _ = worlds()[0]
        class Store:
            manifest = {"read": {}}
            def withdraw(self, ids):
                pass
            def invoke(self, tool, args):
                return {"page_complete": True}
        class Reader:
            def __init__(self):
                self.turn = 0
                self.first_prompt = None
            def append(self, value):
                pass
            def generate(self, messages, remaining):
                self.turn += 1
                if self.first_prompt is None:
                    self.first_prompt = json.dumps(messages)
                if self.turn == 1:
                    return json.dumps({"calls": [{"tool": "read", "arguments": {}}]*25})
                return json.dumps({"final": {"answer": []}})
        reader = Reader()
        rows = episode(world, Store(), reader, "boundary-test")
        self.assertTrue(all(r["status"] == "answered" for r in rows))
        self.assertEqual(rows[-1]["cumulative_tool_calls"], 25)
        self.assertNotIn('"q1"', reader.first_prompt)
        self.assertNotIn('"q2"', reader.first_prompt)
        self.assertNotIn('s0004', reader.first_prompt)

    def test_global_budget_exhaustion_remains_a_budget_result(self):
        world, _ = worlds()[0]
        class Store:
            manifest = {}
            def withdraw(self, ids):
                pass
        class Reader:
            def append(self, value):
                pass
            def generate(self, messages, remaining):
                raise BudgetExhausted("global cap")
        rows = episode(world, Store(), Reader(), "budget-boundary")
        self.assertEqual([r["status"] for r in rows], ["budget_exhausted"]*3)

    def test_failed_source_update_keeps_earlier_answer_and_stops_dependent_questions(self):
        world, _ = worlds()[0]
        class Store:
            manifest = {}
            def withdraw(self, ids):
                if ids:
                    raise RuntimeError("synthetic source update failure")
        class Reader:
            def append(self, value):
                pass
            def generate(self, messages, remaining):
                return json.dumps({"final": {"answer": []}})
        rows = episode(world, Store(), Reader(), "source-update-boundary")
        self.assertEqual([r["status"] for r in rows], ["answered", "source_update_failed", "not_attempted"])

    def test_protocol_rejects_native_tools_and_mixed_final_calls(self):
        self.assertEqual(parse_reply('{"calls":[{"tool":"query","arguments":{"sql":"SELECT 1"}}]}')["calls"][0]["tool"], "query")
        self.assertEqual(parse_reply('Checking now. {"final":{"answer":[]}}'), {"final":{"answer":[]}})
        self.assertEqual(parse_reply('```json\n{"final":{}}\n```'), {"final":{}})
        for text in ['{}', '{"calls":[],"final":{}}', '{"calls":[]}', '{"calls":[{"tool":"shell"}]}', 'not json', '{"final":{}} {"calls":[]}']:
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_reply(text)
        # An ID string by itself is not evidence of reading its assertion.
        self.assertEqual(observed_facts({"rows": [{"id": "s0004"}], "proof": ["s0004"]}), [])
        self.assertEqual(SqlError('ERROR:  25006: cannot execute DELETE').sqlstate, '25006')
        self.assertIsNone(SqlError('connection refused').sqlstate)

    def test_live_http_boundary_rejects_redirects_and_remote_targets(self):
        for url in ["https://app.lobu.ai", "http://127.0.0.1.evil.test", "http://u:p@localhost", "http://localhost/path"]:
            with self.assertRaises(ValueError):
                loopback(url)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://example.invalid")
                    self.end_headers()
                else:
                    body = json.dumps({"received": json.loads(self.rfile.read(int(self.headers["Content-Length"])))}).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:"+str(server.server_port)
            self.assertEqual(post(base, {"synthetic": True}), {"received": {"synthetic": True}})
            with self.assertRaises(ValueError):
                post(base+"/redirect", {})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
