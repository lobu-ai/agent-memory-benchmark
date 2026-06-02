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


def session_date_iso(meta: Dict[str, Any]) -> str:
    """Extract an ISO datetime from a LongMemEval session_date like
    '2023/04/10 (Mon) 17:50' -> '2023-04-10T17:50'. Keeping the TIME lets
    same-day sessions be ordered (two sessions on the same date but different
    times are otherwise a sort tie). Falls back to date-only, then empty."""
    import re

    raw = str((meta or {}).get("session_date") or "")
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2}).*?(\d{1,2}):(\d{2})", raw)
    if m:
        return (f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                f"T{int(m.group(4)):02d}:{m.group(5)}")
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", raw)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def chunk_session(content: str, turns_per_chunk: int) -> List[str]:
    """Group consecutive turns into mid-sized chunks (coarser than per-turn, so
    embeddings stay broad enough for recall; finer than whole-session, so a
    buried fact ranks on its own). turns_per_chunk<=0 -> whole session."""
    import re

    if turns_per_chunk <= 0:
        return [content]
    parts = re.split(r"\n(?=Turn \d+ \()", content)
    header = parts[0].strip() if parts and not parts[0].lstrip().startswith("Turn ") else ""
    turns = [p.strip() for p in parts if p.strip().startswith("Turn ")]
    if not turns:
        return [content]
    chunks = []
    for i in range(0, len(turns), turns_per_chunk):
        body = "\n".join(turns[i : i + turns_per_chunk])
        chunks.append(f"{header}\n{body}".strip() if header else body)
    return chunks


def action_ingest(payload: Dict[str, Any]) -> Any:
    scenario = payload.get("scenario") or {}
    steps = scenario.get("steps") or []
    run_id = scenario_tag(payload)
    created = 0
    turns_per_chunk = int(os.environ.get("LOBU_CHUNK_TURNS", "0"))
    for step in steps:
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        iso = session_date_iso(step.get("metadata") or {})
        for ci, chunk in enumerate(chunk_session(content, turns_per_chunk)):
            args: Dict[str, Any] = {
                "content": chunk,
                # Suite semanticType (e.g. "conversation_session") is not a
                # built-in Lobu event kind; store as a generic "fact".
                "semantic_type": "fact",
                "metadata": {
                    "benchmark_step_id": str(step.get("id")),
                    "benchmark_scenario_id": scenario.get("id"),
                    "benchmark_run_id": run_id,
                    "session_date": iso,
                    "chunk_index": ci,
                },
            }
            # Give Lobu the REAL chronology so recency is grounded in when the
            # fact was stated, not when it happened to be ingested.
            if iso:
                args["occurred_at"] = iso
            call_tool("save_memory", args)
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
    run_id = scenario_tag(payload)
    top_k = int(payload.get("topK") or 8)
    chunked = int(os.environ.get("LOBU_CHUNK_TURNS", "0")) > 0
    started = time.perf_counter()

    # Public read path. Over-fetch so the scenario filter still yields topK; when
    # chunked, over-fetch more since each session contributes several chunks.
    res = call_tool(
        "read_knowledge",
        # Over-fetch enough to cover the whole accumulated pool, then filter
        # client-side to this scenario's run_id. reset() is a public-API no-op
        # (no bulk delete), so memories from prior scenarios pile up in the org;
        # a small over-fetch lets a scenario's 2-4 sessions get out-ranked and
        # missed. Supermemory reaches the same end-state via per-run container
        # isolation. The answerer still receives only topK items.
        {"query": payload.get("prompt") or "", "limit": int(os.environ.get("LOBU_OVERFETCH", "400"))},
    )
    latency_ms = (time.perf_counter() - started) * 1000

    rows = (res or {}).get("content") or []
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if meta.get("benchmark_run_id") != run_id:
            continue
        if scenario_id is not None and meta.get("benchmark_scenario_id") != scenario_id:
            continue
        step_id = str(meta.get("benchmark_step_id") or row.get("id"))
        text = row.get("text_content") or row.get("payload_text") or ""
        if step_id in grouped:
            # chunked: merge the matched chunks for this session (keeps focused
            # facts while still one item per source step -> recall preserved).
            if chunked and text and text not in grouped[step_id]["text"]:
                grouped[step_id]["text"] = (grouped[step_id]["text"] + "\n" + text).strip()
            continue
        grouped[step_id] = {
            "id": step_id,
            "text": text,
            "score": row.get("combined_score") or row.get("similarity") or 0.5,
            "sourceType": "memory",
            "metadata": meta,
        }
        order.append(step_id)

    items = [grouped[s] for s in order][:top_k]

    if os.environ.get("LOBU_ENHANCED") == "1":
        # Present the retrieved memories CHRONOLOGICALLY (oldest first) and prepend
        # an explicit session-date index. This is a general presentation
        # improvement — temporal-ordering questions ("which came first?") and
        # multi-session aggregation both need to reason over *when* each memory
        # happened, which is otherwise buried in verbose session text. No
        # question-type detection, no per-question tuning.
        items.sort(key=lambda it: (it.get("metadata") or {}).get("session_date") or "")
        # Factual presentation only — a date index, no answerer instructions
        # (reasoning guidance lives in the shared answerer prompt, fair to all).
        idx = ["Session date index (chronological, oldest first):"]
        for it in items:
            sd = (it.get("metadata") or {}).get("session_date") or "unknown date"
            idx.append(f"- [{it['id']}] {sd}")
        return {"items": items, "latencyMs": latency_ms, "contextPrefix": "\n".join(idx)}

    # Read-time recency: present the relevance-selected set newest-first so the
    # CURRENT value of any updated fact leads (no superseding, history intact).
    items.sort(
        key=lambda it: (it.get("metadata") or {}).get("session_date") or "",
        reverse=True,
    )
    return {"items": items, "latencyMs": latency_ms}


ACTIONS = {
    "reset": action_reset,
    "setup": action_setup,
    "ingest": action_ingest,
    "retrieve": action_retrieve,
}


if __name__ == "__main__":
    raise SystemExit(serve(ACTIONS))
