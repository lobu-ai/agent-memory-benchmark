# Methodology

## The fairness invariant

**Every system is reached only through its public client.** A system's adapter
may call its REST API, SDK, or local server — the same surface a real user has.
It may **not** read the system's database directly, use test-only hooks, or do
bespoke context engineering that other systems don't get. An adapter that needs
privileged access to score well is disqualifying: the point is to measure the
product a user actually gets.

## Run shape

- **Per-scenario isolation.** Each scenario is evaluated in a fresh state;
  systems do not retrieve across earlier scenarios in the same run. Where a
  public API has no bulk wipe, the adapter scopes writes with a scenario tag and
  filters on read (documented per adapter).
- **Uniform top-K.** Every system is asked for exactly the configured `topK`.
- **One shared answerer.** A single fixed model reads each system's retrieved
  context and answers. Holding it constant isolates the *memory system's*
  contribution from the reader model's strength. We publish which answerer was
  used; changing it changes everyone's number equally.
- **Multi-trial for published runs.** Public leaderboard runs use ≥3 trials and
  report run-to-run variability. Smoke configs are single-trial.
- **Raw metrics first.** Answer accuracy, retrieval recall, and citation quality
  are primary. Any blended "overall" is a secondary convenience score.

## Metrics

- **Retrieval recall** — fraction of a question's gold source steps that appear
  in the system's returned items (`retrievedIds` ∩ `expectedSourceStepIds`).
- **Answer accuracy** — the shared answerer's answer judged against expected
  answers.
- **Citation recall / precision** — overlap of cited ids with gold sources.
- **Latency** — retrieval-only wall time. Informative, not apples-to-apples
  between a local server and a hosted API; always read it next to context size.
- **Answerer context tokens** — how much context each system hands the reader.
  Lower-for-equal-accuracy is a real efficiency win.

## Answerer notes

- Reasoning models have thinking disabled where supported (e.g. z.ai) so the
  JSON answer can't be truncated mid-string.
- A global `ANSWERER_MAX_CONCURRENCY` cap (default 3) plus Retry-After-aware
  backoff keeps parallel systems from rate-limiting the shared answerer. This
  was added after a 5-way run where unbounded parallel answerer calls 429'd.

## Per-system fairness notes

- **Mem0 — currently excluded.** A uniform harness cannot reproduce Mem0's
  published ~66% on LongMemEval. We tried hard to be fair: turn-by-turn
  (not blob) ingestion, `infer:true`, poll-until-indexed, proper `top_k`, and a
  fully self-hosted local engine (OSS `mem0` lib + local Chroma, no SaaS quota)
  with both Gemini and BGE embedders. It still scores near-0% across categories
  because Mem0's extraction is **user-centric** (it distills facts about "the
  user" and underweights third-party / assistant facts that several LongMemEval
  categories require), and because reproducing Mem0's number needs *its own*
  answerer + ingestion pipeline, not a shared one. Publishing 0% would be
  unfair to Mem0, so it is excluded pending an eval that matches its intended
  setup. The local adapter (`adapters/mem0_local_adapter.py`) is kept for that.
- **Zep, Letta — quota / Docker blocked**, not measured here (Zep over its
  account episode quota; Letta self-host needs the Docker image).
- **Supermemory** runs against its hosted API (no clean self-host); **Lobu,
  Hindsight** run self-hosted with their real pipelines.

## Honest caveats

- **Sample/category skew.** Small slices (e.g. LongMemEval oracle-10) are not
  publishable on their own and can favor a system's strongest category. Use the
  full suites with multiple trials for claims.
- **Per-system ingest differs by design.** Some systems extract facts with an
  LLM at ingest (front-loading cost to shrink query context); others store raw.
  That difference is part of what's being measured — we report context tokens so
  it's visible.
- **Reproduce it yourself.** Every published number ships its config; the run
  command is in the README.
