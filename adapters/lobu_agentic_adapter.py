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
def gemini(prompt: str, *, temperature: float = 0.0, timeout: int = 90) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{JUDGE_MODEL}:generateContent?key={GEMINI_KEY}")
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(5):
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
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # read/connect timeout — the longer comprehensive prompt occasionally
            # runs past the socket timeout; back off and retry rather than fail
            # the whole ingest (the runner treats one ingest error as fatal).
            last = exc
            time.sleep(2 * (attempt + 1))
            continue
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
EXTRACT_PROMPT = """Extract a COMPLETE, FAITHFUL set of facts from this chat session \
so they can answer later questions WITHOUT the raw text. Capture EVERY salient detail \
the user states or that is established about them or the people/things they mention, and \
PRESERVE ALL SPECIFICS VERBATIM: numbers, counts, quantities, amounts, money, dates, times, \
durations, names, brands, models, places, scales, schedules, preferences, statuses, \
decisions, events, ownership. When the user has MULTIPLE of something (a list or collection \
— kits owned, trips taken, doctors seen, festivals attended), emit ONE fact PER ITEM; never \
collapse a collection into a single count or summary. Do not omit detail; do not invent. \
Ignore only pure pleasantries and the assistant's generic boilerplate advice.

Return a JSON array. Each item: {"subject": "...", "attribute": "...", "value": "...", \
"statement": "one self-contained sentence stating the fact WITH its specifics"}.
Make subject+attribute canonical so a genuine later UPDATE to the SAME single thing reuses \
the same subject+attribute (e.g. subject "user", attribute "home location"); for accumulating \
items give each a distinct value so they stay separate. Empty array only if there is truly nothing.

SESSION:
{session}"""

JUDGE_PROMPT = """A NEW fact was just stated. Decide if it REPLACES a prior fact — i.e. the \
prior fact's value is NO LONGER TRUE because this one changed it.

CRITICAL RULE: supersede ONLY a genuine REPLACEMENT. If the new fact and a prior fact can both \
be TRUE AT THE SAME TIME, that is ACCUMULATION, not replacement — do NOT supersede.
- "lives in Chicago" then "lives in NYC" -> REPLACES (can't live in both) -> supersede.
- "owns a Spitfire kit" then "owns a Tiger tank kit" -> ADDS (owns both) -> do NOT supersede.
- "attended festival A" then "attended festival B" -> ADDS -> do NOT supersede.
- "personal best 25:50" then "personal best 24:30" -> REPLACES -> supersede.

NEW FACT: {new}

PRIOR STORED FACTS (id :: statement):
{candidates}

Return JSON: {"supersedes_id": <id of the single prior fact this REPLACES, or null>}."""


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


BATCH_JUDGE_PROMPT = """You are deduplicating a memory store. Below are NEW facts (each
with an index) and PRIOR stored facts (each with an id). For each NEW fact decide which
single PRIOR fact it REPLACES — i.e. the prior fact's value is NO LONGER TRUE because the
new one changed the SAME single thing (a move, a corrected number, a changed status/
preference).

CRITICAL: only mark a genuine REPLACEMENT. If a new fact and a prior fact can both be TRUE
AT THE SAME TIME, that is ACCUMULATION — leave supersedes_id null. Owning two kits,
attending two festivals, visiting two doctors = accumulation, NOT replacement.

NEW FACTS (index :: statement):
{new}

PRIOR STORED FACTS (id :: statement):
{prior}

Return a JSON array, one object per NEW fact: [{"i": <index>, "supersedes_id": <prior id it replaces, or null>}, ...]."""


