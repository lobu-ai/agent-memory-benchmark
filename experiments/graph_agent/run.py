"""Experimental Muse agent loop. No evaluator/oracle imports or solving tools.

The controller owns the fixture and sequential source updates. Only current
public-tool results and a complete incremental withdrawal feed reach Muse.
The feed and snapshot bindings are benchmark application glue, not a native
Lobu or PostgreSQL change-capture capability claim.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time

from adapters import Lobu, Postgres, post

ROOT = Path(__file__).resolve().parents[2]
MODEL = {"providerID": "opencode-go", "id": "muse-spark-1.3-contributor"}
BINDINGS = ["snapshot_id", "required_watermark", "principal", "authorization_scope", "source_id", "valid_at", "observation_cutoff"]
POLICY = """You are a benchmark agent with only the declared JSON tools. No shell, filesystem, external tools, or hidden knowledge is available. Return exactly one JSON object per turn:
{"calls":[{"tool":"declared_name","arguments":{...}},...]} OR
{"final":{"answer":["node_ref",...],"proof":["assertion_id",...],"certificate":{...}}}.
Do not put tool calls and final in the same object. Do not include Markdown.
Find ALL current open-risk organizations reachable from the invoice issuer via ZERO OR MORE directed org:subOrganizationOf links. Cycles do not create new paths. Names are not identity. This seed has no inverse/subproperty reasoning. Risk is a benchmark label, not authority.
Ground positive answers in assertion IDs you read. SQL evidence must include actual raw assertion records (id,subject,predicate,object), not only an aggregate answer. Native node risk metadata maps to a bench:risk assertion; relationship assertion_id maps to its directed assertion.
Every exact set, including an empty one, needs a complete frontier certificate. Certificate shape:
{snapshot_id,required_watermark,principal,authorization_scope,source_id,valid_at,observation_cutoff,health:"healthy",issuer_assertions:[all active issuer assertion IDs for the invoice],issuer_pages_complete:true,issuer_terminal_cursor:null,frontier:{"each reachable node_ref":{"outbound_assertions":[all active outgoing org:subOrganizationOf assertion IDs],"risk_assertions":[all its active bench:risk assertion IDs],"pages_complete":true,"terminal_cursor":null}}}.
Use sorted ID arrays. Include every reachable node, even nodes with no risk and no outgoing edges. Return the bound current snapshot fields exactly. Partial pages cannot certify absence. Source coverage is complete over this bounded world, all three predicates, and the principal's scope.
Between checkpoints a complete source feed supplies all withdrawals since the last checkpoint and binds the new snapshot. Previously read facts and terminal-page receipts remain valid except the withdrawn assertions. No future updates are visible. You may reuse retained reads after applying that feed; these change-feed guarantees belong to the benchmark application.
Budget for the WHOLE three-checkpoint episode: 10 model turns, 25 underlying tool invocations, 200000 tool-response bytes, 360 seconds. Independent calls can be batched. Spend calls carefully. Schema/setup/writes are controller work and excluded from reader tool counts. Errors still count. Be explicit with a final answer; do not merely describe a plan.
"""


def parse_reply(text):
    # The stateless endpoint may prefix a valid object with narration. Accept
    # one object only; never search for multiple executable fragments.
    start = text.find("{")
    if start < 0:
        raise ValueError("Missing JSON object")
    value, end = json.JSONDecoder().raw_decode(text, start)
    if text[end:].strip() not in {"", "```"}:
        raise ValueError("Unexpected content after JSON object")
    if not isinstance(value, dict) or set(value) not in ({"calls"}, {"final"}):
        raise ValueError("Return exactly calls or final")
    if "calls" in value:
        if not isinstance(value["calls"], list) or not 1 <= len(value["calls"]) <= 25:
            raise ValueError("Expected 1..25 calls")
        for call in value["calls"]:
            if not isinstance(call, dict) or set(call) != {"tool", "arguments"}:
                raise ValueError("Invalid call")
    elif not isinstance(value["final"], dict):
        raise ValueError("Invalid final")
    return value


class Executor:
    def __init__(self, state, output):
        self.state, self.output = state, output
        self.calls = state.get("prior_model_calls", 0)

    def generate(self, messages, remaining):
        if self.calls >= 80:
            raise RuntimeError("Global model-call budget exhausted")
        self.calls += 1
        prompt = "\n\n".join(x["role"] + ":\n" + x["content"] for x in messages)
        started = time.monotonic()
        record = {"kind": "model", "call": self.calls, "model": MODEL,
                  "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                  "prompt_bytes": len(prompt.encode()), "usage": None}
        try:
            auth = base64.b64encode(("opencode:" + self.state["password"]).encode()).decode()
            r = post("http://127.0.0.1:" + str(self.state["opencode_port"]) + "/api/experimental/generate",
                     {"prompt": prompt, "model": MODEL}, {"Authorization": "Basic " + auth}, timeout=min(120, remaining))
            text = r["data"]["text"]
            record.update(status="completed", text=text)
            return text
        except Exception as exc:
            record.update(status="failed", error=type(exc).__name__ + ": " + str(exc)[:300])
            raise
        finally:
            record["latency_ms"] = round((time.monotonic() - started) * 1000)
            self.append(record)

    def append(self, record):
        with self.output.open("a") as f:
            f.write(json.dumps(record) + "\n")


def episode(world, adapter, executor, system):
    started = time.monotonic()
    turns, calls, byte_count = 0, 0, 0
    seen_withdrawals = set()
    messages = [{"role": "system", "content": POLICY}, {"role": "user", "content": json.dumps({
        "task": world["task"], "invoice": world["invoice"], "inference_profile": world["inferenceProfile"],
        "tools": adapter.manifest, "coverage": world["coverage"]})}]
    results = []
    for checkpoint in world["checkpoints"]:
        removed = set(checkpoint["withdraw"]) - seen_withdrawals
        adapter.withdraw(sorted(removed))
        seen_withdrawals |= removed
        visible = {k: v for k, v in checkpoint.items() if k != "withdraw"}
        visible["newly_withdrawn_assertion_ids"] = sorted(removed)
        visible["incremental_feed_complete"] = True
        messages.append({"role": "user", "content": "Current checkpoint: " + json.dumps(visible)})
        executor.append({"kind": "checkpoint", "system": system, "episode": world["id"], "checkpoint": visible})
        row = {"system": system, "episode": world["id"], "checkpoint": checkpoint["id"]}
        while True:
            remaining = 360 - (time.monotonic() - started)
            if turns >= 10 or byte_count >= 200000 or remaining <= 0:
                row.update(status="budget_exhausted")
                break
            turns += 1
            try:
                raw = executor.generate(messages, remaining)
                messages.append({"role": "assistant", "content": raw})
                reply = parse_reply(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                messages.append({"role": "user", "content": "Protocol error: " + str(exc)[:200]})
                continue
            except Exception as exc:
                row.update(status="executor_failed", error=str(exc)[:300])
                break
            if "final" in reply:
                if time.monotonic()-started >= 360:
                    row.update(status="budget_exhausted")
                else:
                    row.update(status="answered", final=reply["final"])
                break
            if calls >= 25:
                messages.append({"role": "user", "content": "Tool budget exhausted. Return your final answer using existing evidence."})
                continue
            for call in reply["calls"]:
                if calls >= 25 or time.monotonic() - started >= 360 or byte_count >= 200000:
                    break
                calls += 1
                before = time.monotonic()
                try:
                    result = adapter.invoke(call["tool"], call["arguments"])
                except Exception as exc:
                    result = {"error": type(exc).__name__, "message": str(exc)[:700]}
                encoded = json.dumps(result)
                response_size = len(encoded.encode())
                record = {"kind": "tool", "system": system, "episode": world["id"],
                          "checkpoint": checkpoint["id"], "tool": call["tool"], "arguments": call["arguments"],
                          "argument_sha256": hashlib.sha256(json.dumps(call["arguments"], sort_keys=True).encode()).hexdigest(),
                          "result": result, "result_bytes": len(encoded.encode()), "latency_ms": round((time.monotonic()-before)*1000)}
                executor.append(record)
                if byte_count + response_size > 200000:
                    byte_count = 200000
                    break
                byte_count += response_size
                messages.append({"role": "user", "content": "Tool result: " + json.dumps({"tool": call["tool"], "arguments": call["arguments"], "result": result})})
        row.update(cumulative_model_calls=turns, cumulative_tool_calls=calls, cumulative_tool_bytes=byte_count,
                   elapsed_ms=round((time.monotonic()-started)*1000))
        results.append(row)
        executor.append({"kind": "answer", **row})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", type=Path, required=True, help="Private local service state; never publish")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--systems", nargs="+", choices=["lobu", "postgres"], default=["lobu", "postgres"])
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    state = json.loads(args.state.read_text())
    source = ROOT / "fixtures/graph-agent/worlds.json"
    worlds = json.loads(source.read_text())["episodes"]
    executor = Executor(state, args.output / "trace.jsonl")
    result = {"experimental": True, "fixture_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py") if p.name in {"run.py", "adapters.py"}},
              "prior_diagnostic_model_calls_charged": state.get("prior_model_calls", 0),
              "model": MODEL, "independent_episodes": 2, "matched_pairs": 1,
              "usage_note": "OpenCode stateless endpoint returns no billed token or dollar usage.",
              "capability": "agent query-time directed traversal; no native OWL claim",
              "application_glue": ["structural mappings", "complete incremental withdrawal feed", "snapshot bindings"], "rows": []}
    for system in args.systems:
        for world in worlds:
            adapter = Lobu("http://127.0.0.1:" + str(state["lobu_port"])) if system == "lobu" else Postgres(state["pg_port"])
            try:
                adapter.load(world)
                result["rows"].extend(episode(world, adapter, executor, system))
            except Exception as exc:
                result["rows"].append({"system": system, "episode": world["id"], "status": "setup_failed", "error": str(exc)[:500]})
            (args.output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "model_calls": executor.calls,
                      "statuses": [x["status"] for x in result["rows"]]}))


if __name__ == "__main__":
    main()
