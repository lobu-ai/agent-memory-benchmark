#!/usr/bin/env python3
"""Benchmark adapter for Tree Ring Memory (TerminallyLazy/Tree_Ring_Memory).

This adapter talks to Tree Ring only through its public CLI:

  - ingest:   tree-ring remember --json
  - retrieve: tree-ring recall --json --include-sensitive

Each benchmark run id gets an isolated Tree Ring root under TREE_RING_ROOT_BASE,
so scenarios do not share memory. Benchmark step ids are stored as tags and then
mapped back from recalled events, preserving the harness' citation/retrieval
scoring contract without reading Tree Ring's SQLite database.

Env:
  TREE_RING_BIN        default tree-ring
  TREE_RING_ROOT_BASE  default /tmp/tree-ring-benchmark
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_protocol import serve  # noqa: E402

TREE_RING_BIN = os.environ.get("TREE_RING_BIN", "tree-ring")
ROOT_BASE = Path(os.environ.get("TREE_RING_ROOT_BASE", "/tmp/tree-ring-benchmark"))
PROJECT_PREFIX = os.environ.get("TREE_RING_PROJECT_PREFIX", "agent-memory-benchmark")
COMMAND_TIMEOUT_SECONDS = int(os.environ.get("TREE_RING_COMMAND_TIMEOUT_SECONDS", "120"))

_INITIALIZED: set[str] = set()

STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "been",
    "before",
    "being",
    "could",
    "first",
    "from",
    "had",
    "has",
    "have",
    "into",
    "its",
    "memory",
    "should",
    "that",
    "the",
    "their",
    "there",
    "these",
    "thing",
    "this",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
    "was",
    "were",
}


def run_key(run_id: str) -> str:
    digest = hashlib.sha1(run_id.encode("utf-8")).hexdigest()[:20]
    return f"run-{digest}"


def project_name(run_id: str) -> str:
    return f"{PROJECT_PREFIX}-{run_key(run_id)}"


def root_for(payload: Dict[str, Any]) -> Path:
    run_id = str(payload.get("runId") or "benchmark-run")
    return ROOT_BASE / run_key(run_id)


def run_tree_ring(args: List[str]) -> str:
    result = subprocess.run(
        [TREE_RING_BIN, *args],
        check=False,
        text=True,
        capture_output=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"{TREE_RING_BIN} {' '.join(args)} exited {result.returncode}")
    return result.stdout


def parse_json_output(stdout: str) -> Any:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [idx for idx in (text.find("{"), text.find("[")) if idx >= 0]
        start = min(starts) if starts else -1
        end = max(text.rfind("}"), text.rfind("]"))
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def ensure_root(payload: Dict[str, Any]) -> Path:
    root = root_for(payload)
    root_str = str(root)
    if root_str in _INITIALIZED:
        return root
    root.mkdir(parents=True, exist_ok=True)
    parse_json_output(run_tree_ring(["init", "--root", root_str, "--json"]))
    _INITIALIZED.add(root_str)
    return root


def tag_value(tags: List[Any], prefix: str) -> Optional[str]:
    for tag in tags:
        if isinstance(tag, str) and tag.startswith(prefix):
            return tag[len(prefix) :]
    return None


def recall_queries(prompt: str) -> List[str]:
    queries = [prompt]
    terms = [
        token.lower()
        for token in "".join(ch.lower() if ch.isalnum() else " " for ch in prompt).split()
        if len(token) >= 3 and token.lower() not in STOPWORDS
    ]
    simplified = " ".join(dict.fromkeys(terms))
    if simplified and simplified != prompt.lower():
        queries.append(simplified)
    unique_terms = list(dict.fromkeys(terms))
    for width in (3, 2):
        if len(unique_terms) < width:
            continue
        for index in range(0, len(unique_terms) - width + 1):
            window = " ".join(unique_terms[index : index + width])
            if window not in queries:
                queries.append(window)
    return queries


def action_reset(payload: Dict[str, Any]) -> Any:
    root = root_for(payload)
    _INITIALIZED.discard(str(root))
    shutil.rmtree(root, ignore_errors=True)
    return None


def action_setup(payload: Dict[str, Any]) -> Any:
    ensure_root(payload)
    return None


def action_ingest(payload: Dict[str, Any]) -> Any:
    scenario = payload.get("scenario") or {}
    steps = scenario.get("steps") or []
    root = ensure_root(payload)
    project = project_name(str(payload.get("runId") or "benchmark-run"))
    created = 0
    refused = 0

    for step in steps:
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue

        step_id = str(step.get("id") or "")
        tags = [
            "benchmark",
            f"benchmark_id:{step_id}",
            f"scenario:{scenario.get('id')}",
            f"kind:{step.get('kind')}",
        ]
        if step.get("semanticType"):
            tags.append(f"semantic:{step.get('semanticType')}")

        args = [
            "remember",
            "--root",
            str(root),
            "--json",
            "--event-type",
            "observation",
            "--scope",
            "eval",
            "--project",
            project,
        ]
        for tag in tags:
            args.extend(["--tag", tag])
        args.append(content)

        try:
            parse_json_output(run_tree_ring(args))
            created += 1
        except RuntimeError as exc:
            # Tree Ring intentionally refuses secret-like memories. Treat that
            # as an honest product policy result, not an adapter crash.
            if "blocked by policy" in str(exc).lower():
                refused += 1
                continue
            raise

    return {"created": created, "refused": refused}


def action_retrieve(payload: Dict[str, Any]) -> Any:
    root = ensure_root(payload)
    project = project_name(str(payload.get("runId") or "benchmark-run"))
    top_k = int(payload.get("topK") or 8)
    prompt = str(payload.get("prompt") or "")

    started = time.perf_counter()
    all_rows: List[Any] = []
    seen_tree_ring_ids: set[str] = set()
    for query in recall_queries(prompt):
        rows = parse_json_output(
            run_tree_ring(
                [
                    "recall",
                    "--root",
                    str(root),
                    "--json",
                    "--project",
                    project,
                    "--limit",
                    str(max(top_k * 4, 20)),
                    "--include-sensitive",
                    query,
                ]
            )
        )
        for row in rows if isinstance(rows, list) else []:
            memory = row.get("memory") if isinstance(row, dict) else {}
            memory_id = memory.get("id") if isinstance(memory, dict) else None
            key = str(memory_id or len(all_rows))
            if key in seen_tree_ring_ids:
                continue
            seen_tree_ring_ids.add(key)
            all_rows.append(row)
        if len(all_rows) >= top_k:
            break
    latency_ms = (time.perf_counter() - started) * 1000

    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for index, row in enumerate(all_rows):
        memory = row.get("memory") if isinstance(row, dict) else {}
        if not isinstance(memory, dict):
            continue
        tags = memory.get("tags") if isinstance(memory.get("tags"), list) else []
        benchmark_id = tag_value(tags, "benchmark_id:") or str(memory.get("id") or f"tree-ring-{index}")
        text = str(memory.get("summary") or memory.get("details") or "")
        if not text:
            continue
        if benchmark_id not in grouped:
            grouped[benchmark_id] = {
                "id": benchmark_id,
                "text": text,
                "score": row.get("score") if isinstance(row, dict) else None,
                "sourceType": "memory",
                "metadata": {
                    "treeRingId": memory.get("id"),
                    "eventType": memory.get("event_type"),
                    "createdAt": memory.get("created_at"),
                    "tags": tags,
                },
            }
            order.append(benchmark_id)

    items = [grouped[benchmark_id] for benchmark_id in order][:top_k]
    return {"items": items, "latencyMs": latency_ms, "raw": {"returned": len(all_rows)}}


def action_version(_payload: Dict[str, Any]) -> Any:
    try:
        out = run_tree_ring(["--version"]).strip()
        return out or None
    except Exception:
        return None


ACTIONS = {
    "reset": action_reset,
    "setup": action_setup,
    "ingest": action_ingest,
    "retrieve": action_retrieve,
    "version": action_version,
}


if __name__ == "__main__":
    raise SystemExit(serve(ACTIONS))
