#!/usr/bin/env python3
"""Freeze a bounded, oracle-subgraph KQA Pro diagnostic (not an official score).

The published programs select the evidence corpus, never the retrieved context.
Programs/choices/answers remain evaluator-side. All adapters receive the same
complete visited records plus deterministic distractors. This intentionally
removes corpus discovery and cannot establish whole-KB retrieval performance.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import random
import sys
import tempfile
import urllib.request

REV = "14d87cd22eb79f702fd4ad5c09240bef126d9dce"
FILES = {
    "val.json": ("https://huggingface.co/datasets/drt/kqa_pro/resolve/main/val.json", "b4aed6ab3d7ad071722064fe3bb02bc028cfbeb15da5f7115d57a1e2d198f3bb"),
    "kb.json": ("https://github.com/data-iitd/symKGQA/raw/refs/heads/main/QUACK/preprocessed_kb/kb.json", "db134178b6aba1e7c480cad03c54847b1deb7ffdd5e45fbbf565c68b5f7172d1"),
    "Program/executor_rule.py": (f"https://raw.githubusercontent.com/shijx12/KQAPro_Baselines/{REV}/Program/executor_rule.py", "eca7bfdd6c4a8648de186e4922e0e4be09c9f64bb8aa1b066e5d542336e54dc6"),
    "utils/value_class.py": (f"https://raw.githubusercontent.com/shijx12/KQAPro_Baselines/{REV}/utils/value_class.py", "83dcc227b5028dcc8835b68cc88ebc23d8a47c2c5b0259a5ce4adfd3451631a6"),
    "LICENSE": (f"https://raw.githubusercontent.com/shijx12/KQAPro_Baselines/{REV}/LICENSE", "5ee474ee6c375296524e35cc43c271b28d5c0cc69cdb73ccabe20957f979cfd6"),
}


def verified_file(cache, name, download=False):
    url, expected = FILES[name]
    target = cache / name
    if not target.exists():
        if not download:
            raise ValueError(f"Missing {name}; pass --download to obtain the pinned public files")
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read(150_000_001)
        if len(data) > 150_000_000:
            raise ValueError("Download exceeds the bounded size")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch for {name}")
        target.write_bytes(data)
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Checksum mismatch for {name}")
    return target


def category(program):
    names = [step["function"] for step in program]
    if len(names) < 6 or "FindAll" in names:
        return None
    if any("Qualifier" in name or name.startswith("QFilter") for name in names):
        return "qualifier"
    if any(name in {"SelectBetween", "SelectAmong", "VerifyNum", "VerifyYear", "VerifyDate"} for name in names):
        return "comparison"
    if names.count("Relate") >= 2:
        return "multi-hop"
    if any(name in {"And", "Or", "Count"} for name in names):
        return "set"
    return None


def normalize_kb(kb):
    # The mirror names class-to-class edges subclassOf; the pinned reference
    # executor calls this field instanceOf for both concepts and individuals.
    for concept in kb["concepts"].values():
        if "instanceOf" in concept and "subclassOf" in concept and concept["instanceOf"] != concept["subclassOf"]:
            raise ValueError("Conflicting concept parent fields")
        if "instanceOf" not in concept:
            concept["instanceOf"] = concept["subclassOf"]
    for entity in kb.get("entities", {}).values():
        for edge in entity.get("relations", []):
            if "predicate" in edge and "relation" in edge and edge["predicate"] != edge["relation"]:
                raise ValueError("Conflicting relation label fields")
            if "predicate" not in edge:
                edge["predicate"] = edge["relation"]
    return kb


def execute(executor, program):
    memory, visited = [], set()
    for step in program:
        # Use the release's explicit dependencies, including branches.
        dependencies = [memory[index] for index in step["dependencies"]]
        value = getattr(executor, step["function"])(dependencies, step["inputs"])
        memory.append(value)
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], list):
            visited.update(item for item in value[0] if isinstance(item, str))
    return str(memory[-1]), visited


def record_text(ref, kb, records=None):
    records = records if records is not None else {**kb["concepts"], **kb["entities"]}
    item = records[ref]
    def label(key):
        return {"id": key, "name": records.get(key, {}).get("name", key)}
    def ancestors(key, seen):
        if key in seen:
            return
        seen.add(key)
        for parent in records.get(key, {}).get("instanceOf", []):
            ancestors(parent, seen)
    types = set()
    for key in item.get("instanceOf", []):
        ancestors(key, types)
    value = {
        "id": ref, "name": item["name"],
        "types": [label(key) for key in sorted(types)],
        "attributes": item.get("attributes", []),
        "relations": [{**edge, "object": label(edge["object"])} for edge in item.get("relations", [])],
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_suite(questions, kb, executor, per_category=6, seed=20261102, distractors=8):
    groups = collections.defaultdict(list)
    for index, question in enumerate(questions):
        key = category(question["program"])
        if key:
            groups[key].append((index, question))
    scenarios, audit, rejected = [], [], collections.Counter()
    records = {**kb["concepts"], **kb["entities"]}
    text_cache = {}
    def text(ref):
        if ref not in text_cache:
            text_cache[ref] = record_text(ref, kb, records)
        return text_cache[ref]
    for key in ["multi-hop", "set", "comparison", "qualifier"]:
        candidates = groups[key]
        random.Random(f"{seed}:{key}").shuffle(candidates)
        selected = 0
        for index, question in candidates:
            if selected == per_category:
                break
            try:
                answer, visited = execute(executor, question["program"])
            except Exception:
                rejected["reference_execution_error"] += 1
                continue
            # No hand-edited answers, aliases or question-specific fallbacks.
            if answer != question["answer"]:
                rejected["reference_answer_mismatch"] += 1
                continue
            if not visited or not visited <= records.keys() or len(visited) > 16:
                rejected["visited_record_bound"] += 1
                continue
            support = sorted(visited)
            # Preserve entire records; skip rather than silently truncate facts.
            if any(len(text(ref)) > 8500 for ref in support) or sum(len(text(ref)) for ref in support) > 35000:
                rejected["context_byte_bound"] += 1
                continue
            candidates2 = sorted(set(records) - visited)
            random.Random(f"{seed}:{index}:distractors").shuffle(candidates2)
            noise = []
            for ref in candidates2:
                if len(text(ref)) <= 3500:
                    noise.append(ref)
                    if len(noise) == distractors:
                        break
            refs = support + noise
            random.Random(f"{seed}:{index}:order").shuffle(refs)
            # Neutral randomized IDs do not reveal support/distractor status.
            source_ids = {ref: f"record-{position + 1}" for position, ref in enumerate(refs)}
            scenario_id = f"kqapro-val-{index}"
            scenarios.append({
                "id": scenario_id, "category": key,
                "description": "Public KQA Pro oracle-subgraph diagnostic; whole visited records and distractors",
                "entities": [],
                "steps": [{"kind": "memory", "id": source_ids[ref], "entityRefs": [],
                           "semanticType": "fact", "content": text(ref)} for ref in refs],
                "questions": [{"id": scenario_id, "prompt": question["question"],
                               "expectedAnswers": [question["answer"]],
                               "expectedSourceStepIds": [source_ids[ref] for ref in support],
                               "tags": ["public", "oracle-subgraph", f"program-steps-{len(question['program'])}"]}],
            })
            audit.append({"questionId": scenario_id, "validationIndex": index, "category": key,
                          "referenceAnswer": answer, "program": question["program"],
                          "visitedRefs": support, "distractorRefs": noise,
                          "stepRefs": {source_ids[ref]: ref for ref in refs}})
            selected += 1
        if selected != per_category:
            raise ValueError(f"Only {selected} bounded reference-verified cases in {key}")
    suite = {"id": "kqapro-oracle-subgraph", "version": "1", "entityTypes": [], "scenarios": scenarios}
    return suite, {"cases": audit, "rejectedCandidates": dict(rejected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=pathlib.Path, default=pathlib.Path("datasets/cache/kqapro"))
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("results/kqapro-suite.json"))
    parser.add_argument("--per-category", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20261102)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.per_category <= 25:
        parser.error("--per-category must be between 1 and 25")
    for name in FILES:
        verified_file(args.cache, name, args.download)
    cache = args.cache.resolve()
    sys.path.insert(0, str(cache))
    spec = importlib.util.spec_from_file_location("kqapro_reference", cache / "Program/executor_rule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    kb = normalize_kb(json.loads((cache / "kb.json").read_text()))
    normalized = json.dumps(kb, sort_keys=True).encode()
    with tempfile.NamedTemporaryFile(suffix=".json") as temporary:
        temporary.write(normalized)
        temporary.flush()
        with contextlib.redirect_stdout(io.StringIO()):
            executor = module.RuleExecutor({}, temporary.name)
    suite, audit = build_suite(json.loads((cache / "val.json").read_text()), kb, executor,
                              args.per_category, args.seed)
    data = (json.dumps(suite, indent=2, ensure_ascii=False) + "\n").encode()
    audit.update({"suiteSha256": hashlib.sha256(data).hexdigest(), "seed": args.seed,
                  "source": "https://github.com/shijx12/KQAPro_Baselines", "referenceRevision": REV,
                  "datasetLicense": "CC-BY-SA-4.0 (upstream dataset declaration; code is MIT)",
                  "normalization": {"concepts": "Mirror subclassOf also exposed as executor instanceOf", "relations": "Mirror relation also exposed as executor predicate; no edges or facts added", "normalizedKbSha256": hashlib.sha256(normalized).hexdigest()},
                  "inputFiles": {name: {"url": value[0], "sha256": value[1]} for name, value in FILES.items()},
                  "limits": {"maxVisitedRecords": 16, "maxRecordChars": 8500,
                             "maxSupportChars": 35000, "distractorRecords": 8},
                  "interpretation": "Oracle-subgraph diagnostic, not the official KQA Pro benchmark. Selection uses gold programs; corpus discovery is not tested. Source recall measures visited-record coverage, not a minimal proof. Memory adapters flatten the graph; native graph operations and OWL inference are not tested."})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    args.output.with_suffix(".audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps({"questions": len(suite["scenarios"]), "categories": collections.Counter(s["category"] for s in suite["scenarios"]), "suiteSha256": audit["suiteSha256"], "referenceVerified": len(audit["cases"]), "rejectedCandidates": audit["rejectedCandidates"]}))


if __name__ == "__main__":
    main()
