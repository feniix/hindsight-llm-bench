"""Join bench rows with the Codex proxy's usage log: exact tokens per item, per model.

    python3 analysis/usage.py USAGE.jsonl base,cand1,cand2 [--base base]

Reads every reflect run ($BENCH_WORK/runs/reflect/TAG-bN.json), page dry-run
refresh run (TAG-pg-bN.json) and retain run ($BENCH_WORK/runs/retain/TAG-bN.json) for each
model tag, and attributes each proxy call (usage.jsonl rows from
codex_proxy.py) to the bench item whose [started, ended] window contains it.
Items run one at a time with the worker off, so every call in a window belongs
to that item. Reports per phase and model: calls per item, input / cached /
output / reasoning tokens (median per item and total), reasoning share of
output, time to first byte, and the paired per-item ratio against --base for
output and reasoning tokens. Stdlib only.
"""

import argparse
import json
import sys
import math
import random
import statistics
from bisect import bisect_left, bisect_right
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench_config as cfg  # noqa: E402

PHASES = {
    "reflect": (cfg.REFLECT_RUNS, "{tag}-b{n}.json"),
    "page refresh": (cfg.REFLECT_RUNS, "{tag}-pg-b{n}.json"),
    "retain": (cfg.RETAIN_RUNS, "{tag}-b{n}.json"),
}


def ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()


def load_usage(path: Path) -> tuple[list[float], list[dict]]:
    rows = sorted((json.loads(l) for l in path.read_text().splitlines() if l.strip()), key=lambda r: r["ts"])
    return [ts(r["ts"]) for r in rows], rows


def items(phase: str, tag: str) -> dict[str, dict]:
    d, pattern = PHASES[phase]
    out = {}
    for n in range(1, 13):
        p = d / pattern.format(tag=tag, n=n)
        if p.exists():
            out.update({r["id"]: r for r in json.loads(p.read_text())["results"] if "started" in r})
    return out


def attribute(row: dict, keys: list[float], usage: list[dict]) -> dict:
    lo, hi = bisect_left(keys, ts(row["started"])), bisect_right(keys, ts(row["ended"]))
    calls = usage[lo:hi]
    u = [c.get("usage") or {} for c in calls]
    return {
        "calls": len(calls),
        "errors": sum(1 for c in calls if c.get("error") or (c.get("status") or 200) >= 400),
        "input": sum(x.get("input_tokens", 0) for x in u),
        "cached": sum((x.get("input_tokens_details") or {}).get("cached_tokens", 0) for x in u),
        "output": sum(x.get("output_tokens", 0) for x in u),
        "reasoning": sum((x.get("output_tokens_details") or {}).get("reasoning_tokens", 0) for x in u),
        "ttfb": [c["ttfb"] for c in calls if c.get("ttfb") is not None],
        "models": sorted({c.get("model") or "?" for c in calls}),
    }


def geo(ratios: list[float]) -> str:
    ratios = [r for r in ratios if r > 0 and math.isfinite(r)]
    if len(ratios) < 2:
        return "-"
    logs = [math.log(r) for r in ratios]
    rng = random.Random(0)
    boots = sorted(statistics.fmean(rng.choices(logs, k=len(logs))) for _ in range(20_000))
    return f"{math.exp(statistics.fmean(logs)):.2f} [{math.exp(boots[500]):.2f}, {math.exp(boots[19_500]):.2f}]"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("usage", type=Path)
    ap.add_argument("tags")
    ap.add_argument("--base", required=True, help="tag of the baseline model")
    ap.add_argument("--json", type=Path, help="also write the per-item attribution here")
    args = ap.parse_args()
    keys, usage = load_usage(args.usage)
    tags = args.tags.split(",")
    dump = {}
    for phase in PHASES:
        per = {t: {i: attribute(r, keys, usage) for i, r in items(phase, t).items() if not r.get("error")} for t in tags}
        if not any(per.values()):
            continue
        print(f"\n## {phase}")
        print(f"{'model':6s} {'n':>3s} {'calls':>6s} {'input p50':>10s} {'cached%':>8s} {'output p50':>11s} {'reason p50':>11s} {'reason%':>8s} {'ttfb p50':>9s}  output ratio vs base    reasoning ratio vs base")
        base = per.get(args.base, {})
        for t in tags:
            a = per[t]
            if not a:
                continue
            v = list(a.values())
            tot_in, tot_out = sum(x["input"] for x in v), sum(x["output"] for x in v)
            ttfb = [y for x in v for y in x["ttfb"]]
            common = [i for i in a if i in base]
            ro = geo([a[i]["output"] / base[i]["output"] for i in common if base[i]["output"]]) if t != args.base else ""
            rr = geo([a[i]["reasoning"] / base[i]["reasoning"] for i in common if base[i]["reasoning"] and a[i]["reasoning"]]) if t != args.base else ""
            print(
                f"{t:6s} {len(v):3d} {statistics.median(x['calls'] for x in v):6.1f} {statistics.median(x['input'] for x in v):10.0f} "
                f"{100 * sum(x['cached'] for x in v) / max(tot_in, 1):7.0f}% {statistics.median(x['output'] for x in v):11.0f} "
                f"{statistics.median(x['reasoning'] for x in v):11.0f} {100 * sum(x['reasoning'] for x in v) / max(tot_out, 1):7.0f}% "
                f"{statistics.median(ttfb) if ttfb else float('nan'):8.1f}s  {ro:22s} {rr}"
            )
            bad = [i for i, x in a.items() if x["errors"]]
            if bad:
                print(f"       items with failed LLM calls: {bad}")
            dump.setdefault(phase, {})[t] = a
    if args.json:
        args.json.write_text(json.dumps(dump, indent=1))


if __name__ == "__main__":
    main()
