# gpt-5.6-luna vs gpt-6-luna vs gpt-6.1-sol on Hindsight 0.10.2

Run 2026-09-30 (main) and 2026-10-01 (follow-up) on a throwaway bench restored
from a backup of a Hindsight install used as coding-agent memory through the
Hindsight coding-agent plugin (26 banks, about 1.85M rows).
Provider `openai-codex` on a ChatGPT subscription, reasoning effort `low` for
every model. Baseline: gpt-5.6-luna (`g56`). Candidates: gpt-6-luna (`g6l`),
gpt-6.1-sol (`sol`). Six blocks per phase, each model in each position twice.

Everything below is reproduced by the scripts in this repository from the
run's files, which are not published because they contain bank content.

## Latency against gpt-5.6-luna

Geometric mean of per-item ratios, 95% bootstrap interval. `*` excludes 1.

| Operation | Items | gpt-6-luna | gpt-6.1-sol |
|---|---|---|---|
| Reflect | 28 queries | 0.58 [0.51, 0.66]* | 2.55 [2.02, 3.26]* (25 completed) |
| Page refresh | 12 pages | 0.67 [0.58, 0.79]* | 1.86 [1.64, 2.21]* |
| Retain | 24 documents | 0.72 [0.52, 0.96]* | 1.64 [1.40, 1.94]* |

Failures: gpt-6.1-sol exceeded the 300 s reflect limit on 3 of 28 queries
(HTTP 504), and one of its page refreshes had a call cut at the 120 s refresh
limit (the page still completed, in 251 s). The luna models had no failures.

## Tokens, whole run (proxy, exact)

| | gpt-5.6-luna | gpt-6-luna | gpt-6.1-sol |
|---|---|---|---|
| Input | 2.06M | 1.76M | 6.70M |
| Output | 117k | 65k | 135k |
| Reasoning | 9.3k | 1.4k | 0.9k |
| Reflect LLM calls per query (median) | 2 | 2 | 5 |
| Reflect input tokens per query (median) | 29k | 23k | 111k |

8 of 707 proxied calls lost their usage block when a stream was cut at a
deadline (6 of them gpt-6.1-sol's).

## Quality against gpt-5.6-luna (blind, better / tie / worse)

| Operation | Judge | gpt-6-luna | gpt-6.1-sol |
|---|---|---|---|
| Reflect | Claude | 7 / 9 / 12 (p 0.36) | 10 / 3 / 15 (p 0.42), timeouts as losses |
| Page refresh | Claude | 3 / 2 / 7 (p 0.34) | 4 / 2 / 6 (p 0.75) |
| Retain | Claude | 10 / 7 / 7 (p 0.63) | **17 / 5 / 2 (p 0.001)** |
| Retain | Gemini 3.1 Pro | 14 / 4 / 6 (p 0.12) | **15 / 6 / 3 (p 0.008)** |

## Retain, per fact (Jev) and coverage

194 reference items across the 24 documents (Gemini, written blind to every
model's facts).

| | gpt-5.6-luna | gpt-6-luna | gpt-6.1-sol |
|---|---|---|---|
| Facts per document | 5.6 | 4.4 | 6.8 |
| Supported (mean probability) | 0.97 | 0.97 | 0.96 |
| Contradicted facts | 0 / 134 | 1 / 105 | 3 / 164 |
| Relevant / chatter / redundant | 0.79 / 0.18 / 0.18 | 0.79 / 0.18 / 0.18 | 0.79 / 0.17 / 0.20 |
| Stands alone (0–2) | 1.72 | 1.71 | 1.77 |
| Coverage | 0.84 | 0.74 | 0.86 |

Coverage change per document vs gpt-5.6-luna: gpt-6-luna −0.10 [−0.21, −0.01],
gpt-6.1-sol +0.02 [−0.02, +0.06]. gpt-6-luna returned no facts for 2 documents.

## Follow-up: retain model → downstream

Two corpora (33 documents / 721k characters and 32 documents / 553k
characters), each retained once with gpt-5.6-luna and once with gpt-6.1-sol;
consolidation, pages and reflect on gpt-5.6-luna. 50 gold questions (Gemini).

| Candidate-retained vs base-retained | Corpus 1 | Corpus 2 |
|---|---|---|
| Facts stored at retain | 279 → 321 (+15%) | 233 → 286 (+23%) |
| Observations | 90 → 108 (+20%) | 90 → 127 (+41%) |
| Consolidation LLM calls | +21% | +16% |
| Consolidation input tokens | +17% | +26% |
| Consolidation LLM time | +33% | +30% |
| Consolidation failures | 0 / 0 | 0 / 0 |
| Gold Q&A, Jev | 0.74 → 0.92 | 0.62 → 0.78 |
| Gold Q&A, Gemini | 0.80 → 0.94 | 0.70 → 0.80 |

Pooled over 50 questions, candidate minus base:

| Grader | Difference [95%] | Better / same / worse | Sign test p |
|---|---|---|---|
| Jev | +0.170 [+0.050, +0.290] | 16 / 28 / 6 | 0.052 |
| Gemini | +0.120 [+0.010, +0.230] | 11 / 35 / 4 | 0.118 |
| Mean of both | +0.145 [+0.035, +0.260] | 15 / 29 / 6 | 0.078 |

By question type (mean of both graders): current state 6 / 9 / 1, specific
fact 5 / 13 / 0, why 4 / 7 / 5. Wrong answers (both graders): 3 from
base-retained banks, 0 from candidate-retained banks. The graders agreed on
90 of 100 answers. Knowledge pages, judged blind: 4 better / 8 tie / 4 worse.

## Deviations

- The copied bank configs had `enable_auto_consolidation: true`, so the four
  follow-up banks consolidated concurrently. Cost was attributed per bank from
  the traces (1,061 of 1,076 calls matched). Wall-clock time per bank is not
  comparable. `followup/fu.py setup` now forces it off.
- Corpus 1 includes transcripts of the session that ran this evaluation.
- Each follow-up bank was built once.

## Decision

Retain on gpt-6.1-sol; consolidation, page refresh and reflect on
gpt-5.6-luna. As of 2026-10-01 this is planned, not yet deployed.
