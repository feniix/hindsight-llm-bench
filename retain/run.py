"""Run real documents through retain's fact extraction without storing anything.

    python3 retain/run.py LABEL DOCS.json [--ids d00,d04]

Each document in DOCS.json ({"docs": [{"id", "bank", "content", "context",
"timestamp", "strategy"}, ...]}) is sent to POST /v1/default/banks/{bank}/memories/dry-run-extract,
which runs the bank's real extraction (the named retain strategy's mission,
extraction mode and chunking, or the bank default) on the server's retain LLM and returns the candidate facts without
touching the bank. The endpoint is off by default: the server needs
HINDSIGHT_API_ENABLE_DRY_RUN_EXTRACT=true.

The retain model is a server-side setting (HINDSIGHT_API_RETAIN_LLM_MODEL), so
compare two models in alternating blocks with a restart between them, as for
reflect. Documents run one at a time. The source documents are bank content, so
keep DOCS.json under $BENCH_WORK (git-ignored); $BENCH_WORK/runs/retain/LABEL.json
keeps the extracted facts and timings, not the documents. Stdlib only.
"""

import argparse
import json
import os
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


def extract(doc: dict) -> tuple[float, dict | None, str | None]:
    url = f"{BASE}/v1/default/banks/{urllib.parse.quote(doc['bank'], safe='')}/memories/dry-run-extract"
    body = {"content": doc["content"], "context": doc.get("context") or "", "timestamp": doc.get("timestamp")}
    if doc.get("strategy"):
        body["strategy"] = doc["strategy"]
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return time.monotonic() - start, json.load(resp), None
    except urllib.error.HTTPError as e:
        return time.monotonic() - start, None, f"HTTP {e.code}: {e.read()[:300]!r}"
    except (urllib.error.URLError, TimeoutError) as e:
        return time.monotonic() - start, None, repr(e)


def server_meta() -> dict:
    meta = {}
    try:
        with urllib.request.urlopen(f"{BASE}/version", timeout=10) as resp:
            meta["api_version"] = json.load(resp).get("api_version")
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass
    import subprocess

    try:
        out = subprocess.run(["docker", "exec", CONTAINER, "env"], capture_output=True, text=True, timeout=20).stdout
        keep = ("HINDSIGHT_API_RETAIN", "HINDSIGHT_API_LLM")
        meta["env"] = dict(l.split("=", 1) for l in out.splitlines() if l.startswith(keep) and "KEY" not in l and "MISSION" not in l)
    except (OSError, subprocess.SubprocessError):
        pass
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label")
    parser.add_argument("docs", type=Path)
    parser.add_argument("--ids", help="run only these document ids, comma-separated")
    args = parser.parse_args()

    docs = json.loads(args.docs.read_text())["docs"]
    if args.ids:
        wanted = set(args.ids.split(","))
        docs = [d for d in docs if d["id"] in wanted]
        if missing := wanted - {d["id"] for d in docs}:
            sys.exit(f"unknown document ids: {sorted(missing)}")

    rows = []
    for n, doc in enumerate(docs, 1):
        started = datetime.now().astimezone().isoformat()
        seconds, resp, error = extract(doc)
        ended = datetime.now().astimezone().isoformat()
        facts = (resp or {}).get("facts") or []
        rows.append(
            {
                "id": doc["id"],
                "bank": doc["bank"],
                "chars": len(doc["content"]),
                "strategy": doc.get("strategy"),
                "started": started,
                "ended": ended,
                "seconds": round(seconds, 2),
                "chunks": len((resp or {}).get("chunks") or []),
                "facts": [
                    {k: f.get(k) for k in ("text", "fact_type", "occurred_start", "occurred_end", "entities", "chunk_index")}
                    for f in facts
                ],
                "error": error,
            }
        )
        status = error or f"{len(facts)} facts from {rows[-1]['chunks']} chunks"
        print(f"[{n:2d}/{len(docs)}] {doc['id']} {len(doc['content']):6d} chars {seconds:6.1f}s  {status}", file=sys.stderr)

    out = cfg.RETAIN_RUNS / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"label": args.label, "at": datetime.now().astimezone().isoformat(), **server_meta()}
    out.write_text(json.dumps({"meta": meta, "results": rows}, indent=1, ensure_ascii=False))
    ok = sorted(r["seconds"] for r in rows if not r["error"])
    if ok:
        print(f"{args.label}: {len(ok)}/{len(rows)} ok, median {ok[len(ok) // 2]:.1f}s -> {out}")
    else:
        print(f"{args.label}: 0/{len(rows)} ok -> {out}")


if __name__ == "__main__":
    main()
