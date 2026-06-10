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
  answers. **Published numbers use an LLM judge** (`gemini-2.5-flash`, separate
  from the z.ai/GLM answerer), applied identically to every system, via
  `scripts/regrade-llm-judge.mjs`. The in-run substring matcher in `scoring.ts`
  is a fast proxy only — it wrongly fails correct paraphrases ("three and a half
  weeks" vs "3.5 weeks", "GPS system issue" vs "GPS system not functioning
  correctly"), so it understates every system. The judge both rescues those and
  can demote a spurious substring match. Disclosed here because the judge choice
  is part of the result; a stronger/ensemble judge is the obvious next step.
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

- **Gold-marker leakage (fixed).** LongMemEval tags the answer-bearing turns
  with `has_answer`, and the converter was rendering that as a visible
  `[evidence]` marker in the session text fed to the reader ("Turn 5 (user
  [evidence]): ..."). That leaks the answer *location* to every system that
  stores raw content and inflates all scores. The marker is now stripped at the
  source (`datasets/longmemeval.ts`); any number from before that fix is not
  comparable. Lesson: audit the content each system actually ingests for
  annotation leakage before publishing.
- **Sample/category skew.** Small slices (e.g. LongMemEval oracle-10) are not
  publishable on their own and can favor a system's strongest category. Use the
  full suites with multiple trials for claims.
- **Single-trial numbers are noise.** On mixed-60 we measured the *same* system
  swing ~±14 points across trials (one answer flipping moves a 10-question
  category by 10%). A single run cannot rank two systems whose means are within
  ~10 points. The leaderboard reports the per-trial range next to each mean for
  exactly this reason — read the spread, not the point.
- **single-session-preference scores ~0 for everyone.** Those questions are
  graded against a meta-description of the user's preference ("would prefer
  resources tailored to X"), while systems return a concrete recommendation, so
  the string/judge match fails uniformly. It's an eval-grading mismatch, not a
  system gap — fixing it (preference-alignment judge) would raise all systems.
- **Per-system ingest differs by design.** Some systems extract facts with an
  LLM at ingest (front-loading cost to shrink query context); others store raw.
  That difference is part of what's being measured — we report context tokens so
  it's visible.
- **Reproduce it yourself.** Every published number ships its config; the run
  command is in the README.

## Finding: the Lobu↔Supermemory gap was retrieval recall (2026-06-10)

A per-question decomposition of the published 3-trial runs showed the entire
Supermemory(85%)↔Lobu(80%) judged gap was **retrieval recall**: +9 trials across
3 questions whose gold sessions Supermemory retrieved and Lobu missed
(conversational-filler queries that embed poorly; synonym gaps like
doctor/physician). The "focused extracted memories give the reader cleaner
context" hypothesis was **falsified** — on every same-recall question both
systems fed the reader the same whole-session chunks within ~1% of the same
tokens. Consistent with that, every extraction/focused/digest variant we tried
regressed (56.7–65% vs the 80% baseline; the configs are in `configs/`).

The fix is **query rewriting**: a small LLM rewrites the conversational
question into focused keyword variants, searched alongside the raw query
(`LOBU_QUERY_REWRITE=1` in `adapters/lobu_adapter.py`; the same capability
ships in the Lobu server as `read_knowledge({rewrite_query: true})`, so the
adapter exercises the product, not adapter glue). Canonical 3-trial mixed-60
result vs the published baseline (same suite, answerer, substring grader):

|                    | Baseline (whole sessions) | + query rewrite           |
| ------------------ | ------------------------- | ------------------------- |
| Retrieval recall   | 95.6%                     | **100.0% (all 3 trials)** |
| Answer (substring) | 68.3% (66.7–70.0)         | 69.4% (68.3–70.0)         |
| multi-session      | 67%                       | **87%**                   |
| knowledge-update   | 93%                       | 83%                       |

Honest read: recall — a primary, deterministic metric — is now perfect,
closing the structural gap to Supermemory; answer accuracy is
parity-to-slightly-better under the substring proxy (the floor rises
66.7→68.3). The **LLM-judged** number for this configuration is pending (the
gemini judge hit its monthly spend cap); the leaderboard keeps the judged 80%
baseline until then. Disclosed trade-off: knowledge-update dips (93→83) — the
variant queries pull additional stale-value sessions about the updated fact
into the top-k. A real Lobu deployment masks superseded values server-side;
the benchmark adapter stores sessions without supersession, so this is
adapter-context behavior, not a product property.
