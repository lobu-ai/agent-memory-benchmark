"""Generate 24 public development queries: four structural pairs, three checkpoints.

These are directed-graph maintenance cases, not the planned temporal/authz/action
suite or a held-out evaluation. Renaming never increases the scenario count.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random

from reference import path_oracle, relational_oracle, make_frontier_certificate, verify_exact_set

FAMILIES = ["parallel_branches", "shared_choke", "multiple_targets", "cycle_exit"]


def generate(seed=2026110206):
    episodes = []
    for family_index, family in enumerate(FAMILIES):
        for reversed_edge in [False, True]:
            rng = random.Random(seed+family_index)
            aliases = ["invoice", "issuer", "a", "b", "c", "d", "merge", "risk", "risk2", "cycle1", "cycle2", "namesake"]+["distractor"+str(i) for i in range(50)]
            refs = {name: "n"+format(x, "08x") for name, x in zip(aliases, rng.sample(range(2**31), len(aliases)))}
            facts = []
            def fact(s, p, o):
                ident = "s"+format(rng.getrandbits(48), "012x")
                facts.append({"id": ident, "subject": refs[s], "predicate": p, "object": refs.get(o, o)})
                return ident
            def edge(a, b):
                return fact(a, "org:subOrganizationOf", b)
            def alternate(a, b):
                return edge(b, a) if reversed_edge else edge(a, b)
            fact("invoice", "bench:issuer", "issuer")
            if family == "parallel_branches":
                edge("issuer", "a");edge("a", "b")
                first = edge("b", "risk")
                edge("issuer", "c");edge("c", "d")
                second = alternate("d", "risk")
            elif family == "shared_choke":
                edge("issuer", "a")
                first = edge("a", "merge")
                edge("issuer", "b");alternate("b", "merge")
                second = edge("merge", "risk")
            elif family == "multiple_targets":
                edge("issuer", "a");first = alternate("a", "risk")
                edge("issuer", "b");edge("b", "c")
                second = edge("c", "risk2")
                fact("risk2", "bench:risk", "open")
            else:
                edge("issuer", "a");first = edge("a", "risk")
                edge("issuer", "cycle1");edge("cycle1", "cycle2");edge("cycle2", "cycle1")
                second = alternate("cycle2", "risk")
            if family != "cycle_exit":
                edge("issuer", "cycle1");edge("cycle1", "cycle2");edge("cycle2", "issuer")
            fact("risk", "bench:risk", "open")
            fact("namesake", "bench:risk", "open")
            for i in range(50):
                edge("distractor"+str(i), "distractor"+str((i+1)%50))
                if i%7 == 0:
                    fact("distractor"+str(i), "bench:risk", "open")
            rng.shuffle(facts)
            checkpoints = []
            for i, withdrawn in enumerate([[], [first], [first, second]]):
                checkpoints.append({"id": "q"+str(i), "withdraw": sorted(withdrawn), "snapshot_id": "snapshot-"+str(i),
                    "required_watermark": i, "principal": "fixture-reader", "authorization_scope": "bounded-world",
                    "source_id": "fixture-source", "source_health": "healthy", "valid_at": i, "observation_cutoff": i})
            episodes.append({"id": "graph-dev-"+str(len(episodes)+1).zfill(2), "pair_id": "pair-"+str(family_index+1),
                "development_family": family,
                "task": "Return all current open-risk organizations reachable from the invoice issuer via zero or more org:subOrganizationOf links. Include sufficient assertion IDs.",
                "invoice": refs["invoice"], "facts": facts,
                "names": [{"id": refs["risk"], "label": "Synthetic Meridian"}, {"id": refs["namesake"], "label": "Synthetic Meridian"}],
                "schema": {"org:subOrganizationOf": "directed asserted containment", "bench:risk": "custom benchmark risk label"},
                "coverage": {"status": "complete", "predicates": ["bench:issuer", "org:subOrganizationOf", "bench:risk"], "scope": "all entities in this bounded world", "version": "v1"},
                "inferenceProfile": "Directed reachability over asserted org:subOrganizationOf only; no inverse, subproperty or other entailment inputs are enabled in this seed.",
                "checkpoints": checkpoints})
    return {"status": "Generated development set; no product/model run", "seed": seed,
            "base_episodes": len(episodes), "matched_pairs": len(FAMILIES), "checkpoint_queries": 3*len(episodes),
            "boundary": "Controller owns future schedules. Executor receives current checkpoint and public tool reads only. This is not a held-out set.",
            "episodes": episodes}


def verify(suite):
    rows = []
    for world in suite["episodes"]:
        for cp in world["checkpoints"]:
            enumerated = sorted(path_oracle(world, set(cp["withdraw"])))
            recursive = relational_oracle(world, set(cp["withdraw"]))
            if enumerated != recursive:
                raise AssertionError("Independent reference disagreement")
            certificate = make_frontier_certificate(world, cp)
            if not verify_exact_set(world, cp, recursive, certificate):
                raise AssertionError("Reference certificate rejected")
            rows.append({"episode": world["id"], "family": world["development_family"], "checkpoint": cp["id"],
                         "expected": recursive, "references_agree": True})
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=2026110206)
    args = p.parse_args()
    suite = generate(args.seed)
    checks = verify(suite)
    data = (json.dumps(suite, indent=2)+"\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    receipt = {"fixture_sha256": hashlib.sha256(data).hexdigest(), "generated_queries": len(checks),
               "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "reference_checks_passed": len(checks), "product_runs": 0, "model_calls": 0, "rows": checks}
    args.output.with_suffix(".reference.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps({k:v for k,v in receipt.items() if k != "rows"}))


if __name__ == "__main__":
    main()
