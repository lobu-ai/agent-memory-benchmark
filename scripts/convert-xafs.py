#!/usr/bin/env python3
"""Convert the supermemory/xAFS HF dataset into a MemoryBench suite.

xAFS layout: dp_NNN/data/**/<file> (the filesystem the agent searches) +
dp_NNN/question.json (a list of {id, family, prompt, gold_file_ids, gold_answer}).

We map: datapoint -> scenario, each data file -> a step (id = path relative to
the datapoint, so it matches gold_file_ids verbatim), each question -> a suite
question (gold_file_ids -> expectedSourceStepIds, gold_answer -> expectedAnswer).

Usage: python3 scripts/convert-xafs.py <max_files_per_dp> <out_suite.json> [dp_001 dp_002 ...]
Picks the datapoints whose file count <= max_files_per_dp (keeps the suite runnable).
"""
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://huggingface.co/api/datasets/supermemory/xAFS"
RAW = "https://huggingface.co/datasets/supermemory/xAFS/resolve/main/"

max_files = int(sys.argv[1]) if len(sys.argv) > 1 else 60
out_path = sys.argv[2] if len(sys.argv) > 2 else "suites/xafs.json"
only_dps = set(sys.argv[3:]) if len(sys.argv) > 3 else None


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


siblings = [s["rfilename"] for s in json.loads(fetch(API))["siblings"]]
by_dp: dict[str, list[str]] = {}
for f in siblings:
    m = re.match(r"(dp_\d+)/", f)
    if m:
        by_dp.setdefault(m.group(1), []).append(f)

scenarios = []
for dp in sorted(by_dp):
    if only_dps and dp not in only_dps:
        continue
    files = by_dp[dp]
    data_files = [f for f in files if f.startswith(f"{dp}/data/")]
    if not only_dps and len(data_files) > max_files:
        print(f"  skip {dp}: {len(data_files)} files > {max_files}")
        continue
    qjson_path = f"{dp}/question.json"
    if qjson_path not in files:
        continue

    print(f"  {dp}: {len(data_files)} files")
    # Download file contents in parallel.
    def load(f: str) -> tuple[str, str]:
        rel = f[len(dp) + 1 :]  # strip "dp_NNN/" -> "data/..."
        try:
            return rel, fetch(RAW + f).decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return rel, f"[unreadable: {e}]"

    with ThreadPoolExecutor(max_workers=8) as ex:
        contents = list(ex.map(load, data_files))

    steps = [
        {"id": rel, "content": content, "metadata": {"path": rel}}
        for rel, content in contents
    ]

    questions_raw = json.loads(fetch(RAW + qjson_path))
    questions = [
        {
            "id": q["id"],
            "category": q.get("family", "xafs"),
            "prompt": q["prompt"],
            "expectedSourceStepIds": q.get("gold_file_ids", []),
            "expectedAnswers": [q.get("gold_answer", "")] or ["(unspecified)"],
            "tags": ["xafs", f"family:{q.get('family','xafs')}"],
        }
        for q in questions_raw
    ]
    scenarios.append(
        {"id": f"xafs-{dp}", "category": "agentic-filesystem", "steps": steps, "questions": questions}
    )

suite = {
    "id": f"xafs-{len(scenarios)}",
    "version": "2026.06.16",
    "description": f"supermemory/xAFS agentic-retrieval benchmark — {len(scenarios)} datapoints "
    f"(<= {max_files} files each), filesystem-shaped multi-hop/temporal QA.",
    "scenarios": scenarios,
}
with open(out_path, "w") as fh:
    json.dump(suite, fh, indent=2)

nq = sum(len(s["questions"]) for s in scenarios)
nsteps = sum(len(s["steps"]) for s in scenarios)
print(f"\nwrote {out_path}: {len(scenarios)} scenarios, {nsteps} steps, {nq} questions")
