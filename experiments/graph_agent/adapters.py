"""Storage adapters only. No task solver, expected answers, or oracle imports."""
import json
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import uuid
import re


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Redirects are disabled")


def loopback(value):
    u = urllib.parse.urlsplit(value)
    if (u.scheme != "http" or u.hostname not in {"localhost", "127.0.0.1", "::1"}
            or u.username or u.password or u.query or u.fragment or u.path not in {"", "/"}):
        raise ValueError("An explicit disposable loopback server is required")
    return value.rstrip("/")


def post(url, body, headers=None, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout) as r:
        return json.load(r)


class Lobu:
    manifest = {
        "list_nodes": {"description": "Page through native entities, including typed risk metadata.",
                       "arguments": {"offset": "integer >= 0", "limit": "integer 1..100"}},
        "list_links": {"description": "Page through all outbound native links for one entity.",
                       "arguments": {"entity_id": "integer", "offset": "integer >= 0", "limit": "integer 1..100"}},
    }

    def __init__(self, base):
        self.base = loopback(base)
        boot = post(self.base + "/api/local-init", {}, {"X-Lobu-Client": "lobu-cli"})
        self.token = boot.get("session_token") or boot.get("device_token")
        self.org = boot["organization"]["slug"]
        if not self.token:
            raise RuntimeError("Missing local session")
        self.ids = {}
        self.links = {}

    def call(self, method, args):
        if method not in {"entities.create", "entities.delete", "entities.link", "entities.unlink", "entities.list", "entities.listLinks"}:
            raise ValueError("Unsupported SDK method")
        script = "export default async (_ctx,client)=>await client." + method + "(" + json.dumps(args) + ");"
        r = post(self.base + "/api/" + urllib.parse.quote(self.org, safe="") + "/run_sdk",
                 {"script": script, "title": "Synthetic native graph smoke"},
                 {"Authorization": "Bearer " + self.token, "X-Lobu-Client": "lobu-cli"})
        if not r.get("success"):
            raise RuntimeError(str(r.get("error")))
        value = r["return_value"]
        if value.get("status") == "pending_approval":
            raise RuntimeError("Pending approval")
        return value

    def load(self, world):
        self.episode = world["id"]
        run_scope = uuid.uuid4().hex[:12]
        # Only this fixture's synthetic rows in the explicit loopback install.
        while True:
            page = self.call("entities.list", {"entity_type": "bench-node", "limit": 100,
                             "filters": [{"field": "episode", "op": "eq", "value": self.episode}]})
            for entity in page["entities"]:
                self.call("entities.delete", {"entity_id": entity["id"]})
            if not page["metadata"]["has_more"]:
                break
        names = {x["id"]: x["label"] for x in world["names"]}
        refs = sorted({f["subject"] for f in world["facts"]} |
                      {f["object"] for f in world["facts"] if f["predicate"] != "bench:risk"})
        for ref in refs:
            meta = {"node_ref": ref, "episode": self.episode}
            for fact in world["facts"]:
                if fact["subject"] == ref and fact["predicate"] == "bench:risk":
                    meta.update(risk=fact["object"], risk_assertion_id=fact["id"])
            result = self.call("entities.create", {"entity_type": "bench-node", "name": names.get(ref, ref),
                               "slug": run_scope + "-" + self.episode + "-" + ref, "metadata": meta})
            self.ids[ref] = result["entity"]["id"]
        self.refs = {v: k for k, v in self.ids.items()}
        for fact in world["facts"]:
            if fact["predicate"] == "bench:risk":
                continue
            value = self.call("entities.link", {"from_entity_id": self.ids[fact["subject"]],
                             "to_entity_id": self.ids[fact["object"]],
                             "relationship_type_slug": "bench_issuer" if fact["predicate"] == "bench:issuer" else "bench_contains",
                             "source": "api", "metadata": {"assertion_id": fact["id"]}})
            self.links[fact["id"]] = value["relationship"]["id"]

    def withdraw(self, ids):
        for assertion in ids:
            self.call("entities.unlink", {"relationship_id": self.links[assertion]})

    def invoke(self, tool, args):
        if tool not in self.manifest or not isinstance(args, dict):
            raise ValueError("Unknown tool")
        allowed = {"offset", "limit"} | ({"entity_id"} if tool == "list_links" else set())
        if set(args) - allowed:
            raise ValueError("Unknown arguments")
        offset, limit = args.get("offset", 0), args.get("limit", 100)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Pagination out of bounds")
        page = {"offset": offset, "limit": limit}
        if tool == "list_nodes":
            r = self.call("entities.list", {"entity_type": "bench-node", **page,
                          "filters": [{"field": "episode", "op": "eq", "value": self.episode}]})
            return {"nodes": [{"entity_id": e["id"], "name": e["name"], **e["metadata"]}
                              for e in r["entities"]], "metadata": r["metadata"]}
        if type(args.get("entity_id")) is not int or args["entity_id"] not in self.refs:
            raise ValueError("Entity is outside this episode")
        r = self.call("entities.listLinks", {"entity_id": args["entity_id"], "direction": "outbound", **page})
        return {"links": [{"assertion_id": e["metadata"]["assertion_id"],
                           "subject": self.refs[e["from_entity_id"]], "object": self.refs[e["to_entity_id"]],
                           "predicate": "bench:issuer" if e["relationship_type_slug"] == "bench_issuer" else "org:subOrganizationOf"}
                          for e in r["relationships"]], "metadata": r["metadata"],
                "relationship_failures": r.get("relationship_failures", [])}


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class SqlError(ValueError):
    def __init__(self, stderr):
        match = re.search(r"ERROR:\s+([0-9A-Z]{5})\b", stderr)
        self.sqlstate = match.group(1) if match else None
        super().__init__(stderr.strip()[:700])


