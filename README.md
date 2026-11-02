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

## Public knowledge-graph diagnostic

The authored pilot and native conformance fixture do not use a public ontology.
For harder public questions, prepare a bounded **KQA Pro** validation sample:

```bash
python3 scripts/prepare-kqapro.py --download
```

This verifies pinned input hashes and executes the upstream reference programs
against the complete public KB (16,960 entities and 794 concepts). It exports
24 reference-verified questions: six each for multi-hop, set, comparison and
qualifier reasoning, with at least six program steps. The audit records original
validation indexes, reference programs, rejected candidates, source hashes and
the frozen suite hash. Point an existing benchmark config's `suitePath` at
`results/kqapro-suite.json` to run the same adapters and shared reader.

This is an **oracle-subgraph diagnostic**, not an official KQA Pro score. Gold
programs select complete visited records; eight deterministic unrelated records
are added per case. Neither programs, answer choices nor gold labels are sent
to adapters. Records are shuffled with neutral source IDs. Oversized cases are
skipped rather than truncated, and the audit reports this selection bias.
Source recall means visited-record coverage, not minimal-proof recall. Current
memory adapters flatten the graph, so this does not measure native graph
navigation, OWL entailment, permissions or actions. Keep any full-context reader
control separate from product retrieval scores.

Source and attribution: [KQA Pro](https://github.com/shijx12/KQAPro_Baselines),
Cao et al., *KQA Pro: A Dataset with Explicit Compositional Programs for Complex
Question Answering over Knowledge Base*. The upstream declares dataset
CC-BY-SA-4.0 and reference code MIT; dataset mirrors may label this differently.
Generated data stays under ignored `results/` and carries the upstream license
in its audit. Hash-pinned mirrors are used because the original download link
is unavailable; the reference executor checks selected answers against the KB.
The mirror's concept `subclassOf` field is exposed to the older executor as
`instanceOf`, and edge `relation` as `predicate`; no edges or facts are added.
The normalized KB hash is recorded and conflicting aliases are rejected.

For ontology entailment, [LUBM](https://swat.cse.lehigh.edu/projects/lubm/)
provides a public university OWL ontology and 14 query templates. It is a
separate future track: importing typed records does not implement its subclass,
inverse-property, transitive-property or restriction inference requirements.
For product comparisons, run native graph tasks against Graphiti/Neo4j rather
than label a text-memory score an ontology-system ranking.

## Experimental native graph agent smoke

`experiments/graph_agent/` is a separate agent-loop experiment, not part of
the retrieval leaderboard. It contains two public development worlds: a
four-edge risk path with an alternate support, and a matched world with that
alternate edge reversed. Each has three sequential withdrawal checkpoints,
same-name distractors, a grounded cycle, and 50 disconnected distractor edges.
This is two base episodes, one matched pair, six correlated queries per system.

The Lobu adapter uses public `entities.create`, `link`, `unlink`, `list`, and
`listLinks` APIs on an explicit disposable loopback install. `entities.delete`
resets only fixture-owned synthetic rows before ingestion. Typed metadata and
directed links are mapped structurally; adapters contain no task solver. The
PostgreSQL control exposes read-only SQL over normalized assertions and nodes,
including recursive CTEs. SQL is a correctness and navigation-cost control,
not a competing memory product. Graphiti is not implemented in this smoke.

Muse runs through the existing authenticated OpenCode stateless endpoint with
only the harness's JSON tool protocol. It receives neither future updates nor
the oracle. A complete incremental withdrawal feed and snapshot bindings are
provided by benchmark application glue; these are not native product credit.
Limits across all three checkpoints: 10 model calls, 25 tool invocations,
200 KB of tool responses, 360 seconds; global model-call cap 80. Responses may
contain a prose prefix followed by one JSON object. Errors consume budget.
Native API granularity differs from SQL; reports retain tool bytes and latency.

Use isolated services you own: apply `fixtures/graph-agent` with pinned CLI
24.4.0 to a disposable local Lobu install; initialize a separate PostgreSQL
database `graph_bench` with owner `bench_owner` and SELECT-only login
`bench_reader`; start `opencode serve` on loopback using its existing provider
authorization. Supply a private service-state JSON with `lobu_port`, `pg_port`,
`opencode_port`, and the generated OpenCode server `password`. Optional
`prior_model_calls` charges earlier diagnostic attempts against the global cap.
Never publish this state or point the runner at a production workspace.

```bash
python3 -m unittest discover -s experiments/graph_agent -p 'test_*.py'
python3 experiments/graph_agent/probe.py --state <private-service-state.json> --output <storage-receipts.json>
python3 experiments/graph_agent/run.py --state <private-service-state.json> --output <new-output-directory>
python3 experiments/graph_agent/grade.py <output-directory>
```

The offline evaluator checks answers against an independent recursive reference,
accepts either sufficient path, rejects stale/unread proof citations, and checks
exact-set frontier certificates against the bounded complete source. Empty sets
need the same coverage proof. Read grounding verifies assertion content in tool
results; it does not prove arbitrary SQL expression lineage. This seed has one
source and one page per reachable subject. It does not test multipage recovery,
authorization, temporal ORG semantics, approvals, dispatch, or OWL entailment.
Run those tracks and a graph-product comparison before making broader claims.
Raw traces, fixed denominators (including setup failures), fixture/code hashes,
and diagnostic attempts must accompany results. OpenCode's endpoint exposes no
billed token/dollar usage, so none is estimated.
Storage probes compare all persisted assertions after each withdrawal through
fresh native API reads, and verify PostgreSQL write/file-read rejection by
SQLSTATE. Run probes and agent trials sequentially; both own disposable data.

Generate additional graph development questions with:

```bash
python3 experiments/graph_agent/generate.py --output results/graph-dev-24.json
python3 experiments/graph_agent/run.py --state <private-service-state.json> --suite results/graph-dev-24.json --episodes graph-dev-01 graph-dev-02 --output <new-batch-directory>
python3 experiments/graph_agent/grade.py <batch-directory> --suite results/graph-dev-24.json
```

The next product comparison focuses on Lobu versus Supermemory. Verify a pinned
Supermemory build and its current document/derived-memory pipeline first; the
saved 0.0.9 v4 direct-memory result is a historical retrieval diagnostic. A
native Supermemory agent adapter is still needed. Use the same Muse reader,
source facts and budgets, with supported native tools for each product. Record
ingestion/extraction settings and readiness separately. Graphiti and Neo4j are
deferred; PostgreSQL remains a correctness control.

The generator emits eight worlds in four structural pairs: parallel branches,
a shared choke edge, multiple risky targets, and exits from a reachable cycle.
Each world has three sequential checkpoint queries, for 24 correlated queries.
The separate `.reference.json` sidecar checks each answer by path enumeration
and recursive SQL. Keep that evaluator artifact away from the executor. Each
pair differs in one edge's direction. Seeds reproduce identifiers and order;
changing a seed alone does not create a structural holdout. These cases expand
graph maintenance only; they do not implement the reviewed temporal,
authorization, pagination or governed-action tracks.

Select explicit episode IDs for bounded batches. The 80-call global cap remains
in force for a run, and `prior_model_calls` charges earlier attempts; never reset
the counter to retry a failed batch. Evaluation checks the executed fixture hash
and uses the selected episodes' denominator, including setup failures. A partial
batch must not be reported as complete coverage of all 24 queries.

Build a standalone question-level report from saved retrieval, native smoke and
generated-development artifacts (no model calls):

```bash
python3 experiments/graph_agent/report.py --retrieval-dir <retrieval-artifacts> --native-summary <native-summary.json> --generated-suite results/graph-dev-24.json --output <report-directory>/index.html
```

The retrieval artifacts are `suite.json`, `comparison.json`,
`semantic-review.json` and the source reports named by the comparison manifest.
Their hashes must agree. The native summary is the combined smoke manifest with
`systems`, `rows`, fixture hash and source commit. The report keeps strict versus
same-reader semantic review separate, shows paired wins/losses and missing
reference records for each question, and labels generated questions as not run.

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
