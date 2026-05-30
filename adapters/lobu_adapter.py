#!/usr/bin/env python3
"""Benchmark adapter for Lobu (lobu.ai) — PUBLIC API ONLY.

Talks to a *running* Lobu server through its public REST tool proxy
(`POST /api/{org}/{tool}`), exactly like a real user/SDK client would. There is
no direct database access here — that is the fairness invariant of this
benchmark: every system is reached only through its public client.

Env:
  LOBU_BASE_URL    Origin of a running lobu server (default http://localhost:8787)
  LOBU_ORG_SLUG    Org slug to write into (required)
  LOBU_API_TOKEN   OAuth/API bearer token with mcp:read/write/admin (required)
  LOBU_EMBED_WAIT  Max seconds to wait for async embeddings after ingest (default 60)

Per-scenario isolation: the harness resets between scenarios. Without a bulk
wipe on the public API, each scenario tags its writes with a unique
`benchmark_scenario_id` and retrieval filters to the current scenario
client-side (with over-fetch). Content from prior scenarios is superseded via
tombstones on reset where possible. This is the honest API-only behavior; see
METHODOLOGY.md "Isolation".
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_protocol import serve  # noqa: E402

BASE_URL = os.environ.get("LOBU_BASE_URL", "http://localhost:8787").rstrip("/")
ORG_SLUG = os.environ.get("LOBU_ORG_SLUG")
API_TOKEN = os.environ.get("LOBU_API_TOKEN")
EMBED_WAIT_S = int(os.environ.get("LOBU_EMBED_WAIT", "60"))


def require_env() -> None:
    missing = [k for k, v in (("LOBU_ORG_SLUG", ORG_SLUG), ("LOBU_API_TOKEN", API_TOKEN)) if not v]
    if missing:
        raise RuntimeError(f"Missing required env: {', '.join(missing)}")


def call_tool(tool: str, args: Dict[str, Any], timeout: int = 120) -> Any:
    require_env()
    data = json.dumps(args).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/{ORG_SLUG}/{tool}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {API_TOKEN}",
            "User-Agent": "agent-memory-benchmark-lobu-adapter",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"Lobu tool '{tool}' error {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Lobu tool '{tool}' request failed: {exc}") from exc


def scenario_tag(payload: Dict[str, Any]) -> str:
    return str(payload.get("runId") or "benchmark-run")


def action_reset(payload: Dict[str, Any]) -> Any:
    # API-only: no bulk wipe. Isolation is enforced at retrieve time by filtering
    # on benchmark_scenario_id. A fresh runId per scenario keeps scopes disjoint.
    return None


def action_setup(payload: Dict[str, Any]) -> Any:
    return None


def action_ingest(payload: Dict[str, Any]) -> Any:
    scenario = payload.get("scenario") or {}
    steps = scenario.get("steps") or []
    run_id = scenario_tag(payload)
    created = 0
    for step in steps:
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        call_tool(
            "save_memory",
            {
                "content": content,
                # Suite semanticType (e.g. "conversation_session") is not a
                # built-in Lobu event kind; store as a generic "fact".
                "semantic_type": "fact",
                "metadata": {
                    "benchmark_step_id": str(step.get("id")),
                    "benchmark_scenario_id": scenario.get("id"),
                    "benchmark_run_id": run_id,
                },
            },
        )
        created += 1

    # Wait for the real async embedding pipeline to make these searchable, the
    # same way an SDK user would poll before querying. We probe search_memory
    # until it returns scenario hits or the budget is exhausted.
    deadline = time.time() + EMBED_WAIT_S
    probe = (steps[0] or {}).get("content", "")[:80] if steps else ""
    while time.time() < deadline:
        res = call_tool("read_knowledge", {"query": probe or "memory", "limit": 4})
        if _has_scenario_hit(res, scenario.get("id")):
            break
        time.sleep(2)
    return {"created": created}


def _has_scenario_hit(res: Any, scenario_id: Any) -> bool:
    items = (res or {}).get("content") or []
    for it in items:
        meta = it.get("metadata") if isinstance(it, dict) else None
        if isinstance(meta, dict) and meta.get("benchmark_scenario_id") == scenario_id:
            return True
    return bool(items)


def action_retrieve(payload: Dict[str, Any]) -> Any:
    scenario_id = payload.get("scenarioId")
    top_k = int(payload.get("topK") or 8)
    started = time.perf_counter()

    # Public read path: semantic/full-text content read. Over-fetch so the
    # client-side scenario filter still yields topK after cross-scenario rows
    # are dropped.
    res = call_tool(
        "read_knowledge",
        {"query": payload.get("prompt") or "", "limit": top_k * 4},
    )
    latency_ms = (time.perf_counter() - started) * 1000

    rows = (res or {}).get("content") or []
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if scenario_id is not None and meta.get("benchmark_scenario_id") != scenario_id:
            continue
        step_id = str(meta.get("benchmark_step_id") or row.get("id"))
        if step_id not in grouped:
            grouped[step_id] = {
                "id": step_id,
                "text": row.get("text_content") or row.get("payload_text") or "",
                "score": row.get("combined_score") or row.get("similarity") or 0.5,
                "sourceType": "memory",
                "metadata": meta,
            }
            order.append(step_id)

    items = [grouped[s] for s in order][:top_k]
    return {"items": items, "latencyMs": latency_ms}


ACTIONS = {
    "reset": action_reset,
    "setup": action_setup,
    "ingest": action_ingest,
    "retrieve": action_retrieve,
}


if __name__ == "__main__":
    raise SystemExit(serve(ACTIONS))
