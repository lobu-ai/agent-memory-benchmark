import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lobu_adapter as adapter


class VisibilityTests(unittest.TestCase):
    def ingest(self, hits, steps=None):
        payload = {"runId": "synthetic-run", "scenario": {
            "id": "synthetic-scenario", "steps": steps if steps is not None else [
                {"id": "synthetic-fact", "content": "A synthetic fact."}
            ]}}
        def call(tool, args):
            if tool == "save_memory":
                return {"id": 42}
            self.assertEqual(tool, "read_knowledge")
            self.assertEqual(args["agent_id"], "synthetic-run")
            return {"content": hits}
        with patch.dict(os.environ, {"LOBU_CHUNK_TURNS": "0"}), \
             patch.object(adapter, "EMBED_WAIT_S", 1), \
             patch.object(adapter.time, "time", side_effect=[0, 0, 2]), \
             patch.object(adapter.time, "sleep"), \
             patch.object(adapter, "call_tool", side_effect=call) as request:
            result = adapter.action_ingest(payload)
            return result, request.call_count

    def test_timeout_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "not searchable"):
            self.ingest([])

    def test_other_scenario_does_not_satisfy_visibility(self):
        with self.assertRaisesRegex(RuntimeError, "not searchable"):
            self.ingest([{"metadata": {"benchmark_scenario_id": "other", "benchmark_run_id": "synthetic-run"}}])

    def test_other_run_does_not_satisfy_visibility(self):
        with self.assertRaisesRegex(RuntimeError, "not searchable"):
            self.ingest([{"metadata": {"benchmark_scenario_id": "synthetic-scenario", "benchmark_run_id": "other"}}])

    def test_matching_scope_succeeds(self):
        result, calls = self.ingest([{"metadata": {"benchmark_scenario_id": "synthetic-scenario", "benchmark_run_id": "synthetic-run"}}])
        self.assertEqual(result, {"created": 1})
        self.assertEqual(calls, 2)

    def test_empty_ingest_needs_no_visibility_probe(self):
        result, calls = self.ingest([], [])
        self.assertEqual(result, {"created": 0})
        self.assertEqual(calls, 0)

    def test_skipped_blank_steps_need_no_visibility_probe(self):
        result, calls = self.ingest([], [{"id": "blank", "content": "  "}])
        self.assertEqual(result, {"created": 0})
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
