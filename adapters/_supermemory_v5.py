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
    def __init__(self, base, key, scope, timeout=30, ready_timeout=180):
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
        self.opener = urllib.request.build_opener(NoRedirect())
        self.requests = 0

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
            raise SupermemoryError(exc.code) from None
        except urllib.error.URLError:
            raise RuntimeError("Supermemory transport failed") from None

    def get(self, doc_id, timeout=None):
        return self.request("GET", "/document/" + urllib.parse.quote(doc_id, safe=""), timeout=timeout)

    def write(self, doc_id, content, metadata, replace=False):
        if replace:
            self.request("PATCH", "/document/" + urllib.parse.quote(doc_id, safe=""),
                         {"content": content, "metadata": metadata})
        else:
            self.request("POST", "/document", {"id": doc_id, "content": content,
                "metadata": metadata, "dreaming": "instant", "taskType": "memory"})
        return self.wait_ready(doc_id, content)

    def wait_ready(self, doc_id, expected_content):
        deadline = time.monotonic() + self.ready_timeout
        last = {}
        while time.monotonic() < deadline:
            doc = self.get(doc_id, timeout=min(self.timeout, max(.01, deadline-time.monotonic())))
            last = doc.get("system") or {}
            if last.get("status") == "failed":
                raise ReadinessError("Supermemory document processing failed")
            # Published old content can survive a replacement. Neither a stale
            # 'done' nor chunk readiness alone establishes fresh memory readiness.
            if (last.get("status") == "done" and last.get("dreamingStatus") == "done"
                    and doc.get("content") == expected_content):
                return {"id": doc_id, "system": last,
                        "content_sha256": hashlib.sha256(expected_content.encode()).hexdigest()}
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
                            {"include": {"content": True}} if kind == "documents" else {})

    def cleanup(self):
        if not self.scope.startswith("bench_"):
            raise ValueError("Cleanup restricted to owned benchmark namespaces")
        return self.request("DELETE", "", {})
