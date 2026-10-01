"""Downstream follow-up driver: does changing the retain model change what consolidation and reflect produce?

    python3 followup/fu.py setup    SRC_BANK NEW_BANK          # create NEW_BANK with SRC_BANK's config
    python3 followup/fu.py retain   SRC_BANK NEW_BANK [--limit N]
    python3 followup/fu.py consolidate NEW_BANK
    python3 followup/fu.py pages    SRC_BANK NEW_BANK          # create SRC_BANK's pages in NEW_BANK, refresh, save
    python3 followup/fu.py qa       GOLD_BANK BANK_A BANK_B    # reflect every gold question on both banks

Runs against the follow-up container (HINDSIGHT_BENCH_URL, default http://localhost:18888).
Inputs come from FU_DIR (default $BENCH_WORK/followup): corpus.json (documents in
date order, page definitions; written by prepare/export.py corpus), config-<bank>.json
(source bank config), gold.json (questions; written by followup/gold.py).
Setup copies the source bank's config but always sets enable_auto_consolidation to
false: a per-bank true overrides the server-wide setting, and then every bank
consolidates as soon as the worker starts instead of when this driver asks.
FU_SKIP_PAGES (comma-separated name prefixes) leaves out pages that should not be
rebuilt, such as a page about the experiment itself. Every step appends to FU_DIR/out/<step>-<bank>.json with
started/ended timestamps, so proxy usage and llm_requests rows can be attributed to it.
Retain sends each document as the source bank received it: same document_id, context,
timestamp, metadata, observation_scopes, and the retain strategy it would get today.
Stdlib only.
"""

import argparse
import json
import os
import random
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
FU = Path(os.environ.get("FU_DIR", cfg.FOLLOWUP))
SKIP_PAGES = tuple(p for p in os.environ.get("FU_SKIP_PAGES", "").split(",") if p)


def now() -> str:
    return datetime.now().astimezone().isoformat()


