# Memory Benchmark Report

> Suite **longmemeval-oracle-10** v1.0.0 · Generated **2026-05-30T14:26:43.060Z**

## Run configuration

- Trials: **1**
- Top-K: **8**
- Answerer: **glm-5.1 via https://api.z.ai/api/coding/paas/v4**
- Evaluation mode: **full QA**
- Scenario isolation: **per-scenario**
- Latency measurement: **retrieval-only**
- Context token estimate: **chars_div_4**

## Methodology notes

- Each benchmark scenario is evaluated in a fresh isolated system state. Systems do **not** search over previously ingested scenarios within the same run.
- Primary comparison metrics are **answer accuracy**, **retrieval recall**, and **citation quality**. The reported **overall** value is a secondary house score, not an official benchmark metric.
- Latency is **retrieval-only latency**. If one system is local/in-process and another is a hosted API, latency is informative but not fully apples-to-apples.
- Multi-trial runs report 95% confidence intervals when enough trials are available.

## Executive summary

- Highest answer accuracy: **Lobu** at **90.0%**
- Highest retrieval recall: **Lobu** at **100.0%**
- Fastest average retrieval latency: **Lobu** at **66ms**

## Leaderboard (raw metrics first)

| System | Answer | Retrieval | Citation | Avg latency | Avg context | Answerer prompt tok | Answerer completion tok | Overall* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Lobu | 90.0% | 100.0% | 96.7% | 66ms | 7662 tok | 69694 | 499 | 94.5% |
| Supermemory | 80.0% | 100.0% | 93.3% | 1744ms | 7762 tok | 70863 | 475 | 84.2% |
| Hindsight | 80.0% | 100.0% | 91.7% | 1918ms | 1020 tok | 13498 | 490 | 83.9% |
| Mem0 | 10.0% | 30.0% | 5.0% | 790ms | 118 tok | 3626 | 145 | 14.2% |

> * `Overall` is a secondary house score that blends answer/retrieval/citation/latency. Use the raw metrics above as the primary comparison.

## Lobu

> **Answer:** 90.0% · **Retrieval:** 100.0% · **Citation recall:** 96.7% · **Avg latency:** 66ms · **Overall (secondary house score):** 94.5%

- Trials: **1**
- Questions: **10**
- Citation precision: **100.0%**
- P95 latency: **72ms**
- Avg context: **7662 tok** (approx)

| Category | Answer | Retrieval | Citation | Avg latency | Avg context |
|---|---:|---:|---:|---:|---:|
| temporal-reasoning | 90.0% | 100.0% | 96.7% | 66ms | 7662 tok |

### Highlights

**Strongest categories**
- **temporal-reasoning** — answer 90.0%, retrieval 100.0%

**Weakest categories**
- **temporal-reasoning** — answer 90.0%, retrieval 100.0%

### Retrieval diagnostics
- Questions: **10**
- Distinct retrieved step IDs: **21**
- Total retrievals across questions: **21**
- Zero-recall questions (no retrieved ID matched any expected source): **0**
**Top retrieved step IDs**
| Step ID | Hit count | Share of questions |
|---|---:|---:|
| `answer_1c6b85ea_1` | 1 | 10.0% |
| `answer_1c6b85ea_2` | 1 | 10.0% |
| `answer_1cc3cd0c_1` | 1 | 10.0% |
| `answer_1cc3cd0c_2` | 1 | 10.0% |
| `answer_4be1b6b4_1` | 1 | 10.0% |
| `answer_4be1b6b4_2` | 1 | 10.0% |
| `answer_4be1b6b4_3` | 1 | 10.0% |
| `answer_5328c3c2_1` | 1 | 10.0% |
| `answer_5328c3c2_2` | 1 | 10.0% |
| `answer_6ea1541e_1` | 1 | 10.0% |

### Misses

| Category | Prompt | Issue | Expected source(s) | Retrieved | Answer | Cited |
|---|---|---|---|---|---|---|
| temporal-reasoning | What was the first issue I had with my new car after its first service? | answer miss | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | answer_4be1b6b4_3, answer_4be1b6b4_2, answer_4be1b6b4_1 | GPS system issue | answer_4be1b6b4_2, answer_4be1b6b4_3 |

## Supermemory

> **Answer:** 80.0% · **Retrieval:** 100.0% · **Citation recall:** 93.3% · **Avg latency:** 1744ms · **Overall (secondary house score):** 84.2%