def judge_supersede_batch(facts: List[Dict[str, str]],
                          registry: List[Dict[str, Any]]) -> Dict[int, int]:
    """One judge call for a whole session's new facts vs the prior registry (vs a
    call per fact). Returns {new_fact_index: superseded_prior_id}."""
    if not facts or not registry:
        return {}
    new_txt = "\n".join(f"[{i}] {f['statement']}" for i, f in enumerate(facts))
    prior_txt = "\n".join(f"{r['id']} :: {(r.get('text') or '')[:140]}" for r in registry)
    valid_ids = {r["id"] for r in registry}
    res = _parse_json(
        gemini(BATCH_JUDGE_PROMPT.replace("{new}", new_txt).replace("{prior}", prior_txt)), [])
    out: Dict[int, int] = {}
    if isinstance(res, list):
        for item in res:
            if not isinstance(item, dict):
                continue
            i, sid = item.get("i"), item.get("supersedes_id")
            try:
                i, sid = int(i), int(sid)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(facts) and sid in valid_ids:
                out[i] = sid
    return out


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

        try:
            session_facts = extract_facts(content)
        except Exception as exc:
            # A single oversized session can exhaust the extractor's retries/timeout.
            # Don't fail the whole run: in hybrid mode the raw session is already
            # stored above, so focused retrieval falls back to it gracefully.
            sys.stderr.write(f"[ingest] extraction skipped for one session: {exc}\n")
            over_budget += 1
            continue
        # One BATCH judge call per session (vs per fact) decides which new facts
        # REPLACE a prior fact. LOBU_NO_SUPERSEDE=1 skips it entirely. The judge
        # marks only genuine replacements, never accumulation, so collections
        # ("owns kit A", "owns kit B") stay distinct and aggregation/counting works.
        supersede_map: Dict[int, int] = {}
        if os.environ.get("LOBU_NO_SUPERSEDE") != "1" and saved and session_facts:
            judged += 1
            supersede_map = judge_supersede_batch(session_facts, saved)
        # A prior may be superseded by at most ONE new fact (Lobu's UNIQUE
        # supersede-fork guard rejects a second supersede of the same event).
        # If the batch judge maps two facts to the same prior, only the first
        # supersedes; the rest are plain adds.
        consumed_priors: set = set()
        for i, fact in enumerate(session_facts):
            prior_id = supersede_map.get(i)
            if prior_id is not None and prior_id in consumed_priors:
                prior_id = None
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
                args["metadata"]["resolved_update"] = True
                superseded += 1
                consumed_priors.add(prior_id)
                saved = [s for s in saved if s["id"] != prior_id]
            res = call_tool("save_memory", args)
            new_id = (res or {}).get("id")
            facts_saved += 1
            if new_id is not None:
                saved.append({"id": int(new_id), "subject": fact["subject"],
                              "attribute": fact["attribute"], "text": fact["statement"]})

    # SQL retrieval reads rows directly (no embeddings); any vector path must wait
    # for the async embedder to make this run's SESSIONS searchable before reading.
    _rmode = os.environ.get("LOBU_RETRIEVE", "sql").lower()
    if "vector" in _rmode or "focused" in _rmode or "augmented" in _rmode:
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


def _retrieve_focused(prompt: str, scenario_id: Any, run_id: str, top_k: int) -> List[Dict[str, Any]]:
    """SM-style focused retrieval: vector-RANK the whole sessions for recall (their
    embeddings are strong), but hand the reader the session's EXTRACTED FACT-
    SENTENCES instead of the 10-25k-char raw session. query_sql masks superseded
    facts so the current value leads. Falls back to raw session text when a
    matched session has no extracted facts (graceful degradation)."""
    overfetch = int(os.environ.get("LOBU_OVERFETCH", "400"))
    res = call_tool("read_knowledge", {"query": prompt, "limit": overfetch})
    matched_steps: List[str] = []
    seen: set = set()
    for row in (res or {}).get("content") or []:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if meta.get("benchmark_run_id") != run_id:
            continue
        if scenario_id is not None and meta.get("benchmark_scenario_id") != scenario_id:
            continue
        if meta.get("layer") != "session":  # rank by session relevance (recall)
            continue
        step = str(meta.get("benchmark_step_id") or row.get("id"))
        if step in seen:
            continue
        seen.add(step)
        matched_steps.append(step)
        if len(matched_steps) >= top_k:
            break

    def _by_step(layer: str) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in _query_layer(scenario_id, run_id, layer, limit=overfetch):
            m = r.get("metadata") or {}
            if isinstance(m, str):
                try:
                    m = json.loads(m)
                except Exception:
                    m = {}
            out.setdefault(str(m.get("benchmark_step_id") or ""), []).append(r)
        return out

    facts_by_step = _by_step("fact")
    sessions_by_step = _by_step("session")
    items: List[Dict[str, Any]] = []
    for step in matched_steps:
        facts = facts_by_step.get(step) or []
        if facts:
            text = "\n".join((r.get("payload_text") or "") for r in facts)
        else:  # no extracted facts -> serve the raw session (graceful fallback)
            srows = sessions_by_step.get(step) or []
            text = (srows[0].get("payload_text") if srows else "") or ""
        items.append({"id": step, "text": text, "score": 0.7,
                      "sourceType": "memory", "metadata": {"benchmark_step_id": step}})
    return items


