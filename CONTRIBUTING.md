# Contributing

## Results from your install

Runs on other installs, models, providers and Hindsight versions are the most
useful contribution, including runs that disagree with ours.

1. Follow [`METHODOLOGY.md`](METHODOLOGY.md). Note any deviation.
2. Add `results/<YYYY-MM-DD>-<slug>/README.md` with aggregates only, in the
   shape of [`results/2026-10-01-luna-vs-sol/`](results/2026-10-01-luna-vs-sol/README.md).
3. Never commit anything from `work/`: documents, facts, answers, pages,
   traces, judge pairs or verdict reasons. They contain your banks' content.
   Check `git status` before committing; `work/`, `config.env` and
   `server.env` are ignored on purpose.

## Code changes

- Python stays standard-library only, 3.10+. Shell stays bash.
- Paths, ports, models and banks come from `config.env` or arguments, never
  from constants in a script. Shared settings live in `bench_config.py` and
  `scripts/common.sh`.
- Anything that talks to the real install (`HINDSIGHT_SOURCE_URL`) is
  read-only. Anything that writes talks to a bench container.
- Keep the row formats stable: `reflect/compare.py`, `retain/compare.py`,
  `analysis/usage.py` and `followup/analyze.py` read what the runners write.
  If you change a format, update every reader in the same change.

Before opening a pull request:

```bash
python3 -m py_compile bench_config.py */*.py
for f in scripts/*.sh; do bash -n "$f"; done
for f in examples/*.json; do python3 -m json.tool "$f" >/dev/null; done
```

If the change touches analysis, run it against your own previous run and
check that the numbers it reproduces are unchanged.

## Good first contributions

- Token accounting for providers other than `openai-codex`, from the
  `llm_requests` table instead of the proxy.
- Repeating each follow-up bank build, to separate retain effects from reflect
  answer noise.
- A judge runner for Claude or another model, so pairwise judging is scripted
  end to end.
