#!/usr/bin/env python3
"""Agentic Lobu adapter — simulates a *guided* Lobu worker-agent that detects
contradicting facts and supersedes them, instead of the dumb text adapter that
blind-appends sessions and papers over updates with a read-time recency sort.

This is the head-to-head test of the owner's thesis: "we already have the
primitives (append-only events + supersedes_event_id + current_event_records +
occurred_at); the gap is guiding the agent to USE them." Here the adapter is the
guided agent. It uses ONLY the public REST tool proxy (save_memory /
read_knowledge / search_memory) — same fairness invariant as every other system.

Pipeline (ingest):
  for each session, in chronological order:
    1. EXTRACT atomic stateful facts (subject, attribute, value) with an LLM
       (gemini-2.5-flash — kept off the z.ai answerer so they don't contend).
    2. for each fact: RECALL prior facts about the same subject+attribute
       (search_memory, scenario-scoped). GATED: the contradiction JUDGE LLM call
       only fires when recall returns a candidate — most facts have no prior and
       are saved directly (matches the owner's "gate it" decision).
    3. if the judge says the new fact updates a prior one, save_memory with
       supersedes_event_id=<prior id> and occurred_at=<session date>. The old
       fact drops out of current_event_records; history stays via the chain.

Retrieve: read_knowledge reads through current_event_records, so superseded
(stale) values are already invisible — no recency hack. "as of <date>" questions
use include_superseded + until=<date> to derive the value valid at that date
(valid_at = occurred_at, invalid_at = superseder's occurred_at).

LOBU_STORE:
  facts  (default) — store ONLY extracted atomic facts (cleanest supersession;
                     tests whether extraction hurts recall, Mem0-style).
  hybrid           — also store the whole session (recall engine) alongside the
                     superseding fact layer.

Env: LOBU_BASE_URL, LOBU_ORG_SLUG, LOBU_API_TOKEN, GEMINI_API_KEY,
     LOBU_EMBED_WAIT (default 60), LOBU_JUDGE_MODEL (default gemini-2.5-flash),
     LOBU_STORE (facts|hybrid).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bench_protocol import serve  # noqa: E402

BASE_URL = os.environ.get("LOBU_BASE_URL", "http://localhost:8799").rstrip("/")
ORG_SLUG = os.environ.get("LOBU_ORG_SLUG", "local-install")
API_TOKEN = os.environ.get("LOBU_API_TOKEN")
EMBED_WAIT_S = int(os.environ.get("LOBU_EMBED_WAIT", "60"))
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
JUDGE_MODEL = os.environ.get("LOBU_JUDGE_MODEL", "gemini-2.5-flash")
STORE_MODE = os.environ.get("LOBU_STORE", "facts").lower()


def require_env() -> None:
    missing = [k for k, v in (("LOBU_ORG_SLUG", ORG_SLUG), ("LOBU_API_TOKEN", API_TOKEN),
                              ("GEMINI_API_KEY", GEMINI_KEY)) if not v]
    if missing:
        raise RuntimeError(f"Missing required env: {', '.join(missing)}")


# ----------------------------- Lobu REST proxy -----------------------------
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
            "User-Agent": "agent-memory-benchmark-lobu-agentic",
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


# ------------------------------- Gemini LLM --------------------------------
def gemini(prompt: str, *, temperature: float = 0.0, timeout: int = 45) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{JUDGE_MODEL}:generateContent?key={GEMINI_KEY}")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                doc = json.loads(resp.read().decode())
            parts = (((doc.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])
            return parts[0].get("text", "") or ""
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (429, 500, 503):
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"gemini error {exc.code}: {exc.read().decode()[:200]}") from exc
    raise RuntimeError(f"gemini failed after retries: {last}")


def _parse_json(text: str, default):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return default
        return default


# --------------------------- extraction + judging --------------------------
EXTRACT_PROMPT = """You extract STATEFUL FACTS from a chat session — facts about \
the user or people/things they mention that have a value which could later change \
or accumulate (locations, counts, amounts, schedules, preferences, statuses, \
personal records, ownership). Ignore one-off chit-chat and the assistant's generic advice.

Return a JSON array. Each item: {"subject": "...", "attribute": "...", "value": "...", \
"statement": "a single self-contained sentence stating the fact"}.
Keep subject+attribute STABLE and canonical (e.g. subject "user", attribute "number of bikes owned") \
so a later update to the same thing has the same subject+attribute. Empty array if no stateful facts.

