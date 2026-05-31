#!/usr/bin/env python3
from __future__ import annotations

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

BASE_URL = os.environ.get('MEM0_BASE_URL', 'https://api.mem0.ai').rstrip('/')
API_KEY = os.environ.get('MEM0_API_KEY')


def require_api_key() -> str:
    if not API_KEY:
        raise RuntimeError('Missing MEM0_API_KEY')
    return API_KEY


def request_json(method: str, path: str, body: Dict[str, Any] | None = None) -> Any:
    api_key = require_api_key()
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Token {api_key}',
            'User-Agent': 'lobu-mem0-benchmark-adapter',
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode('utf-8', 'ignore')
        raise RuntimeError(f'Mem0 API error {exc.code}: {body_text}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Mem0 request failed: {exc}') from exc


def scope_user_id(payload: Dict[str, Any]) -> str:
    run_id = str(payload.get('runId') or 'benchmark-run')
    suffix = hashlib.sha1(run_id.encode('utf-8')).hexdigest()[:16]
    return f'lobu-bench-{suffix}'


def normalize_metadata(metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    if not metadata:
        return {}
    normalized: Dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            normalized[key] = value
        else:
            normalized[key] = json.dumps(value, sort_keys=True)
    return normalized


def split_content(content: str, max_chars: int = 9500) -> List[str]:
    text = content.strip()
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split('\n\n')
    chunks: List[str] = []
    current = ''
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f'{current}\n\n{paragraph}'.strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ''
        while len(paragraph) > max_chars:
            chunks.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks or [text[:max_chars]]


def action_reset(payload: Dict[str, Any]) -> Any:
    # No-op. Each run uses a unique user_id scope.
    return None


def action_setup(payload: Dict[str, Any]) -> Any:
    # No-op. Mem0 scopes are created lazily on first write.
    return None


def parse_turns(content: str) -> List[Dict[str, str]]:
    """Parse a benchmark session into role-attributed messages.

    Sessions look like `Turn 1 (user [evidence]): ...` / `Turn 2 (assistant): ...`
    with an optional `Session date: ...` header. Mem0 is built to extract facts
    from a CONVERSATION (proper user/assistant turns) — feeding it a single blob
    message degrades extraction badly, so reconstruct the turns here. Falls back
    to one user message if the format doesn't match.
    """
    import re

    msgs: List[Dict[str, str]] = []
    header = None
    for chunk in re.split(r'\n(?=Turn \d+ \()', content):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r'Turn \d+ \((user|assistant)[^)]*\):\s*(.*)', chunk, re.S)
        if m:
            text = m.group(2).strip()
            if text:
                msgs.append({'role': m.group(1), 'content': text})
        elif header is None:
            header = chunk  # e.g. "Session date: ..."
    if header and msgs:
        # Keep the session date so temporal questions stay answerable.
        msgs.insert(0, {'role': 'user', 'content': header})
    return msgs or [{'role': 'user', 'content': content.strip()}]


def action_ingest(payload: Dict[str, Any]) -> Any:
    scenario = payload.get('scenario') or {}
    steps = scenario.get('steps') or []
    created = 0
    user_id = scope_user_id(payload)

    for step in steps:
        content = step.get('content')
        if not isinstance(content, str) or not content.strip():
            continue

        metadata = normalize_metadata(step.get('metadata'))
        metadata.update(
            {
                'benchmark_id': step.get('id'),
                'scenario_id': scenario.get('id'),
                'step_kind': step.get('kind'),
            }
        )
        if step.get('kind') == 'memory':
            metadata['semantic_type'] = step.get('semanticType')

        # infer:true (Mem0's intended mode) so it extracts + embeds facts; fed as
        # role-attributed turns rather than a blob.
        request_json(
            'POST',
            '/v1/memories/',
            {
                'user_id': user_id,
                'messages': parse_turns(content),
                'metadata': metadata,
                'infer': True,
            },
        )
        created += 1

    # Mem0 indexes asynchronously at HIGHLY variable latency. Poll the memory
    # count until it stops growing for a stability window (or timeout), so
    # retrieval never runs against a half-processed scope (which silently zeroed
    # recall before).
    if created:
        deadline = time.time() + int(os.environ.get('MEM0_INGEST_WAIT', '240'))
        last_count = -1
        stable_for = 0
        while time.time() < deadline:
            res = request_json('GET', f'/v1/memories/?user_id={user_id}')
            entries = res if isinstance(res, list) else (res.get('results') or [])
            count = len(entries)
            if count > 0 and count == last_count:
                stable_for += 5
                if stable_for >= 20:
                    break
            else:
                stable_for = 0
            last_count = count
            time.sleep(5)

    return {'created': created}


def action_retrieve(payload: Dict[str, Any]) -> Any:
    started = time.perf_counter()
    result = request_json(
        'POST',
        '/v2/memories/search/',
        {
            'query': payload.get('prompt') or '',
            'filters': {'user_id': scope_user_id(payload)},
            # Ask for enough candidates that the dedupe-to-source-steps below can
            # still fill topK (Mem0 returns extracted facts, many per source).
            'top_k': max(int(payload.get('topK') or 8) * 4, 20),
        },
    )
    latency_ms = (time.perf_counter() - started) * 1000

    entries = result if isinstance(result, list) else result.get('memories') or result.get('results') or []
    grouped: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        metadata = entry.get('metadata') if isinstance(entry.get('metadata'), dict) else {}
        benchmark_id = str(metadata.get('benchmark_id') or entry.get('id'))
        text = entry.get('memory') or entry.get('text') or ''
        existing = grouped.get(benchmark_id)
        if existing is None:
            grouped[benchmark_id] = {
                'id': benchmark_id,
                'text': text,
                'score': entry.get('score'),
                'sourceType': 'memory',
                'metadata': metadata,
            }
            continue
        if text and text not in existing['text']:
            existing['text'] = f"{existing['text']}\n\n{text}".strip()
        existing['score'] = max(existing.get('score') or 0, entry.get('score') or 0)

    items = sorted(grouped.values(), key=lambda item: item.get('score') or 0, reverse=True)
    top_k = int(payload.get('topK') or 8)
    return {'items': items[:top_k], 'latencyMs': latency_ms, 'raw': result}


ACTIONS = {
    'reset': action_reset,
    'setup': action_setup,
    'ingest': action_ingest,
    'retrieve': action_retrieve,
}


if __name__ == '__main__':
    raise SystemExit(serve(ACTIONS))
