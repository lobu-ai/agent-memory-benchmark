"""Offline deterministic evaluation; never imported by the executor/adapters."""
import argparse
import json
from pathlib import Path
from reference import relational_oracle, sufficient_positive_proof, verify_exact_set


def observed_facts(result):
    """Recover assertion content, not arbitrary ID strings, from tool results."""
    found = []
    if isinstance(result, dict):
        if {"id", "subject", "predicate", "object"} <= result.keys():
            found.append({k: result[k] for k in ["id", "subject", "predicate", "object"]})
        if {"assertion_id", "subject", "predicate", "object"} <= result.keys():
            found.append({"id": result["assertion_id"], **{k: result[k] for k in ["subject", "predicate", "object"]}})
        if {"node_ref", "risk", "risk_assertion_id"} <= result.keys():
            found.append({"id": result["risk_assertion_id"], "subject": result["node_ref"], "predicate": "bench:risk", "object": result["risk"]})
        for value in result.values():
            found.extend(observed_facts(value))
    elif isinstance(result, list):
        for value in result:
            found.extend(observed_facts(value))
    return found


def grade_answer(world, checkpoint, final, reads):
    expected = relational_oracle(world, set(checkpoint["withdraw"]))
    answer = final.get("answer")
    proof = final.get("proof")
    cert = final.get("certificate")
    well_formed = (isinstance(answer, list) and all(isinstance(x, str) for x in answer)
                   and isinstance(proof, list) and all(isinstance(x, str) for x in proof) and isinstance(cert, dict))
    if not well_formed:
        return {"answer_correct": False, "proof_grounded": False, "exact_set_certified": False, "error": "Malformed final"}
    active = {f["id"]: f for f in world["facts"] if f["id"] not in checkpoint["withdraw"]}
    observed = {f["id"] for f in reads if isinstance(f.get("id"), str) and active.get(f["id"]) == f}
    issuer = cert.get("issuer_assertions", [])
    if not isinstance(issuer, list) or any(not isinstance(x, str) for x in issuer):
        return {"answer_correct": False, "proof_grounded": False, "exact_set_certified": False, "error": "Malformed issuer citations"}
    cited = set(proof) | set(issuer)
    frontier = cert.get("frontier", {})
    if not isinstance(frontier, dict):
        return {"answer_correct": False, "proof_grounded": False, "exact_set_certified": False, "error": "Malformed frontier"}
    for row in frontier.values():
        if not isinstance(row, dict):
            return {"answer_correct": False, "proof_grounded": False, "exact_set_certified": False, "error": "Malformed frontier row"}
        for key in ["outbound_assertions", "risk_assertions"]:
            values = row.get(key, [])
            if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
                return {"answer_correct": False, "proof_grounded": False, "exact_set_certified": False, "error": "Malformed citation array"}
            cited.update(values)
    answer_correct = len(answer) == len(set(answer)) and sorted(answer) == expected
    certified = cited <= observed and verify_exact_set(world, checkpoint, answer, cert)
    proof_ok = (answer_correct and set(proof) <= observed
                and (all(sufficient_positive_proof(world, set(checkpoint["withdraw"]), node, proof) for node in answer)
                     if answer else certified))
    return {"answer_correct": answer_correct, "proof_grounded": proof_ok,
            "exact_set_certified": certified, "expected": expected,
            "unread_or_stale_citations": sorted(cited-observed)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run", type=Path)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    worlds = {w["id"]: w for w in json.loads((root/"fixtures/graph-agent/worlds.json").read_text())["episodes"]}
    traces = [json.loads(line) for line in (args.run/"trace.jsonl").read_text().splitlines()]
    result = json.loads((args.run/"results.json").read_text())
    for row in result["rows"]:
        if row["status"] != "answered":
            row["grades"] = {"answer_correct": False, "proof_grounded": False, "exact_set_certified": False}
            continue
        world = worlds[row["episode"]]
        cp = next(c for c in world["checkpoints"] if c["id"] == row["checkpoint"])
        reads = []
        for trace in traces:
            if (trace.get("kind") == "tool" and trace["system"] == row["system"] and trace["episode"] == row["episode"]
                    and trace["checkpoint"] <= row["checkpoint"]):
                reads.extend(observed_facts(trace["result"]))
        row["grades"] = grade_answer(world, cp, row["final"], reads)
    result["summary"] = {}
    for system in sorted({r["system"] for r in result["rows"]}):
        rows = [r for r in result["rows"] if r["system"] == system]
        result["summary"][system] = {"denominator": 6, "answered": sum(r["status"] == "answered" for r in rows),
            **{k: sum(r["grades"][k] for r in rows) for k in ["answer_correct", "proof_grounded", "exact_set_certified"]}}
    result["controls"] = {"all_empty_answer_exact": 3, "always_risky_root_answer_exact": 3, "denominator_per_system": 6,
                          "note": "Neither control supplies grounded proof or complete frontier certificates."}
    (args.run/"graded.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
