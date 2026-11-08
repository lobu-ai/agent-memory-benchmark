#!/usr/bin/env python3
"""Transparent local controls: FTS5 BM25, full context, or no memory.

This is a reference baseline, not an implementation of any competing product.
All controls receive the same source steps; none receives evaluation labels.
"""
import os
import json
import re
import sqlite3
import time

from _bench_protocol import serve

DB = sqlite3.connect(':memory:')
MODE = os.environ.get('REFERENCE_MODE', 'fts')
if MODE not in {'fts', 'full', 'empty'}:
    raise ValueError('REFERENCE_MODE must be fts, full, or empty')


def reset(_payload):
    DB.execute('DROP TABLE IF EXISTS memories')
    DB.execute('CREATE VIRTUAL TABLE memories USING fts5(id UNINDEXED, text, metadata UNINDEXED)')


def ingest(payload):
    for step in payload['scenario']['steps']:
        raw_date = str((step.get('metadata') or {}).get('session_date') or '')
        match = re.search(r'(\d{4})[/-](\d{2})[/-](\d{2}).*?(\d{2}):(\d{2})', raw_date)
        date = f'{match[1]}-{match[2]}-{match[3]}T{match[4]}:{match[5]}' if match else ''
        DB.execute('INSERT INTO memories(id,text,metadata) VALUES (?,?,?)', (step['id'], step['content'], json.dumps({'session_date': date})))
    DB.commit()


def retrieve(payload):
    started = time.perf_counter()
    rows = []
    limit = int(payload['topK'])
    if MODE == 'full':
        rows = DB.execute('SELECT id,text,metadata FROM memories ORDER BY rowid LIMIT ?', (limit,)).fetchall()
    elif MODE == 'fts':
        tokens = list(dict.fromkeys(re.findall(r'\w+', payload['prompt'].lower())))
        query = ' OR '.join('"' + token + '"' for token in tokens)
        if query:
            rows = DB.execute('SELECT id,text,metadata FROM memories WHERE memories MATCH ? ORDER BY rank LIMIT ?', (query, limit)).fetchall()
    return {'items': [{'id': key, 'text': text, 'sourceType': 'memory', 'metadata': json.loads(metadata)} for key, text, metadata in rows],
            'latencyMs': (time.perf_counter() - started) * 1000}


if __name__ == '__main__':
    raise SystemExit(serve({'reset': reset, 'setup': lambda _: None, 'ingest': ingest,
                          'retrieve': retrieve, 'version': lambda _: f'SQLite {sqlite3.sqlite_version}; {MODE}'}))