class Postgres:
    manifest = {"query": {"description": "Read-only PostgreSQL SQL. Tables: assertions(id text primary key, subject text, predicate text, object text), nodes(id text primary key, name text). Directed assertions only. Recursive CTEs supported. Maximum 1000 returned rows.",
                          "arguments": {"sql": "one SELECT or WITH query, no trailing semicolon"}}}

    def __init__(self, port):
        self.port = port

    def sql(self, sql, reader=False):
        r = subprocess.run(["psql", "-X", "-q", "-A", "-t", "-h", "127.0.0.1", "-p", str(self.port),
                            "-U", "bench_reader" if reader else "bench_owner", "graph_bench",
                            "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose", "-c", sql], capture_output=True, text=True, timeout=8)
        if r.returncode:
            raise SqlError(r.stderr)
        return r.stdout.strip()

    def load(self, world):
        self.sql("DROP TABLE IF EXISTS assertions,nodes; CREATE TABLE assertions(id text PRIMARY KEY, subject text NOT NULL, predicate text NOT NULL, object text NOT NULL); CREATE INDEX ON assertions(subject,predicate); CREATE TABLE nodes(id text PRIMARY KEY,name text NOT NULL);")
        values = ["(" + ",".join(literal(f[k]) for k in ["id", "subject", "predicate", "object"]) + ")" for f in world["facts"]]
        refs = sorted({f["subject"] for f in world["facts"]} | {f["object"] for f in world["facts"] if f["predicate"] != "bench:risk"})
        names = {x["id"]: x["label"] for x in world["names"]}
        self.sql("INSERT INTO assertions VALUES " + ",".join(values) + "; INSERT INTO nodes VALUES " +
                 ",".join("(" + literal(ref) + "," + literal(names.get(ref, ref)) + ")" for ref in refs) +
                 "; GRANT SELECT ON assertions,nodes TO bench_reader;")

    def withdraw(self, ids):
        if ids:
            # These are disposable benchmark tables, never Lobu event history.
            self.sql("DELETE FROM assertions WHERE id IN (" + ",".join(map(literal, ids)) + ")")

    def invoke(self, tool, args):
        if tool != "query" or set(args) != {"sql"} or not isinstance(args["sql"], str):
            raise ValueError("Expected query(sql)")
        query = args["sql"].strip()
        if len(query) > 16000 or query.split(None, 1)[0].upper() not in {"SELECT", "WITH"}:
            raise ValueError("Expected a bounded SELECT or WITH query")
        data = self.sql("BEGIN READ ONLY; SET LOCAL statement_timeout='2s'; SET LOCAL lock_timeout='1s'; "
                        "SELECT COALESCE(json_agg(r),'[]'::json) FROM (SELECT * FROM (" + query +
                        ") AS q LIMIT 1001) AS r; ROLLBACK;", reader=True)
        rows = json.loads(data)
        return {"rows": rows[:1000], "has_more": len(rows) > 1000}
