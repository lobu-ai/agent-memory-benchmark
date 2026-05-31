# Memory Benchmark Report

> Suite **longmemeval-mixed-30** v1.0.0 · Generated **2026-05-31T14:38:45.740Z**

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

- Highest answer accuracy: **Supermemory** at **63.3%**
- Highest retrieval recall: **Supermemory** at **100.0%**
- Fastest average retrieval latency: **Lobu** at **46ms**

## Leaderboard (raw metrics first)

| System | Answer | Retrieval | Citation | Avg latency | Avg context | Answerer prompt tok | Answerer completion tok | Overall* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Supermemory | 63.3% | 100.0% | 71.4% | 1601ms | 6209 tok | 177518 | 1467 | 71.5% |
| Lobu | 50.0% | 96.7% | 68.3% | 46ms | 6069 tok | 173185 | 1222 | 68.4% |
| Hindsight | 43.3% | 100.0% | 53.3% | 309ms | 903 tok | 36589 | 1129 | 58.7% |

> * `Overall` is a secondary house score that blends answer/retrieval/citation/latency. Use the raw metrics above as the primary comparison.

## Supermemory

> **Answer:** 63.3% · **Retrieval:** 100.0% · **Citation recall:** 71.4% · **Avg latency:** 1601ms · **Overall (secondary house score):** 71.5%

- Trials: **1**
- Questions: **30**
- Citation precision: **80.0%**
- P95 latency: **4740ms**
- Avg context: **6209 tok** (approx)

| Category | Answer | Retrieval | Citation | Avg latency | Avg context |
|---|---:|---:|---:|---:|---:|
| knowledge-update | 60.0% | 100.0% | 40.0% | 1677ms | 6938 tok |
| multi-session | 80.0% | 100.0% | 75.0% | 1337ms | 11457 tok |
| single-session-assistant | 80.0% | 100.0% | 100.0% | 2092ms | 1615 tok |
| single-session-preference | 0.0% | 100.0% | 40.0% | 1285ms | 5341 tok |
| single-session-user | 80.0% | 100.0% | 80.0% | 1129ms | 3422 tok |
| temporal-reasoning | 80.0% | 100.0% | 93.3% | 2090ms | 8478 tok |

### Highlights

**Strongest categories**
- **single-session-user** — answer 80.0%, retrieval 100.0%
- **single-session-preference** — answer 0.0%, retrieval 100.0%

**Weakest categories**
- **single-session-assistant** — answer 80.0%, retrieval 100.0%
- **temporal-reasoning** — answer 80.0%, retrieval 100.0%

### Retrieval diagnostics
- Questions: **30**
- Distinct retrieved step IDs: **52**
- Total retrievals across questions: **52**
- Zero-recall questions (no retrieved ID matched any expected source): **0**
**Top retrieved step IDs**
| Step ID | Hit count | Share of questions |
|---|---:|---:|
| `answer_0b1a0942_1` | 1 | 3.3% |
| `answer_0b1a0942_2` | 1 | 3.3% |
| `answer_1c6b85ea_1` | 1 | 3.3% |
| `answer_1c6b85ea_2` | 1 | 3.3% |
| `answer_280352e9` | 1 | 3.3% |
| `answer_2880eb6c_1` | 1 | 3.3% |
| `answer_2880eb6c_2` | 1 | 3.3% |
| `answer_2880eb6c_3` | 1 | 3.3% |
| `answer_2880eb6c_4` | 1 | 3.3% |
| `answer_355c48bb` | 1 | 3.3% |

### Misses

