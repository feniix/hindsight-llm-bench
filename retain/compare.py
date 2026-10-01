"""Compare two retain runs: latency, fact counts, and blind extraction-quality pairs.

    python3 retain/compare.py BASE CANDIDATE                            # latency + counts
    python3 retain/compare.py BASE CANDIDATE --pairs DIR --docs DOCS.json  # also write judge files
    python3 retain/compare.py BASE CANDIDATE --verdicts DIR              # score judged pairs
    python3 retain/compare.py a1,a2,a3,a4 b1,b2,b3,b4                   # alternating blocks, merged

Labels name runs in $BENCH_WORK/runs/retain/ (or pass .json paths).

Latency is the per-document paired ratio CANDIDATE/BASE, as a geometric mean
with a 95% bootstrap interval (documents resampled). --pairs writes one file per
document with the source document and the two fact lists as A and B in random
order (key in DIR/key.json); the files carry bank content, so write them under
$BENCH_WORK, which is git-ignored. Judges follow rubric.md and write <id>.verdict.json as
{"winner": "A"|"B"|"tie", "why": "..."}. Stdlib only.
"""

import argparse
import json
import sys
import math
import random
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench_config as cfg  # noqa: E402



def load(labels: str) -> dict[str, dict]:
    rows = {}
    for label in labels.split(","):
        path = Path(label) if label.endswith(".json") else cfg.RETAIN_RUNS / f"{label}.json"
        rows.update({r["id"]: r for r in json.loads(path.read_text())["results"]})
    return rows


def geo_ci(ratios: list[float]) -> tuple[float, float, float]:
    logs = [math.log(r) for r in ratios]
    rng = random.Random(0)
    boots = sorted(statistics.fmean(rng.choices(logs, k=len(logs))) for _ in range(20_000))
    return math.exp(statistics.fmean(logs)), math.exp(boots[500]), math.exp(boots[19_500])


def report(base: dict, cand: dict) -> None:
    ids = [i for i in base if i in cand and not base[i]["error"] and not cand[i]["error"]]
    failed = [i for i in base if i in cand and (base[i]["error"] or cand[i]["error"])]
    r = geo_ci([cand[i]["seconds"] / base[i]["seconds"] for i in ids])
    mark = "*" if r[2] < 1 or r[1] > 1 else ""
    print(f"documents: {len(ids)} ok in both" + (f", errors (excluded): {failed}" if failed else ""))
    print(
        f"latency  base p50 {statistics.median(base[i]['seconds'] for i in ids):.1f}s  "
        f"cand p50 {statistics.median(cand[i]['seconds'] for i in ids):.1f}s  ratio {r[0]:.2f} [{r[1]:.2f}, {r[2]:.2f}]{mark}"
    )
    nb, nc = [len(base[i]["facts"]) for i in ids], [len(cand[i]["facts"]) for i in ids]
    print(f"facts    base total {sum(nb)} (median {statistics.median(nb)})  cand total {sum(nc)} (median {statistics.median(nc)})")
    cb = [statistics.fmean(len(f["text"]) for f in base[i]["facts"]) for i in ids if base[i]["facts"]]
    cc = [statistics.fmean(len(f["text"]) for f in cand[i]["facts"]) for i in ids if cand[i]["facts"]]
    if cb and cc:
        print(f"fact length (mean chars per fact, median over docs)  base {statistics.median(cb):.0f}  cand {statistics.median(cc):.0f}")
    zero = [i for i in ids if not base[i]["facts"] or not cand[i]["facts"]]
    if zero:
        print(f"documents with no facts on either side: {zero}")
    print("* interval excludes 1.00")


def write_pairs(base: dict, cand: dict, docs: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    key = {}
    for i in sorted(base):
        if i not in cand or base[i]["error"] or cand[i]["error"]:
            continue
        flip = rng.random() < 0.5
        a, b = (cand[i], base[i]) if flip else (base[i], cand[i])
        key[i] = "A" if flip else "B"
        d = docs[i]
        strip = lambda facts: [{k: f[k] for k in ("text", "fact_type", "occurred_start", "entities")} for f in facts]  # noqa: E731
        (out / f"{i}.json").write_text(
            json.dumps(
                {"id": i, "bank": d["bank"], "strategy": d.get("strategy"), "mission": d.get("mission"),
                 "extraction_mode": d.get("extraction_mode"), "custom_instructions": d.get("custom_instructions"),
                 "context": d["context"], "timestamp": d["timestamp"], "document": d["content"],
                 "A": strip(a["facts"]), "B": strip(b["facts"])},
                indent=1,
                ensure_ascii=False,
            )
        )
    (out / "key.json").write_text(json.dumps(key, indent=1))
    print(f"wrote {len(key)} pairs to {out}")


def score(verdicts: Path) -> None:
    key = json.loads((verdicts / "key.json").read_text())
    better = worse = tie = 0
    for i, letter in key.items():
        v = json.loads((verdicts / f"{i}.verdict.json").read_text())["winner"]
        if v == "tie":
            tie += 1
        elif v == letter:
            better += 1
        else:
            worse += 1
    n, k = better + worse, min(better, worse)
    p = min(1.0, 2 * sum(math.comb(n, j) for j in range(k + 1)) / 2**n) if n else 1.0
    print(f"candidate vs base: {better} better, {tie} tie, {worse} worse (sign test p={p:.3f})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("candidate")
    ap.add_argument("--pairs", type=Path)
    ap.add_argument("--docs", type=Path)
    ap.add_argument("--verdicts", type=Path)
    args = ap.parse_args()
    base, cand = load(args.base), load(args.candidate)
    report(base, cand)
    if args.pairs:
        if not args.docs:
            ap.error("--pairs needs --docs")
        docs = {d["id"]: d for d in json.loads(args.docs.read_text())["docs"]}
        write_pairs(base, cand, docs, args.pairs)
    if args.verdicts:
        score(args.verdicts)


if __name__ == "__main__":
    main()