def call(method: str, path: str, body: dict | None = None, timeout: float = 1800) -> dict:
    req = urllib.request.Request(
        BASE + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: HTTP {e.code}: {e.read()[:500]!r}") from None


def bank_path(bank: str) -> str:
    return "/v1/default/banks/" + urllib.parse.quote(bank, safe="")


def save(step: str, bank: str, record: dict) -> Path:
    out = FU / "out" / f"{step}-{bank.split('::')[-1]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return out


def stats(bank: str) -> dict:
    return call("GET", bank_path(bank) + "/stats", timeout=60)


def setup(src: str, new: str) -> None:
    conf = json.loads((FU / f"config-{src.split('::')[-1]}.json").read_text())
    conf = dict(conf.get("config", conf))
    conf["enable_auto_consolidation"] = False
    call("PUT", bank_path(new), {})
    try:
        call("PATCH", bank_path(new) + "/config", {"updates": conf})
    except RuntimeError:
        call("PATCH", bank_path(new) + "/config", conf)
    got = call("GET", bank_path(new) + "/config")
    got = got.get("config", got)
    keys = ("retain_default_strategy", "retain_strategies", "retain_mission", "retain_extraction_mode", "enable_auto_consolidation")
    diff = [k for k in keys if got.get(k) != conf.get(k)]
    if diff:
        sys.exit(f"config copy incomplete for {new}: {diff}")
    print(f"{new}: created with {src}'s config ({len(conf)} keys), auto-consolidation off")


def retain(src: str, new: str, limit: int | None) -> None:
    docs = json.loads((FU / "corpus.json").read_text())[src]["docs"][:limit]
    rows, before = [], stats(new)
    started = now()
    for n, d in enumerate(docs, 1):
        item = {k: d[k] for k in ("content", "context", "timestamp", "metadata", "document_id", "observation_scopes", "tags") if d.get(k)}
        if (s := cfg.strategy_for(d)):
            item["strategy"] = s
        t0, s0 = time.monotonic(), now()
        try:
            resp, err = call("POST", bank_path(new) + "/memories", {"items": [item], "async": False}), None
        except RuntimeError as e:
            resp, err = {}, str(e)[:300]
        rows.append({"document_id": d["document_id"], "chars": len(d["content"]), "strategy": item.get("strategy"), "started": s0, "ended": now(),
                     "seconds": round(time.monotonic() - t0, 2), "error": err, "usage": resp.get("usage")})
        print(f"[{n:2d}/{len(docs)}] {d['document_id'][:40]:40s} {len(d['content']):7d} chars {rows[-1]['seconds']:6.1f}s {err or ''}", file=sys.stderr)
    after = stats(new)
    save("retain", new, {"bank": new, "source": src, "retain_model": os.environ.get("RETAIN_MODEL"), "started": started, "ended": now(), "stats_before": before, "stats_after": after, "docs": rows})
    units = sum((after.get("nodes_by_fact_type") or {}).values())
    print(f"{new}: {len(docs)} documents, {sum(1 for r in rows if r['error'])} errors, {units} memory units")
    if units == 0:
        sys.exit("ABORT: retain produced no memory units")


def busy_ops(bank: str) -> list[dict]:
    d = call("GET", bank_path(bank) + "/operations?limit=100", timeout=60)
    ops = d.get("operations", d if isinstance(d, list) else [])
    return [o for o in ops if o.get("status") in ("pending", "queued", "processing", "running")]


def wait_idle(bank: str, label: str, poll: float = 15, quiet_polls: int = 2) -> dict:
    """Poll until no operation is pending or processing and nothing awaits consolidation, `quiet_polls` times in a row.

    /stats pending_operations counts only pending tasks, not ones a worker is already processing, so the
    operations list is the authority here."""
    quiet = 0
    while True:
        s, busy = stats(bank), busy_ops(bank)
        print(f"  {label} {datetime.now():%H:%M:%S} busy={[o.get('task_type') for o in busy]} pending_consolidation={s.get('pending_consolidation')} observations={s.get('total_observations')}", file=sys.stderr)
        quiet = quiet + 1 if not busy and not s.get("pending_consolidation") else 0
        if quiet >= quiet_polls:
            return s
        time.sleep(poll)


def consolidate(bank: str) -> None:
    before, started = stats(bank), now()
    t0 = time.monotonic()
    op = call("POST", bank_path(bank) + "/consolidate", {})
    after = wait_idle(bank, "consolidate")
    ops = call("GET", bank_path(bank) + "/operations?limit=50")
    save("consolidate", bank, {"bank": bank, "started": started, "ended": now(), "seconds": round(time.monotonic() - t0, 1), "operation": op,
                               "stats_before": before, "stats_after": after, "operations": ops})
    print(f"{bank}: consolidated in {time.monotonic() - t0:.0f}s, observations {after.get('total_observations')}, failed_consolidation {after.get('failed_consolidation')}")


def pages(src: str, new: str, limit: int | None = None) -> None:
    defs = [p for p in json.loads((FU / "corpus.json").read_text())[src]["pages"] if not p["name"].startswith(SKIP_PAGES)][:limit]
    started, results = now(), []
    for p in defs:
        trigger = dict(p.get("trigger") or {})
        trigger["refresh_after_consolidation"] = False
        mm = call("POST", bank_path(new) + "/mental-models", {"name": p["name"], "source_query": p["source_query"], "tags": p.get("tags") or [], "trigger": trigger})
        mid = mm.get("id") or (mm.get("mental_model") or {}).get("id") or mm.get("mental_model_id")
        if not mid:
            sys.exit(f"could not read mental model id from {mm}")
        time.sleep(3)  # let the creation-time refresh get queued before polling
        t0, s0 = time.monotonic(), now()
        # Creating a mental model queues its first refresh; wait for it, and only ask again if it left the page empty.
        wait_idle(new, f"refresh {p['name'][:30]}", poll=10, quiet_polls=1)
        got = call("GET", bank_path(new) + f"/mental-models/{mid}")
        if not got.get("content"):
            call("POST", bank_path(new) + f"/mental-models/{mid}/refresh", {})
            time.sleep(5)
            wait_idle(new, f"re-refresh {p['name'][:27]}", poll=10, quiet_polls=1)
            got = call("GET", bank_path(new) + f"/mental-models/{mid}")
        results.append({"name": p["name"], "id": mid, "question": p["source_query"], "started": s0, "ended": now(), "seconds": round(time.monotonic() - t0, 1),
                        "content": got.get("content"), "last_refreshed_at": got.get("last_refreshed_at")})
        print(f"  {p['name'][:50]:50s} {results[-1]['seconds']:6.1f}s {len(got.get('content') or '')} chars", file=sys.stderr)
    save("pages", new, {"bank": new, "source": src, "started": started, "ended": now(), "pages": results})
    print(f"{new}: {len(results)} pages refreshed, {sum(1 for r in results if not r['content'])} empty")


def qa(gold_bank: str, banks: list[str]) -> None:
    questions = json.loads((FU / "gold.json").read_text())[gold_bank]["questions"]
    rng, rows = random.Random(0), {b: [] for b in banks}
    for n, q in enumerate(questions, 1):
        order = list(banks)
        rng.shuffle(order)
        for b in order:
            t0, s0 = time.monotonic(), now()
            try:
                r, err = call("POST", bank_path(b) + "/reflect", {"query": q["question"], "budget": "low", "include": {"facts": {}, "tool_calls": {}}}, timeout=420), None
            except RuntimeError as e:
                r, err = {}, str(e)[:300]
            based = r.get("based_on") or {}
            rows[b].append({"id": q["id"], "type": q["type"], "question": q["question"], "started": s0, "ended": now(), "seconds": round(time.monotonic() - t0, 2),
                            "answer": r.get("text"), "error": err,
                            "sources": {k: len(v) for k, v in based.items() if isinstance(v, list)},
                            "source_types": sorted({(s.get("type") or s.get("fact_type") or k) for k, v in based.items() if isinstance(v, list) for s in v})})
            print(f"[{n:2d}/{len(questions)}] {q['id']} {b.split('::')[-1]:14s} {rows[b][-1]['seconds']:6.1f}s {err or rows[b][-1]['sources']}", file=sys.stderr)
    for b in banks:
        save("qa", b, {"bank": b, "gold_bank": gold_bank, "answers": rows[b]})
    print(f"qa {gold_bank}: {len(questions)} questions x {len(banks)} banks, errors {[sum(1 for r in rows[b] if r['error']) for b in banks]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["setup", "retain", "consolidate", "pages", "qa"])
    ap.add_argument("banks", nargs="+")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.step == "setup":
        setup(*a.banks)
    elif a.step == "retain":
        retain(a.banks[0], a.banks[1], a.limit)
    elif a.step == "consolidate":
        consolidate(a.banks[0])
    elif a.step == "pages":
        pages(a.banks[0], a.banks[1], a.limit)
    else:
        qa(a.banks[0], a.banks[1:])


if __name__ == "__main__":
    main()
