"""Shared paths and endpoints for every script in this repository.

All settings come from environment variables, which scripts/common.sh loads
from config.env. Python scripts can also run on their own: anything unset falls
back to the defaults below.

    BENCH_WORK            working directory for inputs, runs, pairs and logs (default ./work)
    HINDSIGHT_BENCH_URL   the throwaway bench instance (default http://localhost:18888)
    HINDSIGHT_SOURCE_URL  the instance inputs are exported from, read-only (default http://localhost:8888)
    BENCH_CONTAINER       the bench container name, for recording its environment (default hindsight-bench)

Stdlib only.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = Path(os.environ.get("BENCH_WORK", ROOT / "work")).expanduser()
BENCH_URL = os.environ.get("HINDSIGHT_BENCH_URL", "http://localhost:18888")
SOURCE_URL = os.environ.get("HINDSIGHT_SOURCE_URL", "http://localhost:8888")
CONTAINER = os.environ.get("BENCH_CONTAINER", "hindsight-bench")

# Documents retained before a strategy was recorded carry none. Map the contexts the
# Hindsight coding-agent plugin writes to the strategy each would get today; anything
# else falls back to the bank's retain_default_strategy.
STRATEGY_BY_CONTEXT = {
    "coding agent session": "conversation",
    "developer chat": "conversation",
    "ingested document": "document",
    "hindsight codebase-survey baseline": "survey",
}


def strategy_for(doc: dict) -> str | None:
    if doc.get("strategy"):
        return doc["strategy"]
    ctx = doc.get("context") or ""
    if ctx.startswith("initiative marker"):
        return "document"
    return STRATEGY_BY_CONTEXT.get(ctx)


REFLECT_RUNS = WORK / "runs" / "reflect"
RETAIN_RUNS = WORK / "runs" / "retain"
FOLLOWUP = WORK / "followup"
