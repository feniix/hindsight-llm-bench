# hindsight-llm-bench

Scripts and methodology for comparing LLMs on each operation of a self-hosted
[Hindsight](https://github.com/vectorize-io/hindsight) agent-memory install:
retain (fact extraction), knowledge-page refresh, reflect (question
answering), and, in a follow-up, consolidation and answer accuracy
downstream of retain.

Hindsight can run each operation on a different model. A single "which model
is better" benchmark hides that choice. These scripts measure each operation
separately, on a throwaway copy of a real install, with blind pairwise judging
and exact token accounting, so you can choose a model per operation.

The first comparison run with them, gpt-5.6-luna against gpt-6-luna and
gpt-6.1-sol on Hindsight 0.10.2, is summarized in
[`results/2026-10-01-luna-vs-sol/`](results/2026-10-01-luna-vs-sol/README.md).
In short, gpt-6.1-sol extracted significantly better facts at retain, and banks
built from them answered a gold question set better. On reflect and page
refresh it was slower with no quality gain, so gpt-5.6-luna stayed for those.

## What it measures

| Phase | Operation | How | Compared on |
|---|---|---|---|
| A | Reflect | Real reflect queries against restored banks | latency, LLM calls, tokens, blind answer quality |
| A | Page refresh | `dry-run-refresh` on real knowledge pages | latency, tokens, blind page quality |
| B | Retain | `dry-run-extract` on real documents | latency, tokens, blind fact-list quality, per-fact scores, coverage |
| Follow-up | Retain → consolidation → pages → reflect | Whole corpora retained into fresh banks, once per retain model | consolidation cost, page quality, gold Q&A accuracy |

[`METHODOLOGY.md`](METHODOLOGY.md) explains the design, the statistics, the
judging, and the known limitations.

## Requirements

- Docker, and a Hindsight install you want to measure (0.10.2 or later: the
  dry-run endpoints are needed).
- A `hindsight-admin backup` of that install, restored into the bench.
- Python 3.10+. All scripts use the standard library only.
- For token counts: the `openai-codex` provider, through the logging proxy in
  `proxy/`. Other providers record tokens in Hindsight's own `llm_requests`
  table, which the trace export keeps.
- For scoring: a `GEMINI_API_KEY` (second judge, reference lists, gold
  questions) and a `TYPESAFE_API_KEY` (per-fact scoring and grading with Jev).
  Pairwise judging works with any judge that writes the verdict files
  described in the rubrics.

The bench shares whatever quota your provider login has. Our main run (three
models) made about 700 proxied LLM calls reading about 10.5M input tokens; the
follow-up about 1,100 calls.

## Quickstart

```bash
cp config.example.env config.env          # paths, ports, image, containers
cp server.example.env server.env          # match your real install's settings
# hindsight-admin backup of your install -> work/backup/banks.zip
CODEX_HOME=./work/codex-auth codex login  # a separate login for the bench

# 1. Inputs, read-only from your install (GET requests only)
python3 prepare/export.py docs  --bank coding-agent::my-repo --bank coding-agent::other --per-bank 3
python3 prepare/export.py pages --bank coding-agent::my-repo --bank coding-agent::other --per-bank 3
python3 prepare/export.py queries --pages work/pages.json   # then add first-prompt queries by hand
python3 prepare/plan.py --model base=gpt-5.6-luna --model cand=gpt-6.1-sol --base base \
  --queries work/queries.json --pages work/pages.json --docs work/docs.json

# 2. Measure (proxy in one terminal, driver in another)
python3 proxy/codex_proxy.py 8090
scripts/run-main.sh

# 3. Analyze
python3 reflect/compare.py base-b1,...,base-b6 cand-b1,...,cand-b6 --pairs work/pairs/reflect
python3 retain/compare.py  base-b1,...,base-b6 cand-b1,...,cand-b6 --pairs work/pairs/retain --docs work/docs.json
python3 analysis/usage.py work/usage.jsonl base,cand --base base
# judge the pairs (see METHODOLOGY.md), then:
python3 reflect/compare.py ... --verdicts work/pairs/reflect --failures-as-losses
```

The downstream follow-up has its own driver, `scripts/run-followup.sh`. Its
inputs come from `prepare/export.py corpus` and `followup/gold.py`; see
[`METHODOLOGY.md`](METHODOLOGY.md#downstream-follow-up).

## Layout

| Path | Purpose |
|---|---|
| `prepare/` | Export inputs from a live install (read-only) and write the block plan |
| `scripts/` | Bench container lifecycle and the two end-to-end drivers |
| `proxy/` | Logging pass-through proxy for exact Codex token usage |
| `reflect/` | Reflect and page-refresh runners, comparison, judge rubric |
| `retain/` | Retain runner, comparison, Gemini judge, per-fact scoring, rubric |
| `followup/` | Downstream follow-up driver, gold-set writer, analysis |
| `analysis/` | Token attribution per item, per model, per phase |
| `examples/` | Synthetic examples of every input file format |
| `results/` | Aggregate results of published runs |
| `bench_config.py` | Shared paths and endpoints, all from the environment |

## Privacy

Inputs, runs, judge pairs and traces contain your banks' content: session
transcripts, documents, answers. All of it goes under `work/`, which is
git-ignored. The scoring steps send documents and answers to Google (Gemini)
and TypeSafe (Jev) under your own keys. Share results as aggregates, the way
`results/` does.

## Contributing

Runs on other installs, models and providers are the most useful
contribution. [`CONTRIBUTING.md`](CONTRIBUTING.md) explains how to submit
results and changes. Agents can start from [`llms.txt`](llms.txt).

## Licence

[MIT](LICENSE)
