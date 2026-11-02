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

Reports include the suite hash, harness revision, dirty-tree flag, system
versions, raw question answers, and failed systems. Missing source annotations
produce n/a attribution metrics. Gold answers and source labels are never sent
to command adapters; only source data and the current retrieval question cross
that boundary. Failed systems cause a nonzero exit after diagnostic reports are
written, including when every system fails. The credential-free `harness` CI
workflow runs regression tests separately from paid benchmark jobs.

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
