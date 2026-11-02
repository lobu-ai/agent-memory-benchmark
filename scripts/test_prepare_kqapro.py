import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("prepare_kqapro", pathlib.Path(__file__).with_name("prepare-kqapro.py"))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PublicGraphBoundary(unittest.TestCase):
    def test_exported_records_pass_the_real_harness_boundary(self):
        records = {"A": {"name": "Source"}, "B": {"name": "Other source"}}
        question = {"question": "What color?", "answer": "blue", "program": []}
        scenario, _ = prepare.export_scenario(0, "synthetic", question, ["B", "A"], ["A"], records,
                                               lambda ref: json.dumps({"id": ref, "fact": "blue"}))
        suite = prepare.export_suite([scenario])
        repo = pathlib.Path(__file__).resolve().parents[1]
        result = subprocess.run(["bun", "-e",
            "import {validateBenchmarkSuite} from './harness/src/suite.ts'; "
            "validateBenchmarkSuite(await Bun.stdin.json()); console.log('valid');"],
            cwd=repo, input=json.dumps(suite), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "valid")

    def test_concept_parent_normalization_preserves_edges_and_refuses_ambiguity(self):
        kb = {"concepts": {"C": {"name": "Class", "subclassOf": ["D"]}}}
        normalized = prepare.normalize_kb(kb)
        self.assertEqual(normalized["concepts"]["C"]["instanceOf"], ["D"])
        self.assertEqual(normalized["concepts"]["C"]["subclassOf"], ["D"])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            prepare.normalize_kb({"concepts": {"C": {"instanceOf": ["A"], "subclassOf": ["B"]}}})
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            prepare.normalize_kb({"concepts": {}, "entities": {"A": {"relations": [{"relation": "a", "predicate": "b"}]}}})

    def test_changed_upstream_bytes_fail_before_reference_code_can_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "val.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                prepare.verified_file(root, "val.json")
            with self.assertRaisesRegex(ValueError, "Missing"):
                prepare.verified_file(root, "Program/executor_rule.py")

    def test_serialization_retains_identity_direction_units_and_qualifiers(self):
        kb = {"concepts": {"C": {"name": "Class", "instanceOf": ["C"]}}, "entities": {
            "A": {"name": "Namesake", "instanceOf": ["C"], "attributes": [
                {"key": "amount", "value": {"type": "quantity", "value": 0, "unit": "metre"},
                 "qualifiers": {"point in time": [{"type": "year", "value": 2001}]}}
            ], "relations": [{"predicate": "parent", "direction": "backward", "object": "B",
                              "qualifiers": {"start": [{"type": "year", "value": 1999}]}}]},
            "B": {"name": "Namesake", "instanceOf": [], "attributes": [], "relations": []},
        }}
        result = json.loads(prepare.record_text("A", kb))
        self.assertEqual(result["id"], "A")
        self.assertEqual(result["types"], [{"id": "C", "name": "Class"}])
        edge = result["relations"][0]
        self.assertEqual(edge["direction"], "backward")
        self.assertEqual(edge["object"]["id"], "B")
        self.assertEqual(edge["qualifiers"]["start"][0]["value"], 1999)
        amount = result["attributes"][0]
        self.assertEqual(amount["value"], {"type": "quantity", "value": 0, "unit": "metre"})
        self.assertEqual(amount["qualifiers"]["point in time"][0]["value"], 2001)
        self.assertNotIn("answer", result)


if __name__ == "__main__":
    unittest.main()
