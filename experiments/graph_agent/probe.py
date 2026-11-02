"""Live, model-free storage/effect conformance; not an agent reasoning score."""
import argparse
import json
from pathlib import Path
from adapters import Lobu, Postgres, SqlError


def native_facts(adapter):
    page = adapter.invoke("list_nodes", {"limit": 100})
    if page["metadata"]["has_more"]:
        raise AssertionError("This bounded conformance fixture must fit one node page")
    facts = []
    for node in page["nodes"]:
        if "risk_assertion_id" in node:
            facts.append({"id": node["risk_assertion_id"], "subject": node["node_ref"],
                          "predicate": "bench:risk", "object": node["risk"]})
        links = adapter.invoke("list_links", {"entity_id": node["entity_id"], "limit": 100})
        if links["metadata"]["has_more"] or links["relationship_failures"]:
            raise AssertionError("Incomplete native link read")
        facts.extend({"id": link["assertion_id"], **{k: link[k] for k in ["subject", "predicate", "object"]}}
                     for link in links["links"])
    return sorted(facts, key=lambda f: f["id"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    state = json.loads(args.state.read_text())
    root = Path(__file__).resolve().parents[2]
    worlds = json.loads((root/"fixtures/graph-agent/worlds.json").read_text())["episodes"]
    rows = []
    for source in worlds:
        # Distinct synthetic episode ownership, so probes cannot disturb an
        # agent episode or its state. No production workspace is permitted.
        world = {**source, "id": "storage-probe-"+source["id"]}
        adapter = Lobu("http://127.0.0.1:"+str(state["lobu_port"]))
        adapter.load(world)
        withdrawn = set()
        for cp in world["checkpoints"]:
            adapter.withdraw(sorted(set(cp["withdraw"])-withdrawn))
            withdrawn.update(cp["withdraw"])
            expected = sorted([f for f in world["facts"] if f["id"] not in withdrawn], key=lambda f: f["id"])
            actual = native_facts(adapter)
            if actual != expected:
                raise AssertionError("Native persisted facts differ at "+world["id"]+"/"+cp["id"])
            rows.append({"episode": source["id"], "checkpoint": cp["id"], "native_assertions_match": True,
                         "assertion_count": len(actual), "withdrawn": sorted(withdrawn)})
    pg = Postgres(state["pg_port"])
    pg.load(worlds[0])
    rejections = []
    probes = [
        ("wrapped_data_modification", lambda: pg.invoke("query", {"sql": "WITH removed AS (DELETE FROM assertions RETURNING *) SELECT * FROM removed"}), {"0A000", "25006"}),
        ("reader_write_privilege", lambda: pg.sql("DELETE FROM assertions", reader=True), {"42501"}),
        ("read_only_transaction", lambda: pg.sql("BEGIN READ ONLY; DELETE FROM assertions; ROLLBACK", reader=True), {"25006"}),
        ("server_file_privilege", lambda: pg.invoke("query", {"sql": "SELECT pg_read_file('/etc/passwd')"}), {"42501"}),
    ]
    for name, invoke, expected_codes in probes:
        try:
            invoke()
        except SqlError as exc:
            if exc.sqlstate not in expected_codes:
                raise
            rejections.append({"probe": name, "sqlstate": exc.sqlstate})
        else:
            raise AssertionError("Read-only role accepted a prohibited query")
    count = pg.invoke("query", {"sql": "SELECT count(*)::int AS count FROM assertions"})["rows"][0]["count"]
    if count != 72:
        raise AssertionError("Read-only probes changed persisted assertions")
    report = {"native_storage_checkpoints": rows, "postgres_prohibited_queries_rejected": len(rejections),
              "postgres_rejections": rejections,
              "postgres_assertions_after_rejected_writes": count, "model_calls": 0,
              "note": "Scripted storage conformance only; never a reader-model score."}
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
