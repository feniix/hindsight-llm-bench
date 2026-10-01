"""Compare two reflect runs: latency, and blind answer-quality pairs.

    python3 reflect/compare.py BASE CANDIDATE              # latency report
    python3 reflect/compare.py BASE CANDIDATE --pairs DIR  # also write judge files
    python3 reflect/compare.py BASE CANDIDATE --verdicts DIR  # score judged pairs
    python3 reflect/compare.py a1,a2,a3 b1,b2,b3          # alternating blocks, merged

Labels name runs in $BENCH_WORK/runs/reflect/ (or pass .json paths).

Latency: per-query paired ratio CANDIDATE/BASE, summarised as the geometric
mean with a 95% bootstrap interval (queries resampled), overall and per kind.
A ratio of 0.70 means 30% faster.

Quality: --pairs writes one file per query with the two answers as A and B in
random order (the key is kept in DIR/key.json). Judges follow rubric.md and
write <id>.verdict.json as {"winner": "A"|"B"|"tie", "why": "..."}.
--verdicts un-blinds them and reports candidate wins/ties/losses with a sign
test. Add --failures-as-losses to also count every query the candidate failed
and the base answered (a timeout, say) as a candidate loss, so the slowest
queries do not drop out of the quality record. Stdlib only.
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
    """One run, or several comma-joined runs (alternating blocks) merged by query id."""
    rows = {}
    for label in labels.split(","):
        path = Path(label) if label.endswith(".json") else cfg.REFLECT_RUNS / f"{label}.json"
        rows.update({r["id"]: r for r in json.loads(path.read_text())["results"]})
    return rows


def geo_ci(ratios: list[float]) -> tuple[float, float, float]:
    logs = [math.log(r) for r in ratios]
    rng = random.Random(0)
    boots = sorted(statistics.fmean(rng.choices(logs, k=len(logs))) for _ in range(20_000))
    return math.exp(statistics.fmean(logs)), math.exp(boots[500]), math.exp(boots[19_500])


def latency(base: dict, cand: dict) -> None:
    ids = [i for i in base if i in cand and not base[i]["error"] and not cand[i]["error"]]
    failed = [i for i in base if i in cand and (base[i]["error"] or cand[i]["error"])]
    groups = {"all": ids}
    for i in ids:
        groups.setdefault(base[i]["kind"], []).append(i)
    print(f"{'kind':14s} {'n':>3s} {'base p50':>9s} {'cand p50':>9s}  ratio [95%]          final-call ratio")
    for kind, g in groups.items():
        if not g:
            continue
        r = geo_ci([cand[i]["seconds"] / base[i]["seconds"] for i in g])
        fin = [cand[i]["final_llm_s"] / base[i]["final_llm_s"] for i in g if cand[i]["final_llm_s"] and base[i]["final_llm_s"]]
        f = f"{geo_ci(fin)[0]:.2f}" if len(fin) > 1 else "-"
        mark = "*" if r[2] < 1 or r[1] > 1 else " "
        print(
            f"{kind:14s} {len(g):3d} {statistics.median(base[i]['seconds'] for i in g):8.1f}s "
            f"{statistics.median(cand[i]['seconds'] for i in g):8.1f}s  {r[0]:.2f} [{r[1]:.2f}, {r[2]:.2f}]{mark}  {f}"
        )
    print("* interval excludes 1.00")
    if failed:
        print(f"errors in either run (excluded): {failed}")


def write_pairs(base: dict, cand: dict, queries: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    key = {}
    for i in base:
        if i not in cand or base[i]["error"] or cand[i]["error"]:
            continue
        flip = rng.random() < 0.5
        a, b = (cand[i], base[i]) if flip else (base[i], cand[i])
        key[i] = "A" if flip else "B"  # which letter is the candidate
        q = queries.get(i) or {"bank": base[i]["bank"], "kind": base[i]["kind"], "query": base[i].get("question", "")}
        (out / f"{i}.json").write_text(
            json.dumps(
                {"id": i, "bank": q["bank"], "kind": q["kind"], "question": q["query"], "A": a["answer"], "B": b["answer"]}
                | ({"A_sources": a["sources"], "B_sources": b["sources"]} if "sources" in a and "sources" in b else {}),
                indent=1,
                ensure_ascii=False,
            )
        )
    (out / "key.json").write_text(json.dumps(key, indent=1))
    print(f"wrote {len(key)} pairs to {out}")


def score(out: Path, base: dict | None = None, cand: dict | None = None) -> None:
    key = json.loads((out / "key.json").read_text())
    wins = ties = losses = 0
    failed = [i for i in (base or {}) if i in (cand or {}) and cand[i]["error"] and not base[i]["error"]] if base and cand else []
    missing = []
    for i, cand_letter in key.items():
        f = out / f"{i}.verdict.json"
        if not f.exists():
            missing.append(i)
            continue
        w = json.loads(f.read_text())["winner"]
        if w == "tie":
            ties += 1
        elif w == cand_letter:
            wins += 1
        else:
            losses += 1
    def line(w: int, t: int, lo: int, note: str = "") -> None:
        n = w + lo
        # two-sided sign test on decisive pairs
        p = min(1.0, 2 * sum(math.comb(n, k) for k in range(0, min(w, lo) + 1)) / 2**n) if n else 1.0
        print(f"candidate vs base{note}: {w} better, {t} tie, {lo} worse (sign test p={p:.3f})")

    line(wins, ties, losses)
    if failed:
        line(wins, ties, losses + len(failed), f", counting {len(failed)} candidate failures as losses")
    if missing:
        print(f"no verdict yet: {missing}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("candidate")
    ap.add_argument("--pairs", type=Path)
    ap.add_argument("--verdicts", type=Path)
    ap.add_argument("--failures-as-losses", action="store_true")
    ap.add_argument("--queries", type=Path, default=cfg.WORK / "queries.json", help="for the question text in pairs")
    args = ap.parse_args()
    base, cand = load(args.base), load(args.candidate)
    latency(base, cand)
    if args.pairs:
        queries = {q["id"]: q for q in json.loads(args.queries.read_text())} if args.queries.exists() else {}
        write_pairs(base, cand, queries, args.pairs)
    if args.verdicts:
        score(args.verdicts, *((base, cand) if args.failures_as_losses else ()))


if __name__ == "__main__":
    main()
