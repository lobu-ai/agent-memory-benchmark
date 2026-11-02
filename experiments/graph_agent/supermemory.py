"""Supermemory document/search tools. Structural source mapping only, no solver."""
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "adapters"))
from _supermemory_v5 import Client, ReadinessError, namespace


def with_source_records(value):
    """Decode only source JSON actually returned by the public document API.

    Extracted memory prose is not promoted to an exact source assertion. The
    grader independently matches returned records with the sealed input stream.
    """
    if isinstance(value, list):
        return [with_source_records(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: with_source_records(v) for k, v in value.items()}
    if isinstance(value.get("content"), str):
        try:
            source = json.loads(value["content"])
            if isinstance(source, dict) and isinstance(source.get("assertions"), list):
                result["source_records"] = source
        except json.JSONDecodeError:
            pass
    return result


class Supermemory:
    manifest = {
        "search": {"description": "Supermemory search with native related-memory graph and source documents included. Search absence is not a complete source enumeration.",
                   "arguments": {"query": "nonempty string", "limit": "integer 1..100 (default 10)",
                                 "mode": "hybrid (default), memories, or chunks"}},
        "list_documents": {"description": "Page ALL source documents with their published content. Native pagination.totalPages bounds enumeration; source_records decodes only returned source JSON.",
                           "arguments": {"page": "integer >= 1", "limit": "integer 1..100"}},
        "get_document": {"description": "Read a source document by ID within this episode's namespace, including current published source records.",
                         "arguments": {"id": "document ID string"}},
        "list_memories": {"description": "Page current extracted memories; this is not source enumeration.",
                          "arguments": {"page": "integer >= 1", "limit": "integer 1..100"}},
    }

    def __init__(self, config):
        self.api = Client(config["base_url"], config["api_key"], namespace(uuid.uuid4().hex),
                          ready_timeout=config.get("ready_timeout", 180))
        self.config = {key: config[key] for key in ["version", "extraction_model"]}
        self.config.update(api="v5", taskType="memory", dreaming="instant", rerank="none",
                           grouping="ten assertions per source document, original input order")
        self.documents, self.receipts = {}, []

    def load(self, world):
        self.world_id = world["id"]
        # Fixed input-order groups, never answer-specific selection or closure.
        names = {n["id"]: n["label"] for n in world["names"]}
        for offset in range(0, len(world["facts"]), 10):
            doc_id = "source_" + str(offset // 10)
            facts = world["facts"][offset:offset+10]
            refs = {f["subject"] for f in facts} | {f["object"] for f in facts if f["predicate"] != "bench:risk"}
            source = {"assertions": facts, "names": [{"id": r, "label": names.get(r, r)} for r in sorted(refs)]}
            self.documents[doc_id] = source
            self.publish(doc_id)

    def publish(self, doc_id, replace=False):
        content = json.dumps(self.documents[doc_id], sort_keys=True)
        self.receipts.append(self.api.write(doc_id, content, {"episode": self.world_id}, replace=replace))

    def withdraw(self, ids):
        removed = set(ids)
        for doc_id, source in self.documents.items():
            current = [f for f in source["assertions"] if f["id"] not in removed]
            if len(current) != len(source["assertions"]):
                source["assertions"] = current
                self.publish(doc_id, replace=True)

    def invoke(self, tool, args):
        if tool not in self.manifest or not isinstance(args, dict):
            raise ValueError("Unknown tool")
        if tool == "search":
            if set(args) - {"query", "limit", "mode"} or type(args.get("limit", 10)) is not int:
                raise ValueError("Invalid search arguments")
            value = self.api.search(args.get("query"), args.get("limit", 10), args.get("mode", "hybrid"))
        elif tool == "get_document":
            if set(args) != {"id"} or not isinstance(args["id"], str) or not 1 <= len(args["id"]) <= 200:
                raise ValueError("Expected document ID")
            value = self.api.get(args["id"])
        else:
            if set(args) - {"page", "limit"}:
                raise ValueError("Invalid pagination arguments")
            value = self.api.list("documents" if tool == "list_documents" else "memories",
                                  args.get("page", 1), args.get("limit", 100))
        return with_source_records(value)

    def receipt(self):
        return {**self.config, "namespace": self.api.scope, "ready_documents": self.receipts,
                "http_requests": self.api.requests, "extraction_usage": None}

    def cleanup(self):
        return self.api.cleanup()
