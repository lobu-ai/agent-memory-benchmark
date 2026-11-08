"""Source attribution and ranking transformations; no model/provider required."""
import unittest

from adapters.mem0_local_adapter import collapse_source_hits


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

    def test_substring_facts_are_not_silently_discarded(self):
        hits = [
            {"id": "a", "memory": "The supplier is not approved", "metadata": {"benchmark_id": "source"}},
            {"id": "b", "memory": "approved", "metadata": {"benchmark_id": "source"}},
            {"id": "c", "memory": "approved", "metadata": {"benchmark_id": "source"}},
        ]
        self.assertEqual(collapse_source_hits(hits, 1)[0]["text"], "The supplier is not approved\n\napproved")

    def test_empty_results_and_nonobject_metadata(self):
        self.assertEqual(collapse_source_hits([], 4), [])
        self.assertEqual(collapse_source_hits([{"id": "fallback", "metadata": None}], 1)[0]["id"], "fallback")
