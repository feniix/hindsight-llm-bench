"""Run the reflect benchmark once and save timings plus answers.

    python3 reflect/run.py LABEL [--queries QUERIES.json] [--set KEY=JSON ...] [--only KIND] [--limit N] [--ids r00,r03]
    python3 reflect/run.py LABEL --variant base \
        --variant noent:reflect_search_observations_include_entities=false

Compare variants within ONE run, never across runs: two runs of the same
configuration 9 hours apart differed by 22% (ratio 1.22 [1.02, 1.51]) because the
LLM provider's speed drifts. With --variant NAME[:KEY=JSON,...] (repeatable),
each query runs under every variant back to back, in random order, and each
variant is written to runs/LABEL-NAME.json.

On macOS, run long jobs under `caffeinate -i`: if the machine sleeps mid-run,
the Docker VM freezes and every remaining query hangs until wake.

Each query in QUERIES.json (default $BENCH_WORK/queries.json) is sent to POST /v1/default/banks/{bank}/reflect with
the parameters its real caller uses (params), then any --set overrides, e.g.
--set reflect_search_observations_include_entities=false. Queries run one at a
time so they don't contend with each other; background page refreshes still
can, so the run records each call's per-phase trace.

Writes $BENCH_WORK/runs/reflect/LABEL.json. Server-side settings (the model,
reasoning effort, iteration and context limits) are environment variables:
change them on the bench container, restart it, and run under a new label. The run
records the container's HINDSIGHT_API_REFLECT_* / LLM_* environment for that
reason. A server-side setting cannot be interleaved with --variant, so compare
two of them in alternating blocks (--ids picks each block's queries, restart
between blocks) and hand compare.py the blocks as one comma-joined label per
side. Stdlib only.
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench_config as cfg  # noqa: E402

BASE = cfg.BENCH_URL
CONTAINER = cfg.CONTAINER
API = f"{BASE}/v1/default/banks"


def container_env() -> dict[str, str]:
    try:
        out = subprocess.run(["docker", "exec", CONTAINER, "env"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    keep = ("HINDSIGHT_API_REFLECT", "HINDSIGHT_API_LLM", "HINDSIGHT_API_MENTAL_MODEL", "HINDSIGHT_API_RERANKER")
    return dict(line.split("=", 1) for line in out.splitlines() if line.startswith(keep) and "KEY" not in line)


def api_version() -> str | None:
    try:
        with urllib.request.urlopen(f"{BASE}/version", timeout=10) as resp:
            return json.load(resp).get("api_version")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def reflect(bank: str, body: dict) -> tuple[float, dict | None, str | None]:
    url = f"{API}/{urllib.parse.quote(bank, safe='')}/reflect"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=420) as resp:
            return time.monotonic() - start, json.load(resp), None
    except urllib.error.HTTPError as e:
        return time.monotonic() - start, None, f"HTTP {e.code}: {e.read()[:300]!r}"
    except (urllib.error.URLError, TimeoutError) as e:
        return time.monotonic() - start, None, repr(e)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label")
    parser.add_argument("--queries", type=Path, default=cfg.WORK / "queries.json")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=JSON", help="per-request override")
    parser.add_argument("--only", help="run only queries of this kind")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", help="run only these query ids, comma-separated (e.g. r00,r03,r07)")
    parser.add_argument("--variant", action="append", default=[], metavar="NAME[:KEY=JSON,...]", help="interleaved variant")
    args = parser.parse_args()

    overrides = {}
    for item in args.set:
        key, _, raw = item.partition("=")
        overrides[key] = json.loads(raw)

    queries = json.loads(args.queries.read_text())
    if args.only:
        queries = [q for q in queries if q["kind"] == args.only]
    if args.ids:
        wanted = set(args.ids.split(","))
        queries = [q for q in queries if q["id"] in wanted]
        if missing := wanted - {q["id"] for q in queries}:
            sys.exit(f"unknown query ids: {sorted(missing)}")
    if args.limit:
        queries = queries[: args.limit]

    variants = {}
    for spec in args.variant:
        name, _, rest = spec.partition(":")
        v = dict(overrides)
        for item in filter(None, rest.split(",")):
            key, _, raw = item.partition("=")
            v[key] = json.loads(raw)
        variants[name] = v
    if not variants:
        variants = {"": overrides}

    rng = random.Random(0)
    results = {name: [] for name in variants}
    for n, q in enumerate(queries, 1):
        order = list(variants)
        rng.shuffle(order)
        for name in order:
            body = {"query": q["query"], **q["params"], **variants[name], "include": {"tool_calls": {}, "facts": {}}}
            started = datetime.now().astimezone().isoformat()
            seconds, resp, error = reflect(q["bank"], body)
            ended = datetime.now().astimezone().isoformat()
            trace = (resp or {}).get("trace") or {}
            llm = [c.get("duration_ms", 0) / 1000 for c in trace.get("llm_calls") or []]
            tools = [(c.get("tool"), c.get("duration_ms", 0) / 1000) for c in trace.get("tool_calls") or []]
            results[name].append(
                {
                    "id": q["id"],
                    "kind": q["kind"],
                    "bank": q["bank"],
                    "started": started,
                    "ended": ended,
                    "seconds": round(seconds, 2),
                    "llm_calls_s": [round(x, 2) for x in llm],
                    "final_llm_s": round(llm[-1], 2) if llm else None,
                    "tool_calls": [[t, round(s, 3)] for t, s in tools],
                    "answer": (resp or {}).get("text"),
                    "sources": [
                        {"type": kind, "id": src.get("id"), "text": src.get("text")}
                        for kind, items in ((resp or {}).get("based_on") or {}).items()
                        if isinstance(items, list)
                        for src in items
                    ],
                    "error": error,
                }
            )
            status = error or (f"{len(llm)} LLM calls, final {llm[-1]:.1f}s" if llm else "no trace")
            tag = f" {name}" if name else ""
            print(f"[{n:2d}/{len(queries)}] {q['id']}{tag} {q['kind']:13s} {seconds:6.1f}s  {status}", file=sys.stderr)

    env = container_env()
    version = api_version()
    for name, rows in results.items():
        label = f"{args.label}-{name}" if name else args.label
        out = cfg.REFLECT_RUNS / f"{label}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        meta = {"label": label, "at": datetime.now().astimezone().isoformat(), "overrides": variants[name], "api_version": version, "env": env}
        out.write_text(json.dumps({"meta": meta, "results": rows}, indent=1, ensure_ascii=False))
        ok = sorted(r["seconds"] for r in rows if not r["error"])
        if ok:
            print(f"{label}: {len(ok)}/{len(rows)} ok, median {ok[len(ok) // 2]:.1f}s, p90 {ok[int(len(ok) * 0.9)]:.1f}s -> {out}")
        else:
            print(f"{label}: 0/{len(rows)} ok -> {out}")

if __name__ == "__main__":
    main()