| Category | Prompt | Issue | Expected source(s) | Retrieved | Answer | Cited |
|---|---|---|---|---|---|---|
| knowledge-update | What was my personal best time in the charity 5K run? | answer miss | answer_a25d4a91_1, answer_a25d4a91_2 | answer_a25d4a91_2, answer_a25d4a91_1 | 27:12 | answer_a25d4a91_1 |
| knowledge-update | How many Korean restaurants have I tried in my city? | answer miss | answer_3f9693b7_1, answer_3f9693b7_2 | answer_3f9693b7_1, answer_3f9693b7_2 | unknown | — |
| knowledge-update | Where did Rachel move to after her recent relocation? | citation miss | answer_0b1a0942_1, answer_0b1a0942_2 | answer_0b1a0942_1, answer_0b1a0942_2 | suburbs | answer_0b1a0942_2 |
| knowledge-update | What was the amount I was pre-approved for when I got my mortgage from Wells Fargo? | citation miss | answer_3a6f1e82_1, answer_3a6f1e82_2 | answer_3a6f1e82_1, answer_3a6f1e82_2 | $400,000 | answer_3a6f1e82_2 |
| knowledge-update | How often do I attend yoga classes to help with my anxiety? | citation miss | answer_6a4f8626_1, answer_6a4f8626_2 | answer_6a4f8626_1, answer_6a4f8626_2 | three times a week | answer_6a4f8626_2 |
| multi-session | How many days did I spend on camping trips in the United States this year? | answer miss | answer_a8b4290f_2, answer_a8b4290f_3, answer_a8b4290f_1 | answer_a8b4290f_1, answer_a8b4290f_2, answer_a8b4290f_3 | unknown | — |
| multi-session | How much total money have I spent on bike-related expenses since the start of the year? | citation miss | answer_2880eb6c_3, answer_2880eb6c_1, answer_2880eb6c_4, answer_2880eb6c_2 | answer_2880eb6c_2, answer_2880eb6c_4, answer_2880eb6c_3, answer_2880eb6c_1 | $185 | answer_2880eb6c_2, answer_2880eb6c_4, answer_2880eb6c_1 |
| single-session-assistant | I'm checking our previous chat about the shift rotation sheet for GM social media agents. Can you remind me what was the rotation for Admon on a Sunday? | answer miss | answer_sharegpt_5Lzox6N_0 | answer_sharegpt_5Lzox6N_0 | 8 am - 4 pm (Day Shift), 12 pm - 8 pm (Afternoon Shift) on Friday | answer_sharegpt_5Lzox6N_0 |
| single-session-preference | Can you recommend some resources where I can learn more about video editing? | answer miss | answer_edb03329 | answer_edb03329 | You can learn more about video editing from the following resources: Adobe Premiere Pro's official documentation and tutorials, YouTube channels like Premiere Bro, Video Copilot, and Adobe Creative Cloud, online courses on platforms like Udemy, LinkedIn Learning, and Skillshare, and Premiere Pro communities and forums such as Reddit's r/premierepro and Adobe's Premiere Pro forum. | answer_edb03329 |
| single-session-preference | Can you suggest some accessories that would complement my current photography setup? | answer miss | answer_555dfb94 | answer_555dfb94 | Based on your current setup, here are some accessories that would complement your gear:

**For your Godox V1 Flash:**
* **Cases:** Godox V1 Hard Case ($25), Nanuk 915 Hard Case ($40), or Pelican 1060 Case ($50)
* **Pouches:** Think Tank Photo Flash Pouch ($15), Lowepro Flash Pouch ($20), or Vello Flash Pouch ($10)

**For charging your Sony A7R IV and other devices on the go:**
* **External Battery Packs:** Anker PowerCore 20000 PD ($60), Mophie Powerstation XXL ($100), or Jackery Titan S 20000 ($70)

**General Camera Accessories:**
* **Tripod:** Gitzo GT3543LS, a high-end, durable magnesium alloy tripod with a G-Lock Ultra leg-locking system (ideal for heavy-duty use)
* **Camera Bag:** Upgrading to a comfortable and durable bag from brands like Think Tank Photo, Lowepro, F-Stop Gear, Peak Design, or MindShift Gear | answer_555dfb94 |
| single-session-preference | Can you recommend some recent publications or conferences that I might find interesting? | answer miss | answer_d87a6ef8 | answer_d87a6ef8 | unknown | — |
| single-session-preference | Can you suggest a hotel for my upcoming trip to Miami? | answer miss | answer_d586e9cd | answer_d586e9cd | unknown | — |
| single-session-preference | Can you recommend some interesting cultural events happening around me this weekend? | answer miss | answer_9b182436 | answer_9b182436 | unknown | — |
| single-session-user | Where did I redeem a $5 coupon on coffee creamer? | answer miss | answer_d61669c7 | answer_d61669c7 | unknown | — |
| temporal-reasoning | What was the first issue I had with my new car after its first service? | answer miss | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | an issue with the car's GPS system | answer_4be1b6b4_3, answer_4be1b6b4_2 |