- Trials: **1**
- Questions: **10**
- Citation precision: **100.0%**
- P95 latency: **2496ms**
- Avg context: **7762 tok** (approx)

| Category | Answer | Retrieval | Citation | Avg latency | Avg context |
|---|---:|---:|---:|---:|---:|
| temporal-reasoning | 80.0% | 100.0% | 93.3% | 1744ms | 7762 tok |

### Highlights

**Strongest categories**
- **temporal-reasoning** — answer 80.0%, retrieval 100.0%

**Weakest categories**
- **temporal-reasoning** — answer 80.0%, retrieval 100.0%

### Retrieval diagnostics
- Questions: **10**
- Distinct retrieved step IDs: **21**
- Total retrievals across questions: **21**
- Zero-recall questions (no retrieved ID matched any expected source): **0**
**Top retrieved step IDs**
| Step ID | Hit count | Share of questions |
|---|---:|---:|
| `answer_1c6b85ea_1` | 1 | 10.0% |
| `answer_1c6b85ea_2` | 1 | 10.0% |
| `answer_1cc3cd0c_1` | 1 | 10.0% |
| `answer_1cc3cd0c_2` | 1 | 10.0% |
| `answer_4be1b6b4_1` | 1 | 10.0% |
| `answer_4be1b6b4_2` | 1 | 10.0% |
| `answer_4be1b6b4_3` | 1 | 10.0% |
| `answer_5328c3c2_1` | 1 | 10.0% |
| `answer_5328c3c2_2` | 1 | 10.0% |
| `answer_6ea1541e_1` | 1 | 10.0% |

### Misses

| Category | Prompt | Issue | Expected source(s) | Retrieved | Answer | Cited |
|---|---|---|---|---|---|---|
| temporal-reasoning | What was the first issue I had with my new car after its first service? | answer miss | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | an issue with my car's GPS system | answer_4be1b6b4_3 |
| temporal-reasoning | Which device did I got first, the Samsung Galaxy S22 or the Dell XPS 13? | answer miss | answer_5328c3c2_1, answer_5328c3c2_2 | answer_5328c3c2_1, answer_5328c3c2_2 | Dell XPS 13 | answer_5328c3c2_1, answer_5328c3c2_2 |

## Hindsight

> **Answer:** 80.0% · **Retrieval:** 100.0% · **Citation recall:** 91.7% · **Avg latency:** 1918ms · **Overall (secondary house score):** 83.9%

- Trials: **1**
- Questions: **10**
- Citation precision: **100.0%**
- P95 latency: **2728ms**
- Avg context: **1020 tok** (approx)

| Category | Answer | Retrieval | Citation | Avg latency | Avg context |
|---|---:|---:|---:|---:|---:|
| temporal-reasoning | 80.0% | 100.0% | 91.7% | 1918ms | 1020 tok |

### Highlights

**Strongest categories**
- **temporal-reasoning** — answer 80.0%, retrieval 100.0%

**Weakest categories**
- **temporal-reasoning** — answer 80.0%, retrieval 100.0%

### Retrieval diagnostics
- Questions: **10**
- Distinct retrieved step IDs: **21**
- Total retrievals across questions: **21**
- Zero-recall questions (no retrieved ID matched any expected source): **0**
**Top retrieved step IDs**
| Step ID | Hit count | Share of questions |
|---|---:|---:|
| `answer_1c6b85ea_1` | 1 | 10.0% |
| `answer_1c6b85ea_2` | 1 | 10.0% |
| `answer_1cc3cd0c_1` | 1 | 10.0% |
| `answer_1cc3cd0c_2` | 1 | 10.0% |
| `answer_4be1b6b4_1` | 1 | 10.0% |
| `answer_4be1b6b4_2` | 1 | 10.0% |
| `answer_4be1b6b4_3` | 1 | 10.0% |
| `answer_5328c3c2_1` | 1 | 10.0% |
| `answer_5328c3c2_2` | 1 | 10.0% |
| `answer_6ea1541e_1` | 1 | 10.0% |

### Misses

