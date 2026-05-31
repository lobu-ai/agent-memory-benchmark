#!/usr/bin/env python3
"""Benchmark adapter for SELF-HOSTED Mem0 (the open-source `mem0` library).

No Mem0 cloud, no SaaS quota. Mem0's OSS engine runs in-process with:
  - LLM (fact extraction): any OpenAI-compatible endpoint (default z.ai glm-5.1)
  - embedder: Gemini (default) or set MEM0_EMBEDDER=huggingface for fully local
  - vector store: local Chroma (on-disk, no server)

This is the fair way to run Mem0 head-to-head: same control over the model as
every other system, and no async cloud-indexing variability.

Env:
  Z_AI_API_KEY / OPENAI_API_KEY   LLM key (extraction)
  GEMINI_API_KEY                  embedder key (Gemini)
  MEM0_LLM_MODEL                  default glm-5.1
  MEM0_LLM_BASE_URL               default https://api.z.ai/api/coding/paas/v4/
  MEM0_CHROMA_PATH                default /tmp/mem0-bench-chroma
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_protocol import serve, set_output  # noqa: E402

# Mem0/Chroma/litellm print to stdout, which would corrupt the JSONL protocol.
# Keep a private dup of the real stdout for protocol writes and route ALL other
# stdout (Python + C-level fd 1) to stderr.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
_PROTO = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr
set_output(_PROTO)

_LLM_KEY = os.environ.get("Z_AI_API_KEY") or os.environ.get("OPENAI_API_KEY")
if _LLM_KEY:
    os.environ["OPENAI_API_KEY"] = _LLM_KEY  # Mem0's openai provider reads this

_MEMORY = None


def memory():
    global _MEMORY
    if _MEMORY is not None:
        return _MEMORY
    from mem0 import Memory

    # Default to a local retrieval-grade embedder (BGE). Gemini's embed API
    # needs an asymmetric query/document task_type that Mem0's embedder doesn't
    # set, so it ranks near-randomly here — BGE is symmetric and retrieval-ready
    # (the same family Lobu/Hindsight use). Set MEM0_EMBEDDER=gemini to override.
    embedder = (
        {"provider": "gemini", "config": {"model": "models/gemini-embedding-001"}}
        if os.environ.get("MEM0_EMBEDDER") == "gemini"
        else {"provider": "huggingface", "config": {"model": "BAAI/bge-small-en-v1.5"}}
    )
    cfg = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": os.environ.get("MEM0_LLM_MODEL", "glm-5.1"),
                "openai_base_url": os.environ.get(
                    "MEM0_LLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4/"
                ),
                "temperature": 0,
            },
        },
        "embedder": embedder,
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": "agent_memory_bench",
                "path": os.environ.get("MEM0_CHROMA_PATH", "/tmp/mem0-bench-chroma"),
            },
        },
    }
    _MEMORY = Memory.from_config(cfg)
    return _MEMORY


def scope_user_id(payload: Dict[str, Any]) -> str:
    run_id = str(payload.get("runId") or "benchmark-run")
    return "bench-" + hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:16]


def parse_turns(content: str) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    header = None
    for chunk in re.split(r"\n(?=Turn \d+ \()", content):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"Turn \d+ \((user|assistant)[^)]*\):\s*(.*)", chunk, re.S)
        if m:
            text = m.group(2).strip()
            if text:
                msgs.append({"role": m.group(1), "content": text})
        elif header is None:
            header = chunk
    if header and msgs:
        msgs.insert(0, {"role": "user", "content": header})
    return msgs or [{"role": "user", "content": content.strip()}]


def action_reset(payload: Dict[str, Any]) -> Any:
    try:
        memory().delete_all(user_id=scope_user_id(payload))
    except Exception:
        pass
    return None


def action_setup(payload: Dict[str, Any]) -> Any:
    return None


def action_ingest(payload: Dict[str, Any]) -> Any:
    scenario = payload.get("scenario") or {}
    steps = scenario.get("steps") or []
    user_id = scope_user_id(payload)
    m = memory()
    created = 0
    for step in steps:
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        m.add(
            parse_turns(content),
            user_id=user_id,
            metadata={
                "benchmark_id": str(step.get("id")),
                "benchmark_scenario_id": scenario.get("id"),
            },
            infer=True,
        )
        created += 1
    return {"created": created}


def action_retrieve(payload: Dict[str, Any]) -> Any:
    user_id = scope_user_id(payload)
    top_k = int(payload.get("topK") or 8)
    started = time.perf_counter()
    res = memory().search(
        payload.get("prompt") or "",
        filters={"user_id": user_id},
        limit=max(top_k * 4, 20),
    )
    latency_ms = (time.perf_counter() - started) * 1000
    hits = res.get("results") if isinstance(res, dict) else res

    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for h in hits or []:
        meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
        bid = str(meta.get("benchmark_id") or h.get("id"))
        if bid not in grouped:
            grouped[bid] = {
                "id": bid,
                "text": h.get("memory") or "",
                "score": h.get("score"),
                "sourceType": "memory",
                "metadata": meta,
            }
            order.append(bid)
    items = [grouped[b] for b in order][:top_k]
    return {"items": items, "latencyMs": latency_ms}


ACTIONS = {
    "reset": action_reset,
    "setup": action_setup,
    "ingest": action_ingest,
    "retrieve": action_retrieve,
}

if __name__ == "__main__":
    raise SystemExit(serve(ACTIONS))
