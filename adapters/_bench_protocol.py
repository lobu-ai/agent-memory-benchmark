"""Long-lived JSONL protocol for benchmark adapters.

Each line on stdin is a request:
    {"id": <int>, "action": "reset|setup|ingest|retrieve", "payload": {...}}

Each line on stdout is the matching response:
    {"id": <int>, "ok": true,  "result": <any>}
    {"id": <int>, "ok": false, "error": "<message>"}

Adapters call serve(actions) where `actions` maps action names to functions
that take the payload dict and return the result (or raise on failure).
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable, Dict


ActionHandler = Callable[[Dict[str, Any]], Any]


def _write(message: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message))
    sys.stdout.write('\n')
    sys.stdout.flush()


def serve(actions: Dict[str, ActionHandler]) -> int:
    """Read newline-delimited JSON requests from stdin and dispatch.

    Returns the process exit code (0 on clean EOF).
    """
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        request_id: Any = None
        try:
            request = json.loads(line)
            request_id = request.get('id')
            action = request.get('action')
            payload = request.get('payload') or {}
        except json.JSONDecodeError as exc:
            _write({'id': request_id, 'ok': False, 'error': f'invalid JSON request: {exc}'})
            continue

        handler = actions.get(action) if isinstance(action, str) else None
        if handler is None and action == 'version':
            # Built-in version reporting: every system reports the version under
            # test so results are comparable over time. The runner records this
            # per system. Sourced from BENCH_SYSTEM_VERSION, which CI populates
            # from the actually-installed package / server / API version.
            import os
            _write({'id': request_id, 'ok': True,
                    'result': os.environ.get('BENCH_SYSTEM_VERSION', 'unknown')})
            continue
        if handler is None:
            _write({'id': request_id, 'ok': False, 'error': f'unsupported action {action!r}'})
            continue

        try:
            result = handler(payload)
            _write({'id': request_id, 'ok': True, 'result': result})
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(traceback.format_exc())
            sys.stderr.flush()
            _write({'id': request_id, 'ok': False, 'error': str(exc)})

    return 0
