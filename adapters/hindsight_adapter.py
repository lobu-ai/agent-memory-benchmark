#!/usr/bin/env python3
"""Benchmark adapter for Hindsight (vectorize-io/hindsight).

Talks to a locally running Hindsight API server (default http://localhost:8888)
over its REST API, using the shared JSONL-over-stdin benchmark protocol.

Mapping to the harness contract:
- Each benchmark step is retained as one Hindsight "document": its content is
  sent with document_id = step.id and metadata.benchmark_id = step.id, so that
  recalled facts carry provenance back to the originating step.
- retrieve() recalls facts for the question, then collapses them to the set of
  source document_ids (= step ids), preserving rank, deduped, capped at topK.
  That id list is what the harness scores against expectedSourceStepIds.

Hindsight performs LLM fact-extraction at retain time (its core value-add), so
ingest is synchronous (async=False) to guarantee facts are queryable before the
questions run.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_protocol import serve  # noqa: E402

BASE_URL = os.environ.get("HINDSIGHT_BASE_URL", "http://localhost:8888").rstrip("/")
TENANT = os.environ.get("HINDSIGHT_TENANT", "default")
# Retain content can be large (full chat sessions); give the LLM extraction room.
RETAIN_TIMEOUT = int(os.environ.get("HINDSIGHT_RETAIN_TIMEOUT", "300"))
RECALL_TIMEOUT = int(os.environ.get("HINDSIGHT_RECALL_TIMEOUT", "120"))
# Token budget for recall — generous enough to surface every relevant source doc.
RECALL_MAX_TOKENS = int(os.environ.get("HINDSIGHT_RECALL_MAX_TOKENS", "4096"))


def banks_path(suffix: str = "") -> str:
    return f"/v1/{TENANT}/banks{suffix}"


def request_json(method: str, path: str, body: Dict[str, Any] | None, timeout: int) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "lobu-hindsight-benchmark-adapter",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Hindsight API error {exc.code} on {method} {path}: {body_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Hindsight request failed on {method} {path}: {exc}") from exc


def bank_id(payload: Dict[str, Any]) -> str:
    run_id = str(payload.get("runId") or "benchmark-run")
    suffix = hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:20]
    return f"bench{suffix}"


def ensure_bank(bank: str) -> None:
    # PUT is idempotent: creates the bank or returns the existing one.
    request_json("PUT", banks_path(f"/{bank}"), {}, RECALL_TIMEOUT)


def clear_bank(bank: str) -> None:
    try:
        request_json("DELETE", banks_path(f"/{bank}/memories"), {}, RECALL_TIMEOUT)
    except RuntimeError:
        # Bank may not exist yet on the first reset — safe to ignore.
        pass


def action_reset(payload: Dict[str, Any]) -> Any:
    bank = bank_id(payload)
    ensure_bank(bank)
    clear_bank(bank)
    return None


def action_setup(payload: Dict[str, Any]) -> Any:
    ensure_bank(bank_id(payload))
    return None


def action_ingest(payload: Dict[str, Any]) -> Any:
    scenario = payload.get("scenario") or {}
    steps = scenario.get("steps") or []
    bank = bank_id(payload)
    ensure_bank(bank)

    items: List[Dict[str, Any]] = []
    for step in steps:
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        step_id = str(step.get("id"))
        metadata = {
            "benchmark_id": step_id,
            "scenario_id": scenario.get("id"),
            "step_kind": step.get("kind"),
        }
        if step.get("semanticType"):
            metadata["semantic_type"] = step.get("semanticType")
        items.append(
            {
                "content": content,
                "document_id": step_id,
                "metadata": metadata,
            }
        )

    if not items:
        return {"created": 0}

    # Synchronous retain so LLM extraction completes before questions run.
    request_json(
        "POST",
        banks_path(f"/{bank}/memories"),
        {"items": items, "async": False},
        RETAIN_TIMEOUT,
    )
    return {"created": len(items)}


def action_retrieve(payload: Dict[str, Any]) -> Any:
    bank = bank_id(payload)
    top_k = int(payload.get("topK") or 8)

    started = time.perf_counter()
    result = request_json(
        "POST",
        banks_path(f"/{bank}/memories/recall"),
        {"query": payload.get("prompt") or "", "max_tokens": RECALL_MAX_TOKENS},
        RECALL_TIMEOUT,
    )
    latency_ms = (time.perf_counter() - started) * 1000

    facts = (result or {}).get("results") or []
    # Collapse facts to their source step (document_id), preserving recall rank.
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for fact in facts:
        metadata = fact.get("metadata") if isinstance(fact.get("metadata"), dict) else {}
        source_id = fact.get("document_id") or metadata.get("benchmark_id") or fact.get("id")
        source_id = str(source_id)
        text = fact.get("text") or ""
        existing = grouped.get(source_id)
        if existing is None:
            grouped[source_id] = {
                "id": source_id,
                "text": text,
                "score": fact.get("score"),
                "sourceType": "memory",
                "metadata": metadata,
            }
            order.append(source_id)
        elif text and text not in existing["text"]:
            existing["text"] = f"{existing['text']}\n\n{text}".strip()

    items = [grouped[sid] for sid in order][:top_k]
    return {"items": items, "latencyMs": latency_ms, "raw": {"factCount": len(facts)}}


ACTIONS = {
    "reset": action_reset,
    "setup": action_setup,
    "ingest": action_ingest,
    "retrieve": action_retrieve,
}


if __name__ == "__main__":
    raise SystemExit(serve(ACTIONS))