## Lobu

> **Answer:** 50.0% · **Retrieval:** 96.7% · **Citation recall:** 68.3% · **Avg latency:** 46ms · **Overall (secondary house score):** 68.4%

- Trials: **1**
- Questions: **30**
- Citation precision: **80.0%**
- P95 latency: **55ms**
- Avg context: **6069 tok** (approx)

| Category | Answer | Retrieval | Citation | Avg latency | Avg context |
|---|---:|---:|---:|---:|---:|
| knowledge-update | 40.0% | 100.0% | 50.0% | 46ms | 6845 tok |
| multi-session | 60.0% | 100.0% | 86.7% | 46ms | 11932 tok |
| single-session-assistant | 100.0% | 100.0% | 100.0% | 49ms | 1565 tok |
| single-session-preference | 0.0% | 80.0% | 20.0% | 43ms | 4325 tok |
| single-session-user | 60.0% | 100.0% | 60.0% | 44ms | 3376 tok |
| temporal-reasoning | 40.0% | 100.0% | 93.3% | 48ms | 8373 tok |

### Highlights

**Strongest categories**
- **single-session-user** — answer 60.0%, retrieval 100.0%
- **knowledge-update** — answer 40.0%, retrieval 100.0%

**Weakest categories**
- **single-session-preference** — answer 0.0%, retrieval 80.0%
- **single-session-assistant** — answer 100.0%, retrieval 100.0%

### Retrieval diagnostics
- Questions: **30**
- Distinct retrieved step IDs: **51**
- Total retrievals across questions: **51**
- Zero-recall questions (no retrieved ID matched any expected source): **1**
**Top retrieved step IDs**
| Step ID | Hit count | Share of questions |
|---|---:|---:|
| `answer_0b1a0942_1` | 1 | 3.3% |
| `answer_0b1a0942_2` | 1 | 3.3% |
| `answer_1c6b85ea_1` | 1 | 3.3% |
| `answer_1c6b85ea_2` | 1 | 3.3% |
| `answer_280352e9` | 1 | 3.3% |
| `answer_2880eb6c_1` | 1 | 3.3% |
| `answer_2880eb6c_2` | 1 | 3.3% |
| `answer_2880eb6c_3` | 1 | 3.3% |
| `answer_2880eb6c_4` | 1 | 3.3% |
| `answer_355c48bb` | 1 | 3.3% |

### Misses

| Category | Prompt | Issue | Expected source(s) | Retrieved | Answer | Cited |
|---|---|---|---|---|---|---|
| knowledge-update | What was my personal best time in the charity 5K run? | citation miss | answer_a25d4a91_1, answer_a25d4a91_2 | answer_a25d4a91_2, answer_a25d4a91_1 | 25:50 | answer_a25d4a91_2 |
| knowledge-update | How many Korean restaurants have I tried in my city? | answer miss | answer_3f9693b7_1, answer_3f9693b7_2 | answer_3f9693b7_1, answer_3f9693b7_2 | 3 | answer_3f9693b7_1 |
| knowledge-update | Where did Rachel move to after her recent relocation? | answer miss | answer_0b1a0942_1, answer_0b1a0942_2 | answer_0b1a0942_1, answer_0b1a0942_2 | Chicago | answer_0b1a0942_1 |
| knowledge-update | What was the amount I was pre-approved for when I got my mortgage from Wells Fargo? | answer miss | answer_3a6f1e82_1, answer_3a6f1e82_2 | answer_3a6f1e82_1, answer_3a6f1e82_2 | $350,000 | answer_3a6f1e82_1 |
| knowledge-update | How often do I attend yoga classes to help with my anxiety? | citation miss | answer_6a4f8626_1, answer_6a4f8626_2 | answer_6a4f8626_1, answer_6a4f8626_2 | Three times a week | answer_6a4f8626_2 |
| multi-session | How many days did I spend on camping trips in the United States this year? | answer miss | answer_a8b4290f_2, answer_a8b4290f_3, answer_a8b4290f_1 | answer_a8b4290f_3, answer_a8b4290f_2, answer_a8b4290f_1 | 3 | answer_a8b4290f_2 |
| multi-session | How many weeks did it take me to watch all the Marvel Cinematic Universe movies and the main Star Wars films? | answer miss | answer_86c505e7_1, answer_86c505e7_2 | answer_86c505e7_2, answer_86c505e7_1 | three and a half weeks | answer_86c505e7_1, answer_86c505e7_2 |
| single-session-preference | Can you recommend some resources where I can learn more about video editing? | answer miss | answer_edb03329 | answer_edb03329 | Based on the context, here are some resources to learn more about video editing:

