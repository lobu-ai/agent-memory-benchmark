"""Supermemory v5 public HTTP client; no task-specific retrieval or reasoning.

Contracts: supermemory.ai/docs/ingestion/{add-memories,document-operations}
and /recall/search. No fallback to v4: a missing endpoint is a setup failure.
"""
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class SupermemoryError(RuntimeError):
    def __init__(self, status):
        self.status = status
        # Do not reflect provider bodies, which can echo credentials or content.
        super().__init__(f"Supermemory HTTP {status}")


class ReadinessError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Supermemory redirects are disabled")


def namespace(*parts):
    return "bench_" + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:32]


class Client:
    def __init__(self, base, key, scope, timeout=30, ready_timeout=180, list_content=True, observer=None,
                 replacement_mode="patch"):
        url = urllib.parse.urlsplit(base)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("Supermemory requires an explicit HTTP(S) origin")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote Supermemory requires HTTPS")
        if not key or not scope or len(scope) > 100:
            raise ValueError("Explicit key and namespace required")
        self.base, self.key, self.scope = base.rstrip("/"), key, scope
        self.timeout, self.ready_timeout = timeout, ready_timeout
        self.list_content = list_content
        if replacement_mode not in {"patch", "delete_reingest"}:
            raise ValueError("Explicit supported replacement mode required")
        self.replacement_mode = replacement_mode
        self.observer = observer
        if observer:
            url = urllib.parse.urlsplit(observer["url"])
            if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or url.username or url.password or url.path != "/activity" or url.query or url.fragment):
                raise ValueError("Completion observer must be an owned loopback activity endpoint")
        self.opener = urllib.request.build_opener(NoRedirect())
        self.requests = 0
        self.observer_requests = 0

    def activity(self):
        self.observer_requests += 1
        req = urllib.request.Request(self.observer["url"], headers={"Authorization": "Bearer " + self.observer["token"]})
        with self.opener.open(req, timeout=self.timeout) as response:
            return json.load(response)

    def memory_snapshot(self):
        values = []
        for page in range(1, 11):
            result = self.list("memories", page, 100)
            values.extend(result["memories"])
            if page >= result["pagination"]["totalPages"]:
                return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        raise ReadinessError("Public memory snapshot exceeds 1000-row readiness bound")

    def request(self, method, path="", body=None, timeout=None):
        url = self.base + "/ns/" + urllib.parse.quote(self.scope, safe="") + path
        req = urllib.request.Request(url, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        self.requests += 1
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise SupermemoryError(status) from None
        except urllib.error.URLError:
            raise RuntimeError("Supermemory transport failed") from None

    def get(self, doc_id, timeout=None):
        return self.request("GET", "/document/" + urllib.parse.quote(doc_id, safe=""), timeout=timeout)

    def write(self, doc_id, content, metadata, replace=False, supporting_context=None):
        started, request_count = time.monotonic(), self.requests
        baseline = self.activity() if self.observer else None
        if baseline and baseline["in_flight"]:
            raise ReadinessError("Serial extraction observer requires no prior in-flight model request")
        if replace and self.replacement_mode == "delete_reingest":
            deleted = self.request("DELETE", "/document", {"ids": [doc_id]})
            if deleted.get("count") != 1 or deleted.get("errors"):
                raise ReadinessError("Source replacement deletion was incomplete")
        if replace and self.replacement_mode == "patch":
            self.request("PATCH", "/document/" + urllib.parse.quote(doc_id, safe=""),
                         {"content": content, "metadata": metadata})
        else:
            body = {"id": doc_id, "content": content,
                    "metadata": metadata, "dreaming": "instant", "taskType": "memory"}
            if supporting_context:
                body["supportingContext"] = supporting_context
            self.request("POST", "/document", body)
        receipt = self.wait_ready(doc_id, content, baseline)
        receipt.update(operation="replace" if replace else "create",
                       replacement_mode=self.replacement_mode if replace else None,
                       elapsed_ms=round((time.monotonic()-started)*1000), http_requests=self.requests-request_count)
        return receipt

    def wait_ready(self, doc_id, expected_content, baseline=None):
        deadline = time.monotonic() + self.ready_timeout
        digest, stable_since = None, None
        while time.monotonic() < deadline:
            doc = self.get(doc_id, timeout=min(self.timeout, max(.01, deadline-time.monotonic())))
            last = doc.get("system") or {}
            if last.get("status") == "failed":
                raise ReadinessError("Supermemory document processing failed")
            if last.get("status") == "done" and "dreamingStatus" not in last and not self.observer:
                raise ReadinessError("This build omits system.dreamingStatus; memory completion is unverifiable")
            # Published old content can survive a replacement. Neither a stale
            # 'done' nor chunk readiness alone establishes fresh memory readiness.
            if (last.get("status") == "done" and last.get("dreamingStatus") == "done"
                    and doc.get("content") == expected_content):
                return {"id": doc_id, "system": last,
                        "readiness_contract": "native dreamingStatus",
                        "content_sha256": hashlib.sha256(expected_content.encode()).hexdigest()}
            if self.observer and last.get("status") == "done" and doc.get("content") == expected_content:
                activity = self.activity()
                # This serial test install has one document write at a time.
                # Observe a NEW completed tool loop, then stable public reads;
                # never wait for a gold answer, memory count or expected fact.
                if (activity["in_flight"] == 0 and activity["last_agent_final_seq"] >
                        max(baseline["seq"], activity["last_error_seq"])):
                    current = self.memory_snapshot()
                    if current != digest:
                        digest, stable_since = current, time.monotonic()
                    elif time.monotonic()-stable_since >= self.observer.get("settle_seconds", 2):
                        return {"id": doc_id, "system": last, "readiness_contract": "instrumented quiescence (application glue)",
                                "observer": activity, "memory_snapshot_sha256": digest,
                                "content_sha256": hashlib.sha256(expected_content.encode()).hexdigest()}
                else:
                    digest, stable_since = None, None
            time.sleep(min(.5, max(0, deadline-time.monotonic())))
        raise ReadinessError("Supermemory readiness timeout (document AND memories AND current content required)")

    def search(self, query, limit=10, mode="hybrid", threshold=0, rerank="none"):
        if not isinstance(query, str) or not query.strip() or not 1 <= limit <= 100:
            raise ValueError("Expected a query and limit 1..100")
        if mode not in {"hybrid", "memories", "chunks"} or rerank not in {"none", "order", "aggregate"}:
            raise ValueError("Invalid v5 search mode or rerank")
        if not 0 <= threshold <= 1:
            raise ValueError("Threshold out of bounds")
        return self.request("POST", "/search", {"query": query, "limit": limit,
            "searchMode": mode, "threshold": threshold, "rerank": rerank,
            "include": {"documents": True, "related": True, "forgotten": False}})

    def list(self, kind="documents", page=1, limit=100):
        if kind not in {"documents", "memories", "chunks"} or type(page) is not int or page < 1:
            raise ValueError("Invalid list request")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Limit out of bounds")
        return self.request("POST", f"/list/{kind}?page={page}&limit={limit}&order=asc",
                            {"include": {"content": True}} if kind == "documents" and self.list_content else {})

    def cleanup(self):
        if not self.scope.startswith("bench_"):
            raise ValueError("Cleanup restricted to owned benchmark namespaces")
        return self.request("DELETE", "", {})
