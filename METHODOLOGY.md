# Methodology

How the comparison is designed, what each step measures, and how the results
are computed. The numbers quoted as examples are from the first published run
([`results/2026-10-01-luna-vs-sol/`](results/2026-10-01-luna-vs-sol/README.md)).

## Principles

1. **Measure each operation on its own.** Hindsight makes LLM calls in four
   operations, and each can use its own model:

   | Operation | Setting | Runs |
   |---|---|---|
   | Retain | `HINDSIGHT_API_RETAIN_LLM_MODEL` | on ingest, in the background |
   | Consolidation | `HINDSIGHT_API_CONSOLIDATION_LLM_MODEL` | in the background, after retain |
   | Page refresh | `HINDSIGHT_API_MENTAL_MODEL_REFRESH_LLM_MODEL`, else the reflect model | in the background, after consolidation |
   | Reflect | `HINDSIGHT_API_REFLECT_LLM_MODEL` | while the caller waits |

   A run changes exactly one of these settings. Everything else, including
   reasoning effort, stays fixed.

2. **Never touch the real install.** The bench is a separate container on its
   own volume, restored from a `hindsight-admin backup`, with its own provider
   login. The only contact with the real install is the backup and the
   read-only `GET` requests in `prepare/export.py`.

3. **Read identical memory.** The bench runs with the worker off, so no
   consolidation or refresh changes the banks between runs. The container is
   recreated before every run, with only the model setting changed.

4. **Balance the order.** Provider speed drifts over hours. Two runs of the
   same configuration nine hours apart differed by 22% in our setup. Every
   block runs every model once, and across blocks each model takes each
   position equally often (`prepare/plan.py`). Items are dealt across blocks
   so each block mixes query kinds and document sizes.

5. **Judge blind, in pairs, against a baseline.** Every candidate output is
   compared with the baseline's output on the same item, with sides
   randomized and the key kept apart from the judges.

6. **Count everything that failed.** A timeout is a result. Latency is
   compared on items both models completed; quality can count candidate
   failures as losses (`--failures-as-losses`) so the slowest items do not
   disappear from the record.

## Inputs

`prepare/export.py` builds every input from the real install. Each file's
format is shown in `examples/`.

- **Reflect queries** (`queries.json`). Each query has a kind and the request
  parameters its real caller uses. The published run used 28 queries:

  | Kind | Count | Parameters | What it represents |
  |---|---|---|---|
  | `page_refresh` | 12 | `budget: mid`, `max_tokens: 4096`, `fact_types: [observation]`, `exclude_mental_models: true` | a knowledge page's standing question, answered the way a refresh answers it |
  | `page_survey` | 6 | same as `page_refresh` | broad survey questions (all instances of a category) |
  | `first_prompt` | 9 | `budget: low` | what a coding agent asks at the start of a session |
  | `agent_reflect` | 1 | `budget: high` | a deliberate deep question from an agent |

  `export.py queries` writes the `page_refresh` set from the pages. Write the
  other kinds by hand from real usage.

- **Knowledge pages** (`pages.json`). Run `export.py pages --screen`
  against the restored bench. It keeps only pages whose dry-run refresh
  actually calls the LLM. A delta-mode page with nothing new since its last
  refresh returns without an LLM call and measures nothing. In the published
  run every kept page ran in full mode.

- **Retain documents** (`docs.json`). These are real documents sampled per
  bank in a size range (3,000 to 38,500 characters in the published run, 24
  documents from 8 banks). Each record carries its retain strategy and the
  mission, extraction mode and custom instructions that strategy resolves to,
  so the dry run and the judges see what production would apply. Documents
  retained before Hindsight recorded a strategy get one inferred from their
  context (`bench_config.STRATEGY_BY_CONTEXT`), else the bank default.

## Phase A: reflect and page refresh

For each block and each model in the block's order:

1. `scripts/up.sh MODEL` recreates the bench with
   `HINDSIGHT_API_REFLECT_LLM_MODEL=MODEL`, then waits for health and
   `SETTLE_SECONDS`.
2. `reflect/run.py` sends the block's queries one at a time to
   `POST /banks/{bank}/reflect` with `include.facts` and `include.tool_calls`,
   so every answer carries the sources it used and a per-call timing trace.
3. `reflect/refresh.py` sends the block's pages to
   `POST /banks/{bank}/mental-models/{id}/dry-run-refresh`. This runs the
   production refresh pipeline and skips the writes. With no
   `MENTAL_MODEL_REFRESH_LLM_*` override, refresh runs on the reflect model, so
   both are measured under the same switch.
4. The driver aborts if a run has zero successful items, and saves
   `/metrics` and the server log per run.

Production limits apply on the bench as they would in production. In 0.10.2,
reflect allows 180 s per LLM call (`HINDSIGHT_API_REFLECT_LLM_TIMEOUT`) and
300 s per request, and page refresh allows 120 s per call. A model that
exceeds them fails the item, and that failure is part of the result.

## Phase B: retain

The same loop, switching `HINDSIGHT_API_RETAIN_LLM_MODEL` and enabling
`HINDSIGHT_API_ENABLE_DRY_RUN_EXTRACT=true`. `retain/run.py` sends each
document to `POST /banks/{bank}/memories/dry-run-extract` with its strategy.
The endpoint runs the bank's real extraction and returns the candidate facts
without storing anything.

## Instrumentation

- **Exact tokens.** Hindsight's `openai-codex` provider does not read the usage
  block Codex sends in its final `response.completed` event. It estimates
  output tokens from text length and records no reasoning tokens.
  `proxy/codex_proxy.py` sits between Hindsight and Codex
  (`HINDSIGHT_API_LLM_BASE_URL`), streams responses through unchanged, and
  logs each call's model, timing, time to first byte, and usage (input,
  cached, output, reasoning). `proxy/codex_usage_probe.py` confirms your
  endpoint returns usage before you rely on it.
