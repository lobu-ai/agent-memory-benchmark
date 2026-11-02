"""Bounded OpenAI-compatible extraction transport for an owned local engine.

The engine dispatches its own functions. This bridge only converts model wire
formats; it contains no memory extraction, graph solver or answer oracle.
The activity endpoint is test instrumentation, not native product readiness.
"""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.request
import uuid

MODEL = {"providerID": "opencode-go", "id": "muse-spark-1.3-contributor"}


def model_message(text, tools):
    if not tools:
        return {"role": "assistant", "content": text}, "stop"
    start = text.find("{")
    if start < 0:
        # OpenAI auto tool choice permits a plain final response. Rejecting it
        # used to turn a successful extraction turn into a transport failure.
        return {"role": "assistant", "content": text}, "stop"
    value, end = json.JSONDecoder().raw_decode(text, start)
    if text[end:].strip() not in {"", "```"}:
        raise ValueError("Extra model output")
    if isinstance(value, dict) and set(value) == {"content"} and isinstance(value["content"], str):
        return {"role": "assistant", "content": value["content"]}, "stop"
    if not isinstance(value, dict) or set(value) != {"tool_calls"}:
        raise ValueError("Invalid tool envelope")
    calls = value["tool_calls"]
    if not isinstance(calls, list) or not 1 <= len(calls) <= 64:
        raise ValueError("Invalid tool count")
    allowed = {tool["function"]["name"] for tool in tools}
    converted = []
    for call in calls:
        if (not isinstance(call, dict) or set(call) != {"name", "arguments"}
                or call["name"] not in allowed or not isinstance(call["arguments"], dict)):
            raise ValueError("Invalid declared function call")
        converted.append({"id": "call_" + uuid.uuid4().hex, "type": "function",
                          "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])}})
    return {"role": "assistant", "content": None, "tool_calls": converted}, "tool_calls"


def make_server(state, ledger, host="127.0.0.1", port=0):
    lock = threading.Lock()
    existing = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    activity = {"seq": max([r.get("call", 0) for r in existing] or [0]), "in_flight": 0,
                "last_agent_final_seq": 0, "last_error_seq": 0, "errors": 0,
                "bridge_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != "/activity" or self.headers.get("Authorization") != "Bearer " + state["observer_token"]:
                self.send(403, {"error": "Forbidden"})
                return
            with lock:
                self.send(200, dict(activity))

        def do_POST(self):
            started = time.monotonic()
            request, record, status = {}, {}, 502
            try:
                if self.path != "/v1/chat/completions":
                    raise ValueError("Unsupported endpoint")
                if self.headers.get("Authorization") != "Bearer " + state["extractor_token"]:
                    self.send(403, {"error": "Forbidden"})
                    return
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 1000000:
                    raise ValueError("Request size out of bounds")
                request = json.loads(self.rfile.read(size))
                tools = request.get("tools") or []
                prompt = "Native model messages, in order:\n" + json.dumps(request["messages"])
                if tools:
                    prompt += "\nDeclared function tools:\n" + json.dumps(tools)
                    prompt += "\nTool choice: " + json.dumps(request.get("tool_choice", "auto"))
                    prompt += ('\nReturn ONE JSON object: {"tool_calls":[{"name":"declared name","arguments":{...}}]}'
                               ' to invoke functions, or {"content":"final text"} when finished. '
                               'Do not execute functions yourself. The calling application dispatches them. '
                               'Follow the native system instructions and declared argument schemas. '
                               'Preserve the source facts; do not invent tool results.')
                elif request.get("response_format"):
                    prompt += "\nReturn valid JSON matching: " + json.dumps(request["response_format"])
                with lock:
                    if activity["seq"] >= state["extraction_call_limit"]:
                        raise ValueError("Extraction call cap exhausted")
                    activity["seq"] += 1
                    activity["in_flight"] += 1
                    record["call"] = activity["seq"]
                auth = base64.b64encode(("opencode:" + state["password"]).encode()).decode()
                req = urllib.request.Request("http://127.0.0.1:" + str(state["opencode_port"]) + "/api/experimental/generate",
                    data=json.dumps({"prompt": prompt, "model": MODEL}).encode(),
                    headers={"Content-Type": "application/json", "Authorization": "Basic " + auth})
                with urllib.request.urlopen(req, timeout=100) as response:
                    result = json.load(response)
                text = result["data"]["text"]
                record.update(request=request, model_text=text, model=MODEL,
                              prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
                message, finish = model_message(text, tools)
                record.update(prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                              request=request, model_text=text, message=message, finish_reason=finish,
                              tool_count=len(tools), model=MODEL)
                body = {"id": "bench-" + str(record["call"]), "object": "chat.completion", "created": int(time.time()),
                        "model": MODEL["id"], "choices": [{"index": 0, "message": message, "finish_reason": finish}]}
                status = 200
            except Exception as exc:
                record["error_class"] = type(exc).__name__
                if isinstance(exc, ValueError):
                    record["error_reason"] = str(exc)[:200]
                body = {"error": {"type": type(exc).__name__, "message": "Extraction transport failed"}}
            finally:
                record.update(status=status, duration_ms=round((time.monotonic()-started)*1000), usage=None)
                with lock:
                    if "call" in record:
                        activity["in_flight"] -= 1
                        if status != 200:
                            activity["errors"] += 1
                            activity["last_error_seq"] = max(record["call"], activity["last_error_seq"])
                        elif record.get("tool_count") and record["finish_reason"] == "stop":
                            activity["last_agent_final_seq"] = max(record["call"], activity["last_agent_final_seq"])
                    with ledger.open("a") as stream:
                        stream.write(json.dumps(record) + "\n")
            if status == 200 and request.get("stream"):
                delta = {"role": "assistant"}
                if message.get("tool_calls"):
                    delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(message["tool_calls"])]
                else:
                    delta["content"] = message["content"]
                chunks = []
                for value, reason in [(delta, None), ({}, finish)]:
                    chunks.append("data: " + json.dumps({"id": body["id"], "object": "chat.completion.chunk",
                        "created": body["created"], "model": body["model"],
                        "choices": [{"index": 0, "delta": value, "finish_reason": reason}]}) + "\n\n")
                self.send(200, ("".join(chunks) + "data: [DONE]\n\n").encode(), "text/event-stream")
            else:
                self.send(status, body)

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.state.read_text())
    server = make_server(state, args.ledger, port=state["bridge_port"])
    server.serve_forever()


if __name__ == "__main__":
    main()