* Adobe Premiere Pro's official documentation and tutorials
* YouTube channels like Premiere Bro, Video Copilot, and Adobe Creative Cloud
* Online courses on platforms like Udemy, LinkedIn Learning, and Skillshare
* Premiere Pro communities and forums, such as Reddit's r/premierepro and Adobe's Premiere Pro forum | answer_edb03329 |
| single-session-preference | Can you suggest some accessories that would complement my current photography setup? | answer miss | answer_555dfb94 | answer_555dfb94 | unknown | — |
| single-session-preference | Can you recommend some recent publications or conferences that I might find interesting? | retrieval miss | answer_d87a6ef8 | — | unknown | — |
| single-session-preference | Can you suggest a hotel for my upcoming trip to Miami? | answer miss | answer_d586e9cd | answer_d586e9cd | unknown | — |
| single-session-preference | Can you recommend some interesting cultural events happening around me this weekend? | answer miss | answer_9b182436 | answer_9b182436 | unknown | — |
| single-session-user | How long is my daily commute to work? | answer miss | answer_40a90d51 | answer_40a90d51 | unknown | — |
| single-session-user | Where did I redeem a $5 coupon on coffee creamer? | answer miss | answer_d61669c7 | answer_d61669c7 | unknown | — |
| temporal-reasoning | What was the first issue I had with my new car after its first service? | answer miss | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | answer_4be1b6b4_3, answer_4be1b6b4_2, answer_4be1b6b4_1 | an issue with the car's GPS system | answer_4be1b6b4_2, answer_4be1b6b4_3 |
| temporal-reasoning | Which event did I attend first, the 'Effective Time Management' workshop or the 'Data Analysis using Python' webinar? | answer miss | answer_1c6b85ea_1, answer_1c6b85ea_2 | answer_1c6b85ea_2, answer_1c6b85ea_1 | Effective Time Management | answer_1c6b85ea_2, answer_1c6b85ea_1 |
| temporal-reasoning | Which device did I got first, the Samsung Galaxy S22 or the Dell XPS 13? | answer miss | answer_5328c3c2_1, answer_5328c3c2_2 | answer_5328c3c2_2, answer_5328c3c2_1 | Dell XPS 13 | answer_5328c3c2_2, answer_5328c3c2_1 |

## Hindsight

> **Answer:** 43.3% · **Retrieval:** 100.0% · **Citation recall:** 53.3% · **Avg latency:** 309ms · **Overall (secondary house score):** 58.7%

- Trials: **1**
- Questions: **30**
- Citation precision: **66.7%**
- P95 latency: **1140ms**
- Avg context: **903 tok** (approx)

| Category | Answer | Retrieval | Citation | Avg latency | Avg context |
|---|---:|---:|---:|---:|---:|
| knowledge-update | 100.0% | 100.0% | 50.0% | 523ms | 1174 tok |
| multi-session | 40.0% | 100.0% | 43.3% | 406ms | 1630 tok |
| single-session-assistant | 40.0% | 100.0% | 40.0% | 213ms | 359 tok |
| single-session-preference | 0.0% | 100.0% | 60.0% | 193ms | 562 tok |
| single-session-user | 80.0% | 100.0% | 80.0% | 156ms | 533 tok |
| temporal-reasoning | 0.0% | 100.0% | 46.7% | 364ms | 1158 tok |

