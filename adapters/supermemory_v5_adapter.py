"""Document-pipeline retrieval board, separate from supermemory_adapter.py v4.

Required: SUPERMEMORY_BASE_URL, SUPERMEMORY_API_KEY, BENCH_SYSTEM_VERSION.
Optional: SUPERMEMORY_READY_TIMEOUT (180s), SUPERMEMORY_SEARCH_MODE (hybrid),
SUPERMEMORY_RERANK (none|order|aggregate), SUPERMEMORY_THRESHOLD (0).
Uses one source document per step; explicit supersession replaces its document.
"""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_protocol import serve
from _supermemory_v5 import Client, namespace


def client(payload):
    if not payload.get("runId") or not (payload.get("scenario") or {}).get("id"):
        raise ValueError("Run and scenario IDs required")
    return Client(os.environ["SUPERMEMORY_BASE_URL"], os.environ["SUPERMEMORY_API_KEY"],
                  namespace(payload["runId"], payload["scenario"]["id"]),
                  ready_timeout=float(os.environ.get("SUPERMEMORY_READY_TIMEOUT", "180")))


def ingest(payload):
    api = client(payload)
    receipts, ids = [], {}
    for step in payload["scenario"].get("steps", []):
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        previous = step.get("supersedes")
        doc_id = ids[previous] if previous else str(step["id"])
        metadata = {k: v if isinstance(v, (str, int, float, bool)) or
                    (isinstance(v, list) and all(isinstance(x, str) for x in v)) else json.dumps(v)
                    for k, v in (step.get("metadata") or {}).items()}
        metadata.update(benchmark_id=str(step["id"]), scenario_id=payload["scenario"]["id"])
        receipts.append(api.write(doc_id, content, metadata, replace=bool(previous)))
        ids[step["id"]] = doc_id
    return {"created": len(receipts), "api": "v5", "pipeline": "memory", "dreaming": "instant",
            "ready": receipts, "http_requests": api.requests}


def retrieve(payload):
    started = time.perf_counter()
    top_k = int(payload.get("topK") or 8)
    result = client(payload).search(payload.get("prompt") or "", top_k,
        mode=os.environ.get("SUPERMEMORY_SEARCH_MODE", "hybrid"),
        rerank=os.environ.get("SUPERMEMORY_RERANK", "none"),
        threshold=float(os.environ.get("SUPERMEMORY_THRESHOLD", "0")))
    grouped = {}
    for entry in result.get("results", []):
        document = (entry.get("included") or {}).get("document") or {}
        metadata = {**(document.get("metadata") or {}), **(entry.get("metadata") or {})}
        source_id = str(metadata.get("benchmark_id") or document.get("id") or entry["id"])
        item = grouped.setdefault(source_id, {"id": source_id, "text": "", "score": entry.get("similarity"),
                                             "sourceType": "memory", "metadata": metadata})
        text = entry.get("memory") or entry.get("chunk") or ""
        if text and text not in item["text"]:
            item["text"] = (item["text"] + "\n\n" + text).strip()
    # Keep the public API ranking, including rerank results. Never re-sort by
    # stale similarity or silently add related facts outside the top-K context.
    return {"items": list(grouped.values())[:top_k], "raw": result,
            "latencyMs": (time.perf_counter()-started)*1000}


if __name__ == "__main__":
    raise SystemExit(serve({"reset": lambda p: None, "setup": lambda p: None,
                           "ingest": ingest, "retrieve": retrieve}))
