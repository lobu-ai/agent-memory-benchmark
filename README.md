# agent-memory-benchmark

A ClickBench-style, reproducible, **fair** benchmark for agent long-term memory
systems. One harness, many memory systems, multiple datasets — and a single
rule that makes the numbers trustworthy:

> **Every system is reached only through its public client. No privileged
> access.** No direct database queries, no test-only shortcuts, no bespoke
> per-system context engineering the others don't get.

Anyone can add a new memory system or a new dataset with a pull request; CI runs
it and the website renders the leaderboard.

## What it measures

For each question the harness ingests a conversation/knowledge history into the
system under test, then asks the system to **retrieve** the relevant memories.
A single, fixed **answerer** model reads that retrieved context and answers.
We report the raw metrics first:

- **Retrieval recall** — did the system surface the gold source memories?
- **Answer accuracy** — did the fixed answerer get it right from that context?
- **Citation recall / precision** — did it cite the right sources?
- **Latency** and **answerer context tokens** — cost/efficiency.

The shared answerer is held constant across systems so the score reflects the
**memory system**, not the reader model.

## Systems

| System | Adapter | Reached via |
|---|---|---|
| Lobu | `adapters/lobu_adapter.py` | public REST tool API of a running `lobu` server |
| Hindsight | `adapters/hindsight_adapter.py` | local Hindsight API server |
| Mem0 | `adapters/mem0_local_adapter.py` | local OSS `mem0` lib + local Chroma |
| Supermemory | `adapters/supermemory_adapter.py` | self-hosted `supermemory-server` binary (local) |

## Datasets

- **LongMemEval** (oracle) — single-session knowledge retention.
- **LoCoMo** — multi-session conversational memory.

New datasets implement a small loader (`harness/src/datasets/`) that emits a
suite of `{ steps, questions }` scenarios.

## Run it

```bash
bun install
# bring up the system(s) you want to test (see each adapter's header for env),
# then:
ANSWERER_MAX_CONCURRENCY=3 Z_AI_API_KEY=... \
  bun run scripts/run.ts --config configs/longmemeval-oracle-10.hindsight.json
```

Reports (JSON + Markdown leaderboard) land in `results/`.

### Small correction pilot

For a bounded first run, start a **disposable** local Lobu install, then run:

```bash
python3 scripts/run-local-pilot.py --base-url http://127.0.0.1:8787
```

This uses six synthetic cases, three trials, top-K four, and a fresh
`claude-sonnet-4-6` reader with tools and MCP disabled. The Claude CLI must
already be authenticated. It compares Lobu's public memory API with an explicit
SQLite FTS5 BM25 reference and a no-memory control. These controls do not
represent competing products. Model errors fail the run visibly; the helper
passes the local bootstrap credential only through the child environment.

Cases cover corrections, withdrawn claims, separate identities with the same
name, relationship evidence, unresolved conflicts, and stale recommendations.
The pilot uses normalized exact answer matching, reported separately from the
older substring proxy. It tests evidence retrieval and answering: relationship
steps are stored as text by this adapter, so it does **not** measure native graph
constraints, Automation execution, or authorization. No business action runs.
Six authored examples are a smoke test, not a publishable ranking.

Exact matching can reject correct explanations or grammatical variants. Keep
that strict score and run a separate semantic judge on the unchanged answers:

```bash
JUDGE_MODEL=claude-opus-4-6 node scripts/regrade-claude-judge.mjs results/<report>.json
```

The sidecar records the source report hash and per-question verdicts; unavailable
judgments make the semantic score incomplete. Opus and Sonnet share a vendor,
so this is a diagnostic review rather than an independent quality ranking.

Reports include the suite hash, harness revision, dirty-tree flag, system
versions, raw question answers, and failed systems. Missing source annotations
produce n/a attribution metrics. Gold answers and source labels are never sent
to command adapters; only source data and the current retrieval question cross
that boundary. Failed systems cause a nonzero exit after diagnostic reports are
written, including when every system fails. The credential-free `harness` CI
workflow runs regression tests separately from paid benchmark jobs.

### Native ontology conformance

The text-retrieval pilot cannot establish whether a platform uses its native
entity graph. Run the separate **model-free** suite to exercise typed records,
allowed relationship endpoints/direction, one- and three-hop traversal, namesake
separation, rejected patches, correction visibility, and edge withdrawal. It
also triggers real external Automation windows, checks their extraction schema,
rejects invalid entity/event metadata atomically, persists attributed events,
and verifies keyed upserts keep the same record across two runs.

