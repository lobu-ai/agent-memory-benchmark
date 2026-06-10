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
import re
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

# Optional query-rewrite for retrieval recall (LOBU_QUERY_REWRITE=1). Uses an
# LLM only to turn a CONVERSATIONAL question into focused search queries — a
# standard, generic RAG step (no test annotations, no per-question tuning).
ZAI_KEY = os.environ.get("Z_AI_API_KEY")
ZAI_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4").rstrip("/") + "/chat/completions"
ZAI_MODEL = os.environ.get("ZAI_REWRITE_MODEL", "glm-4.6")


def _zai_rewrite_queries(prompt: str) -> List[str]:
    """Rewrite a conversational question into up to 4 focused keyword search
    queries (strip filler like 'I think we discussed / can you remind me';
    cover synonyms e.g. doctor/physician/specialist). Returns [] on any failure
    so the caller falls back to the raw prompt — purely additive."""
    if not ZAI_KEY:
        return []
    instr = (
        "Rewrite the user's question into 3 short keyword search queries that retrieve "
        "the relevant past conversation sessions from a memory store. Strip conversational "
        "filler. Include synonym variants (doctor/physician/specialist; job/role/position). "
        'Return STRICT JSON {"queries":["...","...","..."]} only.\n\nQUESTION: ' + prompt
    )
    # thinking disabled (same as the shared answerer's z.ai calls): a reasoning
    # model burns 5-25s "thinking" about a trivial rewrite, which stacked the
    # whole retrieve action past the adapter's socket timeout.
    body = json.dumps({"model": ZAI_MODEL, "temperature": 0,
                       "thinking": {"type": "disabled"},
                       "messages": [{"role": "user", "content": instr}]}).encode()
    req = urllib.request.Request(
        ZAI_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {ZAI_KEY}"})
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                doc = json.loads(resp.read().decode())
            txt = (((doc.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if txt.startswith("```"):
                txt = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", txt).strip()
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                qs = json.loads(m.group(0)).get("queries") or []
                return [q.strip() for q in qs if isinstance(q, str) and q.strip()][:4]
            return []
        except Exception:
            time.sleep(1.5)
    return []


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
    prompt = payload.get("prompt") or ""
    overfetch = int(os.environ.get("LOBU_OVERFETCH", "400"))

    # Query set. The raw prompt is ALWAYS queried first so its ranking is
    # preserved; with LOBU_QUERY_REWRITE, focused rewrites are appended so a
    # recall-miss question (conversational filler that embeds poorly, or a
    # synonym gap like doctor/physician) still surfaces its gold sessions. Dedup
    # by step_id keeps the raw query's hits first → rewrites only ADD sessions
    # the raw query missed (additive, low regression). Over-fetch covers the
    # accumulated cross-scenario pool; the answerer still receives only topK.
    queries = [prompt]
    if os.environ.get("LOBU_QUERY_REWRITE") == "1":
        queries += _zai_rewrite_queries(prompt)

    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for q in queries:
        # Generous socket timeout: an over-fetch read over a grown multi-scenario
        # corpus can exceed the 120s default (one did, killing a whole trial).
        res = call_tool("read_knowledge", {"query": q, "limit": overfetch}, timeout=300)
        for row in (res or {}).get("content") or []:
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if meta.get("benchmark_run_id") != run_id:
                continue
            if scenario_id is not None and meta.get("benchmark_scenario_id") != scenario_id:
                continue
            step_id = str(meta.get("benchmark_step_id") or row.get("id"))
            text = row.get("text_content") or row.get("payload_text") or ""
            if step_id in grouped:
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
    latency_ms = (time.perf_counter() - started) * 1000

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
