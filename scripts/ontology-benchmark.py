#!/usr/bin/env python3
"""Model-free native ontology conformance benchmark; public APIs, loopback only.

Apply fixtures/ontology to a DISPOSABLE local install before running. This is
scripted platform verification, not agent reasoning or a competitive QA score.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = [
    ("required", "schema", "Reject a missing required company identifier"),
    ("enum", "schema", "Reject an invalid jurisdiction enum"),
    ("number", "schema", "Reject a negative invoice amount"),
    ("roundtrip", "records", "Read typed metadata back unchanged"),
    ("identity", "records", "Keep same-name legal entities distinct"),
    ("endpoint", "relationships", "Reject a relationship to the wrong endpoint type"),
    ("direction", "relationships", "Reject a reversed typed relationship"),
    ("one-hop", "graph", "Resolve the invoice's actual counterparty"),
    ("three-hop", "graph", "Trace invoice → subsidiary → controller → risk"),
    ("no-path", "graph", "Do not transfer a namesake's unrelated risk"),
    ("invalid-update", "corrections", "Reject an invalid patch without changing current state"),
    ("propagate", "corrections", "Read a resolved risk through the existing graph"),
    ("unlink", "corrections", "Withdraw an edge and remove the derived path"),
    ("automation-schema", "automation", "Expose the declared entity schema in a real window"),
    ("automation-event-schema", "automation", "Expose event metadata enums in the extraction contract"),
    ("automation-invalid", "automation", "Reject invalid completion without persisting a row"),
    ("automation-event-invalid", "automation", "Reject invalid event metadata without a partial entity write"),
    ("automation-save", "automation", "Complete the same window with valid typed outputs"),
    ("automation-event", "automation", "Persist a declared event with its entity attribution"),
    ("automation-upsert", "automation", "Upsert a second run by the declared entity key"),
]


def loopback_url(value):
    u = urllib.parse.urlsplit(value)
    if (u.scheme != "http" or u.hostname not in {"127.0.0.1", "localhost", "::1"}
            or u.username or u.password or u.query or u.fragment or u.path not in {"", "/"}):
        raise ValueError("Select an explicit disposable HTTP loopback install")
    return value.rstrip("/")


class ApiError(Exception):
    def __init__(self, error):
        super().__init__(error.get("message", "SDK call failed"))
        self.error = error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirects are disabled", headers, fp)


class Client:
    def __init__(self, base):
        self.base = loopback_url(base)
        bootstrap = self.post("/api/local-init", {}, authenticated=False)
        # The loopback bootstrap returns the install operator's own session.
        self.token = bootstrap.get("session_token") or bootstrap.get("device_token")
        self.org = (bootstrap.get("organization") or {}).get("slug")
        if not self.token or not self.org:
            raise RuntimeError("Local initialization did not return credentials and workspace")
        self.calls = 0

    def post(self, path, data, authenticated=True):
        headers = {"Content-Type": "application/json", "X-Lobu-Client": "lobu-cli"}
        if authenticated:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode(), headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise

    def call(self, method, args):
        # Method names are harness-owned constants, never supplied by a model.
        script = ("export default async (_ctx,client)=>{try{const value=await client."
                  + method + "(" + json.dumps(args) + ");return {ok:true,value};}"
                  + "catch(e){return {ok:false,error:{name:e.name,code:e.code,"
                  + "httpStatus:e.httpStatus,message:e.message}};}}")
        self.calls += 1
        envelope = self.post("/api/" + urllib.parse.quote(self.org, safe="") + "/run_sdk",
                             {"script": script, "title": "Native ontology benchmark"})
        if not envelope.get("success"):
            raise ApiError(envelope.get("error") or {"message": "SDK execution incomplete"})
        result = envelope["return_value"]
        if not result.get("ok"):
            raise ApiError(result["error"])
        value = result["value"]
        if isinstance(value, dict) and value.get("status") == "pending_approval":
            raise ApiError({"code": "PENDING_APPROVAL", "message": "Human approval is required"})
        return value

    def reject_write(self, method, args):
        # The SDK isolate does not retain httpStatus on every thrown error.
        # Observe the public REST tool boundary for negative-write receipts;
        # 403, 404, 409, 5xx, network errors and approval receipts cannot pass.
        namespace, action = method.split(".")
        tool = {"entities": "manage_entity", "automations": "manage_automations"}[namespace]
        action = {"completeWindow": "complete_window"}.get(action, action)
        self.calls += 1
        try:
            result = self.post("/api/" + urllib.parse.quote(self.org, safe="") + "/" + tool,
                               {"action": action, **args})
        except urllib.error.HTTPError as exc:
            if exc.code not in {400, 422}:
                raise
            return {"rejected": True, "httpStatus": exc.code}
        if isinstance(result, dict) and result.get("status") == "pending_approval":
            raise ApiError({"code": "PENDING_APPROVAL", "message": "Human approval is required"})
        raise AssertionError("The invalid write was accepted")


def equal(actual, expected):
    if actual != expected:
        raise AssertionError(f"expected {expected!r}; received {actual!r}")
    return actual


def reachable_schemas(root, node, seen=None):
    """Follow local JSON Schema refs; ignore unattached definitions."""
    seen = set() if seen is None else seen
    if isinstance(node, dict):
        yield node
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/") and reference not in seen:
            seen.add(reference)
            target = root
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            yield from reachable_schemas(root, target, seen)
        for key, value in node.items():
            if key not in {"$defs", "definitions"}:
                yield from reachable_schemas(root, value, seen)
    elif isinstance(node, list):
        for value in node:
            yield from reachable_schemas(root, value, seen)


def rejected(client, method, args):
    return client.reject_write(method, args)


def summary(rows):
    counts = collections.Counter(row["status"] for row in rows)
    return {"passed": counts["passed"], "failed": counts["failed"],
            "blocked": counts["blocked"], "total": len(CASES),
            "score": counts["passed"] / len(CASES)}


class Suite:
    def __init__(self, client):
        self.c = client
        self.prefix = "SYN-" + uuid.uuid4().hex[:12]
        self.rows = []

    def create(self, kind, name, metadata):
        result = self.c.call("entities.create", {"entity_type": "bench-" + kind,
                             "name": name, "slug": self.prefix.lower() + "-" + uuid.uuid4().hex[:8],
                             "metadata": metadata})
        return result["entity"]

    def record(self, entity_id):
        return self.c.call("entities.get", {"entity_id": entity_id})["entity"]

    def link(self, source, target, kind):
        return self.c.call("entities.link", {"from_entity_id": source, "to_entity_id": target,
                           "relationship_type_slug": "bench_" + kind, "source": "api"})["relationship"]

    def next_ids(self, source, kind):
        result = self.c.call("entities.listLinks", {"entity_id": source,
                             "direction": "outbound", "relationship_type_slug": "bench_" + kind,
                             "limit": 100})
        if result.get("relationship_failures") or result.get("metadata", {}).get("has_more"):
            raise RuntimeError("Relationship traversal returned partial coverage")
        return sorted(edge["to_entity_id"] for edge in result["relationships"])

    def path(self, source):
        paths = [[source]]
        for kind in ["billed_to", "controlled_by", "has_risk"]:
            paths = [path + [target] for path in paths for target in self.next_ids(path[-1], kind)]
        return paths

    def check(self, case_id, fn, requires=()):
        case = next(c for c in CASES if c[0] == case_id)
        started = time.perf_counter()
        row = {"id": case_id, "category": case[1], "task": case[2]}
        missing = [key for key in requires if not any(r["id"] == key and r["status"] == "passed" for r in self.rows)]
        if missing:
            self.rows.append({**row, "status": "blocked", "error": "Prerequisite did not pass: " + ", ".join(missing)})
            return
        try:
            row.update(status="passed", evidence=fn())
        except Exception as exc:
            blocked = isinstance(exc, ApiError) and exc.error.get("code") == "PENDING_APPROVAL"
            row.update(status="blocked" if blocked else "failed", error=str(exc)[:500])
        row["durationMs"] = round((time.perf_counter() - started) * 1000, 2)
        self.rows.append(row)
        print(row["status"].upper(), case_id, flush=True)

    def prepare(self):
        types = self.c.call("entitySchema.listTypes", {})["entity_types"]
        actual = {t["slug"] for t in types}
        if not {"bench-company", "bench-invoice", "bench-risk"} <= actual:
            raise RuntimeError("Apply fixtures/ontology to this disposable install first")
        company = lambda reg: {"registration": self.prefix + reg, "jurisdiction": "GB"}
        self.a = self.create("company", "Synthetic Meridian", company("-A"))["id"]
        self.b = self.create("company", "Synthetic Meridian", company("-B"))["id"]
        self.parent = self.create("company", "Synthetic Parent", company("-P"))["id"]
        self.invoice_data = {"reference": self.prefix + "-I", "amount": 250, "status": "pending"}
        self.invoice = self.create("invoice", "Synthetic invoice", self.invoice_data)["id"]
        self.risk_data = {"reference": self.prefix + "-R", "severity": "high", "state": "open"}
        self.risk = self.create("risk", "Synthetic unresolved risk", self.risk_data)["id"]
        self.link(self.invoice, self.a, "billed_to")
        self.control = self.link(self.a, self.parent, "controlled_by")["id"]
        self.link(self.parent, self.risk, "has_risk")

    def invalid_patch(self):
        receipt = rejected(self.c, "entities.update", {"entity_id": self.invoice,
                           "metadata": {"status": "INVALID"}})
        equal(self.record(self.invoice)["metadata"], self.invoice_data)
        return receipt

    def resolve(self):
        self.c.call("entities.update", {"entity_id": self.risk, "metadata": {"state": "resolved"}})
        equal(self.path(self.invoice), [[self.invoice, self.a, self.parent, self.risk]])
        return {"state": equal(self.record(self.risk)["metadata"]["state"], "resolved")}

    def unlink(self):
        result = self.c.call("entities.unlink", {"relationship_id": self.control})
        equal(result.get("success"), True)
        return {"paths": equal(self.path(self.invoice), [])}

    def window(self):
        triggered = self.c.call("automations.trigger", {"automation_id": self.automation})
        equal(triggered["execution"]["lane"], "external_client")
        read = self.c.call("knowledge.read", triggered["execution"]["next_action"]["read"]["input"])
        self.run = triggered["run_id"]
        self.token = read["window_token"]
        return read

    def automation(self):
        created = self.c.call("automations.create", {"entity_id": self.invoice,
            "slug": self.prefix.lower(), "name": "Synthetic typed-output check",
            "prompt": "Persist the supplied synthetic invoice and assessment.",
            "sources": [{"name": "none", "query": "SELECT id FROM events WHERE false"}],
            "outputs": {"invoices": {"entity": "bench-invoice", "key": ["reference"]},
                        "assessments": {"event": "bench_assessment"}},
            "execution_config": {"executor": {"kind": "external"}}})
        self.automation = str(created["automation_id"])
        self.output_ref = self.prefix + "-OUTPUT"
        read = self.window()
        self.schema = read["extraction_schema"]
        schemas = list(reachable_schemas(self.schema, self.schema["properties"]["invoices"]))
        fields = {key for schema in schemas for key in schema.get("properties", {})}
        if not {"reference", "amount", "status"} <= fields:
            raise AssertionError("Extraction schema is missing declared invoice fields")
        if not any(schema.get("enum") == ["pending", "held", "released"] for schema in schemas):
            raise AssertionError("Extraction schema is missing the invoice status enum")
        return {"schema": read["extraction_schema"]}

    def completion_args(self, status, decision="hold"):
        return {"automation_id": self.automation,
            "run_id": self.run, "window_token": self.token,
            "extracted_data": {"invoices": [{"reference": self.output_ref, "amount": 250,
                                             "status": status}],
                               "assessments": [{"content": "Synthetic assessment", "metadata": {"decision": decision}}]}}

    def complete(self, status, decision="hold"):
        return self.c.call("automations.completeWindow", self.completion_args(status, decision))

    def event_schema(self):
        schemas = reachable_schemas(self.schema, self.schema["properties"]["assessments"])
        if not any(schema.get("properties", {}).get("decision", {}).get("enum") == ["hold", "release"] for schema in schemas):
            raise AssertionError("Declared event metadata decision enum is absent from the extraction schema")
        return {"eventSchema": self.schema["properties"]["assessments"]}

    def outputs(self):
        result = self.c.call("entities.list", {"entity_type": "bench-invoice", "limit": 2,
                             "filters": [{"field": "reference", "op": "eq", "value": self.output_ref}]})
        rows = result["entities"]
        return [row for row in rows if row["metadata"].get("reference") == self.output_ref]

    def invalid_completion(self, status="INVALID", decision="hold"):
        receipt = rejected(self.c, "automations.completeWindow", self.completion_args(status, decision))
        equal(self.outputs(), [])
        read = self.c.call("knowledge.read", {"entity_id": self.invoice, "limit": 100})
        equal([row for row in read.get("content", []) if row.get("semantic_type") == "bench_assessment"], [])
        return {**receipt, "persistedEntityRows": 0, "persistedAssessmentEvents": 0}

    def save(self):
        self.complete("held")
        rows = self.outputs()
        equal(len(rows), 1)
        self.output_id = rows[0]["id"]
        return {"status": equal(rows[0]["metadata"]["status"], "held"), "rows": 1}

    def event(self):
        read = self.c.call("knowledge.read", {"entity_id": self.invoice, "limit": 100})
        rows = [row for row in read.get("content", []) if row.get("semantic_type") == "bench_assessment"]
        if not rows:
            raise AssertionError("No declared assessment event linked to the input invoice")
        return {"decision": equal(rows[0]["metadata"]["decision"], "hold")}

    def upsert(self):
        self.window()
        self.complete("released", "release")
        rows = self.outputs()
        equal(len(rows), 1)
        equal(rows[0]["id"], self.output_id)
        return {"sameRecord": True, "status": equal(rows[0]["metadata"]["status"], "released")}

    def run_all(self):
        try:
            self.prepare()
        except Exception as exc:
            self.rows = [{"id": key, "category": category, "task": task,
                          "status": "blocked", "error": str(exc)[:500]} for key, category, task in CASES]
            return self.rows
        for key, metadata in [("required", {"jurisdiction": "GB"}),
                              ("enum", {"registration": self.prefix, "jurisdiction": "INVALID"})]:
            self.check(key, lambda m=metadata: rejected(self.c, "entities.create",
                       {"entity_type": "bench-company", "name": "Invalid synthetic company", "metadata": m}))
        self.check("number", lambda: rejected(self.c, "entities.create", {"entity_type": "bench-invoice",
                   "name": "Invalid synthetic invoice", "metadata": {**self.invoice_data, "amount": -1}}))
        self.check("roundtrip", lambda: equal(self.record(self.invoice)["metadata"], self.invoice_data))
        self.check("identity", lambda: equal([self.record(x)["metadata"]["registration"] for x in [self.a, self.b]],
                                              [self.prefix + "-A", self.prefix + "-B"]))
        for key, source, target in [("endpoint", self.invoice, self.risk), ("direction", self.a, self.invoice)]:
            self.check(key, lambda s=source, t=target: rejected(self.c, "entities.link",
                       {"from_entity_id": s, "to_entity_id": t, "relationship_type_slug": "bench_billed_to"}))
        self.check("one-hop", lambda: equal(self.next_ids(self.invoice, "billed_to"), [self.a]))
        self.check("three-hop", lambda: equal(self.path(self.invoice), [[self.invoice, self.a, self.parent, self.risk]]))
        self.check("no-path", lambda: equal(self.next_ids(self.b, "controlled_by"), []))
        for key, fn in [("invalid-update", self.invalid_patch), ("propagate", self.resolve),
                        ("unlink", self.unlink), ("automation-schema", self.automation),
                        ("automation-event-schema", self.event_schema),
                        ("automation-invalid", self.invalid_completion),
                        ("automation-event-invalid", lambda: self.invalid_completion("held", "INVALID")),
                        ("automation-save", self.save),
                        ("automation-event", self.event), ("automation-upsert", self.upsert)]:
            deps = ()
            if key.startswith("automation-") and key != "automation-schema":
                deps = ("automation-schema",)
            if key == "automation-save":
                deps += ("automation-invalid", "automation-event-invalid")
            if key in {"automation-event", "automation-upsert"}:
                deps += ("automation-save",)
            self.check(key, fn, requires=deps)
        return self.rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-ref", required=True, help="Exact version or source commit started by the operator")
    parser.add_argument("--output", default="results/ontology-conformance.json")
    args = parser.parse_args()
    tracked = ["scripts/ontology-benchmark.py", "fixtures/ontology/lobu.config.ts", "fixtures/ontology/package.json"]
    digest = hashlib.sha256(b"".join((ROOT / f).read_bytes() for f in tracked)).hexdigest()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    started = time.perf_counter()
    try:
        client = Client(args.base_url)
        rows = Suite(client).run_all()
        calls = client.calls
    except Exception as exc:
        rows = [{"id": key, "category": category, "task": task, "status": "blocked",
                 "error": str(exc)[:500]} for key, category, task in CASES]
        calls = 0
    report = {"suite": "native-ontology-conformance", "version": "1.0.0", "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "method": "Scripted public-API conformance; no model or judge", "modelTokens": 0,
              "runtimeRef": args.runtime_ref,
              "provenance": {"suiteSha256": digest, "harnessRevision": revision, "harnessDirty": dirty},
              "apiCalls": calls, "durationMs": round((time.perf_counter() - started) * 1000, 2),
              "summary": summary(rows), "cases": rows,
              "limitations": ["No agent reasoning or competitive ranking", "No OWL/FIBO inference or completeness claim",
                              "No connector sync, cross-tenant permissions or approval execution measured",
                              "External Automation windows use scripted outputs; no model or device dispatch"]}
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"]))
    return 0 if report["summary"]["passed"] == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