### Highlights

**Strongest categories**
- **single-session-user** — answer 80.0%, retrieval 100.0%
- **single-session-preference** — answer 0.0%, retrieval 100.0%

**Weakest categories**
- **knowledge-update** — answer 100.0%, retrieval 100.0%
- **multi-session** — answer 40.0%, retrieval 100.0%

### Retrieval diagnostics
- Questions: **30**
- Distinct retrieved step IDs: **52**
- Total retrievals across questions: **52**
- Zero-recall questions (no retrieved ID matched any expected source): **0**
**Top retrieved step IDs**
| Step ID | Hit count | Share of questions |
|---|---:|---:|
| `answer_0b1a0942_1` | 1 | 3.3% |
| `answer_0b1a0942_2` | 1 | 3.3% |
| `answer_1c6b85ea_1` | 1 | 3.3% |
| `answer_1c6b85ea_2` | 1 | 3.3% |
| `answer_280352e9` | 1 | 3.3% |
| `answer_2880eb6c_1` | 1 | 3.3% |
| `answer_2880eb6c_2` | 1 | 3.3% |
| `answer_2880eb6c_3` | 1 | 3.3% |
| `answer_2880eb6c_4` | 1 | 3.3% |
| `answer_355c48bb` | 1 | 3.3% |

### Misses

| Category | Prompt | Issue | Expected source(s) | Retrieved | Answer | Cited |
|---|---|---|---|---|---|---|
| knowledge-update | What was my personal best time in the charity 5K run? | citation miss | answer_a25d4a91_1, answer_a25d4a91_2 | answer_a25d4a91_2, answer_a25d4a91_1 | 25:50 | answer_a25d4a91_2 |
| knowledge-update | How many Korean restaurants have I tried in my city? | citation miss | answer_3f9693b7_1, answer_3f9693b7_2 | answer_3f9693b7_1, answer_3f9693b7_2 | 4 | answer_3f9693b7_2 |
| knowledge-update | Where did Rachel move to after her recent relocation? | citation miss | answer_0b1a0942_1, answer_0b1a0942_2 | answer_0b1a0942_2, answer_0b1a0942_1 | the suburbs | answer_0b1a0942_2 |
| knowledge-update | What was the amount I was pre-approved for when I got my mortgage from Wells Fargo? | citation miss | answer_3a6f1e82_1, answer_3a6f1e82_2 | answer_3a6f1e82_2, answer_3a6f1e82_1 | $400,000 | answer_3a6f1e82_2 |
| knowledge-update | How often do I attend yoga classes to help with my anxiety? | citation miss | answer_6a4f8626_1, answer_6a4f8626_2 | answer_6a4f8626_2, answer_6a4f8626_1 | three times a week | answer_6a4f8626_2 |
| multi-session | How many days did I spend on camping trips in the United States this year? | citation miss | answer_a8b4290f_2, answer_a8b4290f_3, answer_a8b4290f_1 | answer_a8b4290f_3, answer_a8b4290f_1, answer_a8b4290f_2 | 8 | answer_a8b4290f_1, answer_a8b4290f_2 |
| multi-session | How many weeks did it take me to watch all the Marvel Cinematic Universe movies and the main Star Wars films? | answer miss | answer_86c505e7_1, answer_86c505e7_2 | answer_86c505e7_2, answer_86c505e7_1 | about three and a half weeks | answer_86c505e7_2 |
| multi-session | How much total money have I spent on bike-related expenses since the start of the year? | answer miss | answer_2880eb6c_3, answer_2880eb6c_1, answer_2880eb6c_4, answer_2880eb6c_2 | answer_2880eb6c_3, answer_2880eb6c_2, answer_2880eb6c_1, answer_2880eb6c_4 | unknown | — |
| multi-session | How many hours in total did I spend driving to my three road trip destinations combined? | answer miss | answer_526354c8_1, answer_526354c8_2, answer_526354c8_3 | answer_526354c8_1, answer_526354c8_3, answer_526354c8_2 | unknown | — |
| single-session-assistant | I'm checking our previous chat about the shift rotation sheet for GM social media agents. Can you remind me what was the rotation for Admon on a Sunday? | answer miss | answer_sharegpt_5Lzox6N_0 | answer_sharegpt_5Lzox6N_0 | unknown | — |
| single-session-assistant | I'm going back to our previous conversation about the children's book on dinosaurs. Can you remind me what color was the scaly body of the Plesiosaur in the image? | answer miss | answer_sharegpt_YkWn1Ne_0 | answer_sharegpt_YkWn1Ne_0 | unknown | — |
| single-session-assistant | I'm planning to revisit Orlando. I was wondering if you could remind me of that unique dessert shop with the giant milkshakes we talked about last time? | answer miss | answer_ultrachat_480665 | answer_ultrachat_480665 | unknown | — |
| single-session-preference | Can you recommend some resources where I can learn more about video editing? | answer miss | answer_edb03329 | answer_edb03329 | YouTube channels (Premiere Bro, Video Copilot, Adobe Creative Cloud) and online course platforms (Udemy, LinkedIn Learning, Skillshare) | answer_edb03329 |
| single-session-preference | Can you suggest some accessories that would complement my current photography setup? | answer miss | answer_555dfb94 | answer_555dfb94 | Based on your Sony A7R IV setup, here are some recommended accessories:

