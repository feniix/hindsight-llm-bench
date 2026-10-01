"""Write a balanced block plan: which models run in which order, on which items, per block.

    python3 prepare/plan.py --model base=gpt-5.6-luna --model g6l=gpt-6-luna --model sol=gpt-6.1-sol \\
        --base base [--blocks 6] [--queries Q.json] [--pages P.json] [--docs D.json] [-o PLAN.json]

Each block runs every model once, in a different order. With 3 models and 6
blocks the orders are all 6 permutations, so each model runs first, second and
third twice; otherwise the orders are rotations of the model list, repeated.
Speed drifts over hours on a shared subscription, so balanced orders matter more
than any single run.

Items are dealt round-robin into the blocks after a seeded shuffle: reflect
queries within each kind (so every block mixes kinds), documents by size (so
every block has short and long ones), and pages in order. The plan lists only
ids; the inputs stay in their own files. Default output: $BENCH_WORK/plan.json.
Stdlib only.
"""

import argparse
import itertools
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench_config as cfg  # noqa: E402


def orders(tags: list[str], blocks: int) -> list[list[str]]:
    rot = [tags[i:] + tags[:i] for i in range(len(tags))]
    perms = [list(p) for p in itertools.permutations(tags)]
    if blocks % len(perms) != 0:
        cycle = rot
    elif len(tags) == 3:
        cycle = rot + [list(reversed(r)) for r in rot]  # all 6 orders, no first model repeated back to back
    else:
        cycle = perms
    return [cycle[i % len(cycle)] for i in range(blocks)]


def deal(groups: list[list[str]], blocks: int) -> list[list[str]]:
    out, n = [[] for _ in range(blocks)], 0
    for g in groups:
        for item in g:
            out[n % blocks].append(item)
            n += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", required=True, metavar="TAG=MODEL")
    ap.add_argument("--base", required=True, help="tag of the baseline model")
    ap.add_argument("--blocks", type=int, default=6)
    ap.add_argument("--queries", type=Path)
    ap.add_argument("--pages", type=Path)
    ap.add_argument("--docs", type=Path)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-o", "--out", type=Path, default=cfg.WORK / "plan.json")
    a = ap.parse_args()

    models = dict(m.split("=", 1) for m in a.model)
    if a.base not in models:
        ap.error(f"--base {a.base} is not one of {list(models)}")
    rng = random.Random(a.seed)
    plan = {"models": models, "base": a.base, "orders": orders(list(models), a.blocks)}

    if a.queries:
        by_kind = defaultdict(list)
        for q in json.loads(a.queries.read_text()):
            by_kind[q["kind"]].append(q["id"])
        for ids in by_kind.values():
            rng.shuffle(ids)
        plan["reflect"] = deal([by_kind[k] for k in sorted(by_kind)], a.blocks)
    if a.pages:
        plan["pages"] = deal([[p["id"] for p in json.loads(a.pages.read_text())["pages"]]], a.blocks)
    if a.docs:
        docs = sorted(json.loads(a.docs.read_text())["docs"], key=lambda d: d.get("chars") or len(d.get("content") or ""))
        plan["retain"] = deal([[d["id"] for d in docs]], a.blocks)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(plan, indent=1))
    print(f"wrote {a.out}: {len(models)} models, {a.blocks} blocks, " + ", ".join(f"{k} {sum(map(len, plan[k]))} items" for k in ("reflect", "pages", "retain") if k in plan), file=sys.stderr)


if __name__ == "__main__":
    main()