SESSION:
{session}"""

JUDGE_PROMPT = """A new fact was just stated. Decide if it UPDATES (supersedes) any \
of the prior stored facts — i.e. it is about the SAME subject and SAME attribute and \
represents the current/changed value (a move, a new count, a new amount, a new schedule, \
a changed preference). A fact that is merely related or about a different attribute does NOT supersede.

NEW FACT: {new}

PRIOR STORED FACTS (id :: statement):
{candidates}

Return JSON: {"supersedes_id": <id of the single prior fact this updates, or null>}."""


MAX_FACTS_PER_SESSION = int(os.environ.get("LOBU_MAX_FACTS", "15"))


def extract_facts(session: str) -> List[Dict[str, str]]:
    out = _parse_json(gemini(EXTRACT_PROMPT.replace("{session}", session[:8000])), [])
    facts = []
    if isinstance(out, list):
        for f in out:
            if isinstance(f, dict) and f.get("statement"):
                facts.append({
                    "subject": str(f.get("subject") or "").strip(),
                    "attribute": str(f.get("attribute") or "").strip(),
                    "value": str(f.get("value") or "").strip(),
                    "statement": str(f.get("statement")).strip(),
                })
    return facts[:MAX_FACTS_PER_SESSION]


def judge_supersede(new_fact: Dict[str, str], candidates: List[Dict[str, Any]]) -> Optional[int]:
    if not candidates:
        return None
    cand_txt = "\n".join(f"{c['id']} :: {c['text'][:160]}" for c in candidates)
    new_txt = f"{new_fact['subject']} | {new_fact['attribute']} | {new_fact['statement']}"
    res = _parse_json(gemini(JUDGE_PROMPT.replace("{new}", new_txt).replace("{candidates}", cand_txt)), {})
    sid = res.get("supersedes_id") if isinstance(res, dict) else None
    if sid is None:
        return None
    try:
        sid = int(sid)
    except Exception:
        return None
    return sid if any(c["id"] == sid for c in candidates) else None


# --------------------------------- protocol --------------------------------
def scenario_tag(payload: Dict[str, Any]) -> str:
    return str(payload.get("runId") or "benchmark-run")


def action_reset(payload: Dict[str, Any]) -> Any:
    return None


def action_setup(payload: Dict[str, Any]) -> Any:
    return None


def session_date_iso(meta: Dict[str, Any]) -> str:
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", str((meta or {}).get("session_date") or ""))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _subject_key(s: str) -> str:
    s = (s or "").lower().strip()
    for pre in ("the ", "a ", "an "):
        if s.startswith(pre):
            s = s[len(pre):]
    return s


def _candidates_for(fact: Dict[str, str], saved: List[Dict[str, Any]], cap: int = 14) -> List[Dict[str, Any]]:
    """Prior in-scenario facts plausibly about the same thing, for the judge.

    Recall from the adapter's own write-log (ids returned by save_memory) rather
    than search_memory: embeddings are async, so a search right after a write
    misses it. A guided agent likewise knows what it just stored this session.
    Narrow to same-subject (token overlap) so the judge sees a focused set.
    """
    nk = _subject_key(fact["subject"])
    ntok = set(re.findall(r"[a-z0-9]+", nk))
    scored = []
    for s in saved:
        sk = _subject_key(s["subject"])
        stok = set(re.findall(r"[a-z0-9]+", sk))
        overlap = len(ntok & stok)
        if sk == nk or overlap >= 1:
            scored.append((overlap, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:cap]]


def action_ingest(payload: Dict[str, Any]) -> Any:
    scenario = payload.get("scenario") or {}
    steps = scenario.get("steps") or []
    run_id = scenario_tag(payload)
    scenario_id = scenario.get("id")
    facts_saved = 0
    superseded = 0
    judged = 0
    over_budget = 0
    saved: List[Dict[str, Any]] = []  # in-scenario write-log: {id, subject, attribute, text}
    # Per-scenario wall-clock budget: if extraction+judging blows past it (e.g. a
    # throttled LLM), stop extracting and rely on stored sessions. In hybrid mode
    # the session is already saved, so this degrades gracefully to dumb recall.
    budget_deadline = time.time() + float(os.environ.get("LOBU_INGEST_BUDGET_S", "180"))

    for step in steps:
        content = step.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        iso = session_date_iso(step.get("metadata") or {})
        base_meta = {
            "benchmark_step_id": str(step.get("id")),
            "benchmark_scenario_id": scenario_id,
            "benchmark_run_id": run_id,
            "session_date": iso,
        }

        if STORE_MODE == "hybrid":
            args = {"content": content, "semantic_type": "fact",
                    "metadata": {**base_meta, "layer": "session"}}
            if iso:
                args["occurred_at"] = iso
            call_tool("save_memory", args)

        if time.time() >= budget_deadline:
            over_budget += 1
            continue  # over budget: session stored (hybrid), skip costly extraction

        for fact in extract_facts(content):
            # GATE: only judge when a plausibly-same prior fact exists this scenario.
            candidates = _candidates_for(fact, saved)
            prior_id = None
            if candidates:
                judged += 1
                prior_id = judge_supersede(fact, candidates)
            args = {
                "content": fact["statement"],
                "semantic_type": "fact",
                "metadata": {**base_meta, "layer": "fact",
                             "subject": fact["subject"], "attribute": fact["attribute"]},
            }
            if iso:
                args["occurred_at"] = iso
            if prior_id is not None:
                args["supersedes_event_id"] = prior_id
                args["metadata"]["resolved_update"] = True  # this fact resolved a conflict
                superseded += 1
                saved = [s for s in saved if s["id"] != prior_id]  # old fact no longer current
            res = call_tool("save_memory", args)
            new_id = (res or {}).get("id")
            facts_saved += 1
            if new_id is not None:
                saved.append({"id": int(new_id), "subject": fact["subject"],
                              "attribute": fact["attribute"], "text": fact["statement"]})

    # SQL retrieval reads rows directly (no embeddings); any vector path must wait
    # for the async embedder to make this run's SESSIONS searchable before reading.
    if "vector" in os.environ.get("LOBU_RETRIEVE", "sql").lower():
        probe = (steps[-1].get("content") or "")[:80] if steps else "memory"
        deadline = time.time() + EMBED_WAIT_S
        while time.time() < deadline:
            res = call_tool("read_knowledge", {"query": probe or "memory", "limit": 8})
            if any((it.get("metadata") or {}).get("benchmark_run_id") == run_id
                   and (it.get("metadata") or {}).get("layer") == "session"
                   for it in (res or {}).get("content") or []):
                break
            time.sleep(2)
    return {"factsSaved": facts_saved, "superseded": superseded, "judged": judged,
            "overBudget": over_budget, "store": STORE_MODE}


def _is_asof(prompt: str) -> Optional[str]:
    m = re.search(r"\bas of\b.*?(\d{4})[/-](\d{1,2})[/-](\d{1,2})", prompt, re.I)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


RETRIEVE_MODE = os.environ.get("LOBU_RETRIEVE", "sql").lower()
_RUN_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _query_layer(scenario_id: Any, run_id: str, layer: str, limit: int = 60) -> List[Dict[str, Any]]:
    """Embedding-free read of CURRENT rows for a layer (query_sql masks
    superseded rows automatically), newest first by occurred_at."""
    if not (_RUN_RE.match(str(run_id)) and _RUN_RE.match(str(scenario_id))):
        return []
    sql = (
        "select id, occurred_at, payload_text, supersedes_event_id, metadata from events "
        f"where metadata->>'benchmark_run_id' = '{run_id}' "
        f"and metadata->>'benchmark_scenario_id' = '{scenario_id}' "
        f"and metadata->>'layer' = '{layer}'"
    )
    res = call_tool("query_sql", {"sql": sql, "sort_by": "occurred_at", "limit": limit})
    rows = (res or {}).get("rows") or []
    rows.sort(key=lambda r: r.get("occurred_at") or "", reverse=True)
    return rows


def _row_to_item(r: Dict[str, Any]) -> Dict[str, Any]:
    meta = r.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    return {
        "id": str(meta.get("benchmark_step_id") or r.get("id")),
        "text": r.get("payload_text") or "",
        "score": 0.6,
        "sourceType": "memory",
        "metadata": meta,
    }


def _retrieve_sql(prompt: str, scenario_id: Any, run_id: str, top_k: int) -> List[Dict[str, Any]]:
    """Facts-only current read (stale values already masked). Isolates the
    supersede mechanism; craters recall categories that need raw conversation."""
    rows = _query_layer(scenario_id, run_id, "fact")
    return [_row_to_item(r) for r in rows[: max(top_k * 3, 20)]]


def _gated_preamble(prompt: str, scenario_id: Any, run_id: str, cap: int = 4) -> List[Dict[str, Any]]:
    """Resolved-current-fact preamble (embedding-free). With LOBU_PREAMBLE_GATED=1
    only facts that actually superseded a prior value are surfaced; ranked by
    keyword overlap with the question so the asked fact's current value leads."""
    fact_rows = _query_layer(scenario_id, run_id, "fact")
    if os.environ.get("LOBU_PREAMBLE_GATED") == "1":
        fact_rows = [r for r in fact_rows if r.get("supersedes_event_id") is not None]
    ptok = set(re.findall(r"[a-z0-9]+", prompt.lower()))
    fact_rows.sort(
        key=lambda r: (len(ptok & set(re.findall(r"[a-z0-9]+", (r.get("payload_text") or "").lower()))),
                       r.get("occurred_at") or ""),
        reverse=True,
    )
    return [_row_to_item(r) for r in fact_rows][:cap]