- **Attribution.** `analysis/usage.py` assigns each proxy row to the bench
  item whose `[started, ended]` window contains it. Items run one at a time
  with the worker off, so every call in a window belongs to that item. Calls
  outside every window, such as the connection check each container start
  makes, are left out.
- **Traces.** With `HINDSIGHT_API_LLM_TRACE_MAX_CHARS` raised, the
  `llm_requests` table keeps full prompts and outputs. The drivers export the
  rows written since the run started.

## Statistics

- **Latency** is the per-item ratio candidate/baseline on items both completed,
  summarized as the geometric mean with a 95% bootstrap interval (20,000
  resamples of items, seed 0). An interval that excludes 1 is marked `*`.
- **Quality** is better / tie / worse from the candidate's side, with a
  two-sided sign test over the decisive pairs. Ties are excluded from the test,
  not split.
- **Follow-up accuracy** scores each answer correct = 1, partial = 0.5,
  wrong or declined = 0. The per-question difference between the two banks of
  a corpus is pooled across corpora, with a 95% bootstrap interval of the mean
  and a sign test.

With 24 to 28 items per comparison, only large effects reach significance.
Report "no significant difference" as such, not as equivalence.

## Judging

### Pairwise, blind

`reflect/compare.py --pairs DIR` and `retain/compare.py --pairs DIR --docs`
write one file per item with the two outputs as `A` and `B` in random order.
The key goes to `DIR/key.json`. A judge reads a pair file and its rubric
(`reflect/rubric.md`, `retain/rubric.md`) and writes
`<id>.verdict.json` as `{"winner": "A" | "B" | "tie", "why": "..."}`.
`--verdicts DIR` unblinds and scores.

- The reflect rubric ranks grounding first. When the pair carries each
  answer's sources, a specific claim that none of its own sources support
  counts as invented.
- The retain rubric judges each fact list against the source document and the
  mission it was extracted under: faithfulness, then coverage of what the
  mission asks to keep, then usefulness as standalone memory. More facts is
  not better in itself.

In the published run, Claude (Opus) judged every pair as Claude Code
subagents, each given a batch of three to six pair files and the rubric, and
none given the key. Any judge that writes the verdict files works.
`retain/gemini_judge.py` runs Gemini as a second, independent judge family
on the same retain pairs.

### Per-fact scoring and coverage (retain)

A pairwise verdict says which list is better, not why. `retain/factscore.py`
adds two measures:

1. **Reference list (Gemini).** For each document, a list of the items its
   mission asks to keep, written without seeing any model's facts. It is a
   fixed denominator for coverage, the same for every model.
2. **Per-fact judgments (TypeSafe Jev).** For every extracted fact: supported,
   contradicted or not addressed by the document; relevant to the mission;
   transient chatter; redundant with another fact; how well it stands alone
   (0 to 2). For every reference item: whether any fact captures it.

Together these separate "more facts" into coverage gained versus noise added.

## Downstream follow-up

Retain quality is an intermediate result. The follow-up measures whether a
better retain model produces a better bank.

1. `prepare/export.py corpus` exports whole banks: every document in date order
   with its retain parameters, the bank's page definitions, and its config.
2. `followup/gold.py` has Gemini write gold questions per corpus from the raw
   documents, without seeing any extracted facts. About a third ask for the
   current state of something that changed, a third ask why, a third ask for
   a specific fact.
3. `scripts/run-followup.sh` (plan in `work/followup/plan.json`, see
   `examples/followup-plan.example.json`):
   - creates two fresh banks per corpus with the source bank's config, and
     auto-consolidation forced off;
   - retains the corpus into each, once per retain model, in ABBA order;
   - turns the worker on, then consolidates, rebuilds pages and answers the
     gold questions (reflect, `budget: low`), all on the baseline model;
   - exports proxy usage and `llm_requests`.
4. `followup/analyze.py`:
   - `grade` grades each answer on its own against its gold answer, never
     beside the other bank's answer, with Jev and with Gemini independently.
   - `pages` writes blind page pairs for judging.
   - `report` prints cost per bank and operation, bank sizes, and accuracy per
     corpus, grader and question type, plus pooled across corpora.

   Cost is attributed by matching each `llm_requests` row (which carries bank
   and operation) to the proxy row with the same model and the nearest start
   time within 3 s (which carries exact tokens).

## Known limitations

- **Single build per bank** in the follow-up. Reflect answers vary from run to
  run. Rebuilding each bank more than once would separate retain effects from
  answer noise.
- **Model judges.** All quality judgments come from LLMs. Using two judge
  families and checking their agreement is the main safeguard.
- **Auto-consolidation override.** A bank's own `enable_auto_consolidation:
  true` overrides the server-wide `false`. In the published follow-up, the
  copied configs had it on, so all four banks consolidated at once. That kept
  conditions equal but made per-bank wall-clock time incomparable.
  `followup/fu.py setup` now forces it off.
- **Self-reference.** If a corpus contains transcripts of the session that ran
  the benchmark, gold questions can be about the benchmark itself. Exclude
  such documents or report it.
- **One reasoning effort.** Results at effort `low` may not hold at other
  settings.
- **Shared quota.** The bench draws on the same provider quota as your real
  install. Watch for rate limits during long runs.

## Reporting a run

Add `results/<date>-<slug>/README.md` with:

- the Hindsight version, the models, and the reasoning effort;
- the item counts;
- latency ratios with intervals;
- verdict splits with p-values;
- failure counts;
- token totals;
- any deviation from this document.

Aggregates only: no documents, answers, facts or judge reasons that quote
bank content.