| Category | Prompt | Issue | Expected source(s) | Retrieved | Answer | Cited |
|---|---|---|---|---|---|---|
| temporal-reasoning | What was the first issue I had with my new car after its first service? | answer miss | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | GPS system issue | answer_4be1b6b4_2, answer_4be1b6b4_3 |
| temporal-reasoning | Which vehicle did I take care of first in February, the bike or the car? | citation miss | answer_b535969f_2, answer_b535969f_1 | answer_b535969f_2, answer_b535969f_1 | bike | answer_b535969f_2 |
| temporal-reasoning | Which device did I got first, the Samsung Galaxy S22 or the Dell XPS 13? | answer miss | answer_5328c3c2_1, answer_5328c3c2_2 | answer_5328c3c2_2, answer_5328c3c2_1 | Dell XPS 13 | answer_5328c3c2_1, answer_5328c3c2_2 |

## Mem0

> **Answer:** 10.0% · **Retrieval:** 30.0% · **Citation recall:** 5.0% · **Avg latency:** 790ms · **Overall (secondary house score):** 14.2%

- Trials: **1**
- Questions: **10**
- Citation precision: **10.0%**
- P95 latency: **1810ms**
- Avg context: **118 tok** (approx)

| Category | Answer | Retrieval | Citation | Avg latency | Avg context |
|---|---:|---:|---:|---:|---:|
| temporal-reasoning | 10.0% | 30.0% | 5.0% | 790ms | 118 tok |

### Highlights

**Strongest categories**
- **temporal-reasoning** — answer 10.0%, retrieval 30.0%

**Weakest categories**
- **temporal-reasoning** — answer 10.0%, retrieval 30.0%

### Retrieval diagnostics
- Questions: **10**
- Distinct retrieved step IDs: **6**
- Total retrievals across questions: **6**
- Zero-recall questions (no retrieved ID matched any expected source): **4**
**Top retrieved step IDs**
| Step ID | Hit count | Share of questions |
|---|---:|---:|
| `answer_5328c3c2_1` | 1 | 10.0% |
| `answer_6ea1541e_2` | 1 | 10.0% |
| `answer_7a4a93f1_2` | 1 | 10.0% |
| `answer_b3763b6b_1` | 1 | 10.0% |
| `answer_d39b7977_1` | 1 | 10.0% |
| `answer_e936197f_1` | 1 | 10.0% |

### Misses

| Category | Prompt | Issue | Expected source(s) | Retrieved | Answer | Cited |
|---|---|---|---|---|---|---|
| temporal-reasoning | What was the first issue I had with my new car after its first service? | retrieval miss | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | — | unknown | — |
| temporal-reasoning | Which event did I attend first, the 'Effective Time Management' workshop or the 'Data Analysis using Python' webinar? | retrieval miss | answer_1c6b85ea_1, answer_1c6b85ea_2 | — | unknown | — |
| temporal-reasoning | Which vehicle did I take care of first in February, the bike or the car? | retrieval miss | answer_b535969f_2, answer_b535969f_1 | — | unknown | — |
| temporal-reasoning | Which device did I got first, the Samsung Galaxy S22 or the Dell XPS 13? | retrieval miss | answer_5328c3c2_1, answer_5328c3c2_2 | answer_5328c3c2_1 | Samsung Galaxy S22 | answer_5328c3c2_1 |
| temporal-reasoning | How many days before the team meeting I was preparing for did I attend the workshop on 'Effective Communication in the Workplace'? | retrieval miss | answer_e936197f_1, answer_e936197f_2 | answer_e936197f_1 | unknown | — |
| temporal-reasoning | How many days had passed between the Sunday mass at St. Mary's Church and the Ash Wednesday service at the cathedral? | retrieval miss | answer_6ea1541e_2, answer_6ea1541e_1 | answer_6ea1541e_2 | unknown | — |
| temporal-reasoning | How many days did it take for me to find a house I loved after starting to work with Rachel? | retrieval miss | answer_d39b7977_1, answer_d39b7977_2 | answer_d39b7977_1 | unknown | — |
| temporal-reasoning | Which seeds were started first, the tomatoes or the marigolds? | retrieval miss | answer_7a4a93f1_2, answer_7a4a93f1_1 | answer_7a4a93f1_2 | unknown | — |
| temporal-reasoning | How many days had passed between the Hindu festival of Holi and the Sunday mass at St. Mary's Church? | retrieval miss | answer_1cc3cd0c_2, answer_1cc3cd0c_1 | — | unknown | — |
| temporal-reasoning | How many days before the 'Rack Fest' did I participate in the 'Turbocharged Tuesdays' event? | retrieval miss | answer_b3763b6b_1, answer_b3763b6b_2 | answer_b3763b6b_1 | unknown | — |