def _combine(preamble: List[Dict[str, Any]], recall: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for it in preamble + recall:
        key = (it["text"] or "")[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= top_k:
            break
    return out


def _retrieve_hybrid_sql(prompt: str, scenario_id: Any, run_id: str, top_k: int) -> List[Dict[str, Any]]:
    """Resolved-fact preamble + whole-session recall, both embedding-free via
    query_sql (sessions ordered by RECENCY, not relevance). Fast/deterministic
    but the recency ordering handicaps recall categories — see hybrid_vector."""
    preamble = _gated_preamble(prompt, scenario_id, run_id)
    sessions = [_row_to_item(r) for r in _query_layer(scenario_id, run_id, "session")]
    return _combine(preamble, sessions, top_k)


def _retrieve_hybrid_vector(prompt: str, scenario_id: Any, run_id: str, top_k: int) -> List[Dict[str, Any]]:
    """Production-faithful hybrid: resolved-fact preamble (embedding-free) + VECTOR
    recall over whole sessions (relevance-ranked, like the dumb baseline). Isolates
    the supersede overlay's effect on top of real vector recall."""
    preamble = _gated_preamble(prompt, scenario_id, run_id)
    res = call_tool("read_knowledge", {"query": prompt, "limit": top_k * 5})
    sessions: List[Dict[str, Any]] = []
    seen: set = set()
    for row in (res or {}).get("content") or []:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if meta.get("benchmark_run_id") != run_id:
            continue
        if scenario_id is not None and meta.get("benchmark_scenario_id") != scenario_id:
            continue
        if meta.get("layer") != "session":  # vector recall over the session layer only
            continue
        key = str(row.get("id"))
        if key in seen:
            continue
        seen.add(key)
        sessions.append({
            "id": str(meta.get("benchmark_step_id") or row.get("id")),
            "text": row.get("text_content") or row.get("payload_text") or "",
            "score": row.get("combined_score") or row.get("similarity") or 0.5,
            "sourceType": "memory",
            "metadata": meta,
        })
    return _combine(preamble, sessions, top_k)


def _retrieve_vector(prompt: str, scenario_id: Any, run_id: str, top_k: int) -> List[Dict[str, Any]]:
    args: Dict[str, Any] = {"query": prompt, "limit": top_k * 5}
    asof = _is_asof(prompt)
    if asof:
        args["include_superseded"] = True
        args["until"] = asof
    res = call_tool("read_knowledge", args)
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in (res or {}).get("content") or []:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if meta.get("benchmark_run_id") != run_id:
            continue
        if scenario_id is not None and meta.get("benchmark_scenario_id") != scenario_id:
            continue
        key = str(row.get("id"))
        if key in grouped:
            continue
        grouped[key] = {
            "id": str(meta.get("benchmark_step_id") or row.get("id")),
            "text": row.get("text_content") or row.get("payload_text") or "",
            "score": row.get("combined_score") or row.get("similarity") or 0.5,
            "sourceType": "memory",
            "metadata": meta,
        }
        order.append(key)
    return [grouped[k] for k in order][:top_k]


def action_retrieve(payload: Dict[str, Any]) -> Any:
    scenario_id = payload.get("scenarioId")
    run_id = scenario_tag(payload)
    top_k = int(payload.get("topK") or 8)
    prompt = payload.get("prompt") or ""
    started = time.perf_counter()
    if RETRIEVE_MODE == "hybrid-vector":
        items = _retrieve_hybrid_vector(prompt, scenario_id, run_id, top_k)
    elif RETRIEVE_MODE == "vector":
        items = _retrieve_vector(prompt, scenario_id, run_id, top_k)
    elif os.environ.get("LOBU_HYBRID_RECALL") == "1":
        items = _retrieve_hybrid_sql(prompt, scenario_id, run_id, top_k)
    else:
        items = _retrieve_sql(prompt, scenario_id, run_id, top_k)
    latency_ms = (time.perf_counter() - started) * 1000
    return {"items": items, "latencyMs": latency_ms}


ACTIONS = {
    "reset": action_reset,
    "setup": action_setup,
    "ingest": action_ingest,
    "retrieve": action_retrieve,
}

if __name__ == "__main__":
    raise SystemExit(serve(ACTIONS))