**Flash Options:**
- Sony HVL-F60RM ($550)
- Sony HVL-F45RM ($350)
- Godox V1 ($250)
- Godox AD200 ($300)
- Profoto A1 ($1,095)
- Nissin Di700A ($230)

**Camera Bags (compatible with Sony cameras):**
- Think Tank Photo
- Lowepro
- F-Stop Gear
- Peak Design
- MindShift Gear | answer_555dfb94 |
| single-session-preference | Can you recommend some recent publications or conferences that I might find interesting? | answer miss | answer_d87a6ef8 | answer_d87a6ef8 | The assistant provided a curated list of 12 research papers covering advanced deep learning topics for medical image analysis, including multi-modal image fusion, transfer learning, domain adaptation, image registration, image synthesis using GANs, and explainable AI. | answer_d87a6ef8 |
| single-session-preference | Can you suggest a hotel for my upcoming trip to Miami? | answer miss | answer_d586e9cd | answer_d586e9cd | unknown | — |
| single-session-preference | Can you recommend some interesting cultural events happening around me this weekend? | answer miss | answer_9b182436 | answer_9b182436 | unknown | — |
| single-session-user | Where did I redeem a $5 coupon on coffee creamer? | answer miss | answer_d61669c7 | answer_d61669c7 | unknown | — |
| temporal-reasoning | What was the first issue I had with my new car after its first service? | answer miss | answer_4be1b6b4_2, answer_4be1b6b4_3, answer_4be1b6b4_1 | answer_4be1b6b4_2, answer_4be1b6b4_1, answer_4be1b6b4_3 | GPS issue | answer_4be1b6b4_3 |
| temporal-reasoning | Which event did I attend first, the 'Effective Time Management' workshop or the 'Data Analysis using Python' webinar? | answer miss | answer_1c6b85ea_1, answer_1c6b85ea_2 | answer_1c6b85ea_1, answer_1c6b85ea_2 | Effective Time Management | answer_1c6b85ea_1, answer_1c6b85ea_2 |
| temporal-reasoning | Which vehicle did I take care of first in February, the bike or the car? | answer miss | answer_b535969f_2, answer_b535969f_1 | answer_b535969f_2, answer_b535969f_1 | unknown | — |
| temporal-reasoning | Which device did I got first, the Samsung Galaxy S22 or the Dell XPS 13? | answer miss | answer_5328c3c2_1, answer_5328c3c2_2 | answer_5328c3c2_2, answer_5328c3c2_1 | Dell XPS 13 | answer_5328c3c2_2, answer_5328c3c2_1 |
| temporal-reasoning | How many days before the team meeting I was preparing for did I attend the workshop on 'Effective Communication in the Workplace'? | answer miss | answer_e936197f_1, answer_e936197f_2 | answer_e936197f_1, answer_e936197f_2 | unknown | — |