Start a fresh, disposable local install from the synthetic fixture:

```bash
cd fixtures/ontology
bun install --frozen-lockfile
node_modules/.bin/lobu validate
# Choose unused gateway/proxy ports and a NEW data directory. Explicitly unset
# any inherited DATABASE_URL so it cannot select an existing database.
env -u DATABASE_URL LOBU_CONTEXT=ontology-benchmark \
  LOBU_API_URL=http://localhost:8899 LOBU_DATA_DIR="$(mktemp -d)" \
  WORKER_PROXY_PORT=8129 node_modules/.bin/lobu run --port 8899
```

The normal local bootstrap applies the fixture to the disposable install. In
another terminal, from the repository root:

```bash
python3 scripts/ontology-benchmark.py \
  --base-url http://127.0.0.1:8899 --runtime-ref 24.4.0
```

The runner accepts only HTTP loopback origins, refuses redirects, initializes
the local operator session in memory, and uses public SDK/tool endpoints. It
never reads Postgres directly or writes a cloud workspace. Negative checks use
the public REST tool boundary because the SDK isolate does not always retain
an exception's HTTP status. Validation 400/422 is distinguished from permission,
missing-resource, conflict, outage, and pending-approval results.

`results/ontology-conformance.json` contains all 20 checks, actual evidence,
failures/blocked prerequisites, API call count, duration, a frozen input hash,
source revision, and the operator-supplied exact runtime version/commit. The
CLI exits nonzero unless every check passes. Model calls and judge tokens are
zero. Stop the disposable runtime and remove only its temporary data/context
after retaining the report; never delete shared workspace history.

This is **scripted platform conformance**, not agent reasoning, a competitive
memory score, OWL/FIBO inference, connector synchronization, or an authorization
audit. Keep its score separate from retrieval QA and official STATE evaluation.

SQLite FTS5 is a retrieval control, not a product alternative to Lobu. Real
comparison targets are Mem0, Supermemory and Hindsight for agent memory;
Zep/Graphiti and Neo4j GraphRAG for typed/temporal knowledge; and Foundry or a
composed memory/workflow stack for operational tasks. See the staged comparison
plan in [METHODOLOGY.md](METHODOLOGY.md#product-comparisons-and-retrieval-controls).
Native graphs and connectors are not automatically unique to Lobu; compare the
actual editions, required application code, and successful task outcomes.

The next agent-task comparison should give OpenCode + Muse the same synthetic
facts with three access modes: text retrieval, typed records without edges, and
typed records with relationships. Tasks ask for the invoice's controller/risk,
a hold/release decision before and after a correction, and a safe state update.
Grade exact record/edge sets and saved state with a hidden deterministic oracle,
including unsupported-action penalties; do not send expected outcomes to agents.
Use identical model, facts, tools budget, one initial trial, distractor counts,
and failure accounting. Report task success, unsafe writes, attribution, API
calls, latency and recorded input/output tokens. A graph advantage needs to be
measured across held-out generated cases; 20 scripted checks do not establish it.

## Continuous benchmarking (GitHub Actions)

`.github/workflows/benchmark.yml` runs the whole thing in CI — it stands up the
self-hosted systems (Hindsight via `pip`, Lobu via `lobu run` with embedded
Postgres, Supermemory via its local binary), captures **each system's version
under test**, runs the benchmark, regenerates the leaderboard data, and uploads/commits
the result artifacts. This keeps results reproducible and version-tracked over
time (`systems[].version` in every report).

Trigger it from the **Actions → benchmark → Run workflow** button, or let the
weekly schedule run it.

Required repository secrets (Settings → Secrets and variables → Actions):

| Secret | Used for |
|---|---|
| `Z_AI_API_KEY` | the shared answerer (glm-5.1) + Hindsight's internal LLM |
| `SUPERMEMORY_API_KEY` | Supermemory self-hosted binary (any value for local) |

```bash
gh secret set Z_AI_API_KEY -R lobu-ai/agent-memory-benchmark
gh secret set SUPERMEMORY_API_KEY -R lobu-ai/agent-memory-benchmark
```

## Add a system (PR)

1. Add `adapters/<system>_adapter.py` implementing the `reset/setup/ingest/retrieve`
   protocol in `adapters/_bench_protocol.py`. **Use only the system's public
   client.**
2. Add a `command` entry to a config in `configs/`.
3. Open a PR. CI runs the smoke suite; the website picks up the result.

See [METHODOLOGY.md](./METHODOLOGY.md) for the fairness rules and scoring.

## License

MIT.
