"""Dry-run knowledge-page refreshes and save them in the reflect run row format.

    python3 reflect/refresh.py LABEL PAGES.json [--ids p00,p03]

Each page in PAGES.json ({"pages": [{"id", "bank", "mental_model_id", "name",
"question"}, ...]}) is sent to POST /v1/default/banks/{bank}/mental-models/{id}/dry-run-refresh:
the production refresh pipeline (its own delta/full mode, window and scope) with
the content and watermark writes skipped, so repeating it reads the same window.
With no HINDSIGHT_API_MENTAL_MODEL_REFRESH_LLM_* override, refresh runs on the
reflect model, so it is compared in the same alternating blocks as reflect.

Rows land in $BENCH_WORK/runs/reflect/LABEL.json with the reflect row fields, so compare.py reports
latency and writes blind pairs for them unchanged: `answer` is the raw synthesis
(candidate_content, produced in every mode), `sources` its evidence, plus the
refresh-specific outcome, mode, fact counts, delta operations and preview.
Stdlib only.
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


def dry_run(page: dict) -> tuple[float, dict | None, str | None]:
    bank = urllib.parse.quote(page["bank"], safe="")
    url = f"{BASE}/v1/default/banks/{bank}/mental-models/{page['mental_model_id']}/dry-run-refresh"
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return time.monotonic() - start, json.load(resp), None
    except urllib.error.HTTPError as e:
        return time.monotonic() - start, None, f"HTTP {e.code}: {e.read()[:300]!r}"
    except (urllib.error.URLError, TimeoutError) as e:
        return time.monotonic() - start, None, repr(e)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label")
    parser.add_argument("pages", type=Path)
    parser.add_argument("--ids", help="run only these page ids, comma-separated")
    args = parser.parse_args()

    pages = json.loads(args.pages.read_text())["pages"]
    if args.ids:
        wanted = set(args.ids.split(","))
        pages = [p for p in pages if p["id"] in wanted]
        if missing := wanted - {p["id"] for p in pages}:
            sys.exit(f"unknown page ids: {sorted(missing)}")

    rows = []
    for n, page in enumerate(pages, 1):
        started = datetime.now().astimezone().isoformat()
        seconds, resp, error = dry_run(page)
        ended = datetime.now().astimezone().isoformat()
        r = resp or {}
        trace = r.get("trace") or {}
        llm = [c.get("duration_ms", 0) / 1000 for c in trace.get("llm_calls") or []]
        based_on = r.get("based_on") or {}
        rows.append(
            {
                "id": page["id"],
                "kind": "page_dry_refresh",
                "bank": page["bank"],
                "question": page["question"],
                "name": page.get("name"),
                "started": started,
                "ended": ended,
                "seconds": round(seconds, 2),
                "llm_calls_s": [round(x, 2) for x in llm],
                "final_llm_s": round(llm[-1], 2) if llm else None,
                "answer": r.get("candidate_content"),
                "sources": [
                    {"type": kind, "id": src.get("id"), "text": src.get("text")}
                    for kind, items in based_on.items()
                    if isinstance(items, list)
                    for src in items
                ],
                "effective_mode": r.get("effective_mode"),
                "mode_fallback_reason": r.get("mode_fallback_reason"),
                "outcome": r.get("outcome"),
                "would_persist": r.get("would_persist"),
                "facts": r.get("facts"),
                "delta_operations": r.get("delta_operations"),
                "preview_content": r.get("preview_content"),
                "diff_lines": len((r.get("diff") or "").splitlines()),
                "warnings": r.get("warnings"),
                "server_duration_ms": r.get("duration_ms"),
                "error": error or (None if r.get("candidate_content") else "no candidate_content"),
            }
        )
        status = rows[-1]["error"] or f"{r.get('effective_mode')} {r.get('outcome')} {len(llm)} LLM calls"
        print(f"[{n:2d}/{len(pages)}] {page['id']} {seconds:6.1f}s  {status}", file=sys.stderr)

    out = cfg.REFLECT_RUNS / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"label": args.label, "at": datetime.now().astimezone().isoformat(), "pages": str(args.pages)}
    out.write_text(json.dumps({"meta": meta, "results": rows}, indent=1, ensure_ascii=False))
    ok = sorted(r["seconds"] for r in rows if not r["error"])
    print(f"{args.label}: {len(ok)}/{len(rows)} ok" + (f", median {ok[len(ok) // 2]:.1f}s" if ok else "") + f" -> {out}")


if __name__ == "__main__":
    main()