def _retrieve_augmented(prompt: str, scenario_id: Any, run_id: str, top_k: int) -> List[Dict[str, Any]]:
    """LOSSLESS whole sessions (vector-ranked, like the 80% baseline) PLUS one
    compact deduplicated fact-list item as a preamble. The whole sessions preserve
    everything the answerer needs for every category (no regression on the working
    ones); the fact list is purely a COUNTING/LOOKUP aid that fixes the failure the
    baseline data exposed — multi-session 'how many X' questions where recall=1.0
    but the answerer miscounts distinct items scattered across verbose prose.
    query_sql masks superseded facts, so the list carries current values only."""
    cap = int(os.environ.get("LOBU_PREAMBLE_CAP", "16"))
    fact_rows = _query_layer(scenario_id, run_id, "fact", limit=300)
    ptok = set(re.findall(r"[a-z0-9]+", prompt.lower()))
    fact_rows.sort(
        key=lambda r: (len(ptok & set(re.findall(r"[a-z0-9]+", (r.get("payload_text") or "").lower()))),
                       r.get("occurred_at") or ""),
        reverse=True,
    )
    bullets: List[str] = []
    seen_f: set = set()
    for r in fact_rows:
        t = (r.get("payload_text") or "").strip()
        if not t or t[:120] in seen_f:
            continue
        seen_f.add(t[:120])
        bullets.append("- " + t)
        if len(bullets) >= cap:
            break

    # Vector recall over whole sessions (relevance-ranked, lossless) — same engine
    # the 80% baseline uses. Over-fetch wide (like the baseline's 400): in hybrid
    # store the many small FACT events otherwise crowd whole sessions out of a
    # narrow top-N, starving session recall. We filter to the session layer below.
    overfetch = int(os.environ.get("LOBU_OVERFETCH", "400"))
    res = call_tool("read_knowledge", {"query": prompt, "limit": max(overfetch, top_k * 5)})
    sessions: List[Dict[str, Any]] = []
    seen: set = set()
    for row in (res or {}).get("content") or []:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if meta.get("benchmark_run_id") != run_id:
            continue
        if scenario_id is not None and meta.get("benchmark_scenario_id") != scenario_id:
            continue
        if meta.get("layer") != "session":
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

    # Keep the FULL top_k whole sessions (no recall cost) and ride the fact digest
    # along on the most-relevant session's text, rather than spending a slot on a
    # separate digest item (which drops one gold session out of the top_k budget).
    sessions = sessions[:top_k]
    if bullets and sessions:
        digest = "Recorded facts across all sessions (current values, deduplicated):\n" + "\n".join(bullets)
        sessions[0] = {**sessions[0], "text": digest + "\n\n---\n\n" + (sessions[0].get("text") or "")}
    return sessions


def action_retrieve(payload: Dict[str, Any]) -> Any:
    scenario_id = payload.get("scenarioId")
    run_id = scenario_tag(payload)
    top_k = int(payload.get("topK") or 8)
    prompt = payload.get("prompt") or ""
    started = time.perf_counter()
    if RETRIEVE_MODE == "augmented":
        items = _retrieve_augmented(prompt, scenario_id, run_id, top_k)
    elif RETRIEVE_MODE == "focused":
        items = _retrieve_focused(prompt, scenario_id, run_id, top_k)
    elif RETRIEVE_MODE == "hybrid-vector":
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
