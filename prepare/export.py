"""Build benchmark inputs from an existing Hindsight instance, read-only.

    python3 prepare/export.py docs    --bank B [--bank B2 ...] [--per-bank 3] [--min-chars 3000] [--max-chars 40000] [-o FILE]
    python3 prepare/export.py pages   --bank B [--bank B2 ...] [--per-bank 3] [--screen --target 12] [-o FILE]
    python3 prepare/export.py queries --pages PAGES.json [-o FILE]
    python3 prepare/export.py corpus  --bank B [--bank B2 ...] [-o DIR]

Every request against the source instance ($HINDSIGHT_SOURCE_URL, default
http://localhost:8888) is a GET, so the source is never modified. Outputs go to
$BENCH_WORK by default and contain bank content: keep them there (git-ignored).

docs     samples documents with text length in [min, max] from each bank (seeded),
         and records each one's retain strategy (recorded, else inferred from its
         context, else the bank default), context and timestamp, plus the
         mission, extraction mode and custom instructions that strategy resolves
         to in the bank config, for the retain judges. -> docs.json
pages    lists knowledge pages (mental models with a source query). With --screen
         it dry-runs each page's refresh on the BENCH instance
         ($HINDSIGHT_BENCH_URL, restored from the same backup so the ids match)
         and keeps only pages whose refresh calls the LLM, until --target pages.
         Pages whose refresh finds nothing new skip the LLM and measure nothing.
         -> pages.json
queries  turns each page's question into a reflect query of kind page_refresh,
         with the parameters a page refresh uses. Add other kinds by hand (see
         examples/queries.example.json). -> queries.json
corpus   exports whole banks for the downstream follow-up: every document in date
         order with its retain parameters, the bank's page definitions, and its
         config. -> DIR/corpus.json and DIR/config-<bank>.json

Stdlib only.
"""

import argparse
import json
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench_config as cfg  # noqa: E402

PAGE_REFRESH_PARAMS = {"budget": "mid", "max_tokens": 4096, "fact_types": ["observation"], "exclude_mental_models": True}


def get(path: str, base: str = cfg.SOURCE_URL) -> dict:
    with urllib.request.urlopen(base + path, timeout=120) as resp:
        return json.load(resp)


def bank_path(bank: str) -> str:
    return "/v1/default/banks/" + urllib.parse.quote(bank, safe="")


def paged(path: str, page: int = 100) -> list[dict]:
    items, offset = [], 0
    while True:
        sep = "&" if "?" in path else "?"
        d = get(f"{path}{sep}limit={page}&offset={offset}")
        batch = d.get("items") or []
        items += batch
        offset += len(batch)
        if not batch or offset >= (d.get("total") or 0):
            return items


def bank_config(bank: str) -> dict:
    d = get(bank_path(bank) + "/config")
    return d.get("config", d)


def resolve_strategy(conf: dict, strategy: str | None) -> dict:
    """The mission, extraction mode and custom instructions a retain strategy runs with.

    None means the bank sets nothing and the server default (HINDSIGHT_API_RETAIN_*) applies."""
    over = ((conf.get("retain_strategies") or {}).get(strategy) or {}) if strategy else {}
    pick = lambda k: over.get(k, conf.get(k))  # noqa: E731
    return {"mission": pick("retain_mission"), "extraction_mode": pick("retain_extraction_mode"), "custom_instructions": pick("retain_custom_instructions")}


def document(bank: str, doc_id: str, conf: dict) -> dict:
    d = get(bank_path(bank) + "/documents/" + urllib.parse.quote(doc_id, safe=""))
    rp = d.get("retain_params") or {}
    strategy = cfg.strategy_for({"strategy": rp.get("strategy"), "context": rp.get("context")}) or conf.get("retain_default_strategy")
    return {
        "document_id": d["id"],
        "content": d.get("original_text") or "",
        "context": rp.get("context") or "",
        "timestamp": rp.get("event_date") or d.get("created_at"),
        "strategy": strategy,
        "metadata": d.get("document_metadata") or rp.get("metadata"),
        "observation_scopes": d.get("observation_scopes") or rp.get("observation_scopes"),
        "tags": d.get("tags") or [],
    }


def write(obj, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    print(f"wrote {out}", file=sys.stderr)


def cmd_docs(a) -> None:
    rng, docs = random.Random(a.seed), []
    for bank in a.bank:
        listed = [d for d in paged(bank_path(bank) + "/documents") if a.min_chars <= (d.get("text_length") or 0) <= a.max_chars]
        rng.shuffle(listed)
        conf = bank_config(bank)
        for d in listed[: a.per_bank]:
            doc = document(bank, d["id"], conf)
            docs.append({"bank": bank, "doc_id": doc["document_id"], "chars": len(doc["content"]), **doc, **resolve_strategy(conf, doc["strategy"])})
        print(f"{bank}: {min(a.per_bank, len(listed))} of {len(listed)} documents in range", file=sys.stderr)
    for i, d in enumerate(docs):
        d["id"] = f"d{i:02d}"
    write({"docs": docs}, a.out or cfg.WORK / "docs.json")


def screen(page: dict) -> tuple[bool, str]:
    url = f"{cfg.BENCH_URL}{bank_path(page['bank'])}/mental-models/{page['mental_model_id']}/dry-run-refresh"
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            r = json.load(resp)
    except (urllib.error.URLError, TimeoutError) as e:
        return False, repr(e)[:120]
    calls = len((r.get("trace") or {}).get("llm_calls") or [])
    return calls > 0, f"{r.get('effective_mode')} {r.get('outcome')} {calls} LLM calls"


def cmd_pages(a) -> None:
    rng, candidates = random.Random(a.seed), []
    for bank in a.bank:
        mms = [m for m in paged(bank_path(bank) + "/mental-models?detail=content") if m.get("source_query")]
        rng.shuffle(mms)
        candidates += [{"bank": bank, "mental_model_id": m["id"], "name": m["name"], "question": m["source_query"]} for m in mms]
    if a.screen:
        rng.shuffle(candidates)
        kept, per_bank = [], {}
        for p in candidates:
            if len(kept) >= a.target:
                break
            if per_bank.get(p["bank"], 0) >= a.per_bank:
                continue
            ok, why = screen(p)
            print(f"{'keep' if ok else 'skip'} {p['bank']} {p['name'][:50]}: {why}", file=sys.stderr)
            if ok:
                kept.append(p)
                per_bank[p["bank"]] = per_bank.get(p["bank"], 0) + 1
        pages = kept
    else:
        pages, per_bank = [], {}
        for p in candidates:
            if per_bank.get(p["bank"], 0) < a.per_bank:
                pages.append(p)
                per_bank[p["bank"]] = per_bank.get(p["bank"], 0) + 1
    for i, p in enumerate(pages):
        p["id"] = f"p{i:02d}"
    write({"pages": pages}, a.out or cfg.WORK / "pages.json")


def cmd_queries(a) -> None:
    pages = json.loads(a.pages.read_text())["pages"]
    queries = [{"id": f"r{i:02d}", "kind": "page_refresh", "bank": p["bank"], "query": p["question"], "params": dict(PAGE_REFRESH_PARAMS)} for i, p in enumerate(pages)]
    write(queries, a.out or cfg.WORK / "queries.json")


def cmd_corpus(a) -> None:
    out = a.out or cfg.FOLLOWUP
    corpus = {}
    for bank in a.bank:
        listed = paged(bank_path(bank) + "/documents")
        conf = bank_config(bank)
        docs = sorted((document(bank, d["id"], conf) for d in listed), key=lambda d: d["timestamp"] or "")
        pages = [
            {k: m.get(k) for k in ("name", "source_query", "tags", "trigger", "max_tokens")}
            for m in paged(bank_path(bank) + "/mental-models?detail=content")
            if m.get("source_query")
        ]
        corpus[bank] = {"docs": docs, "pages": pages}
        write(get(bank_path(bank) + "/config"), out / f"config-{bank.split('::')[-1]}.json")
        print(f"{bank}: {len(docs)} documents, {sum(len(d['content']) for d in docs):,} chars, {len(pages)} pages", file=sys.stderr)
    write(corpus, out / "corpus.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("docs")
    d.add_argument("--bank", action="append", required=True)
    d.add_argument("--per-bank", type=int, default=3)
    d.add_argument("--min-chars", type=int, default=3000)
    d.add_argument("--max-chars", type=int, default=40000)
    p = sub.add_parser("pages")
    p.add_argument("--bank", action="append", required=True)
    p.add_argument("--per-bank", type=int, default=3)
    p.add_argument("--screen", action="store_true", help="keep only pages whose dry-run refresh on the bench calls the LLM")
    p.add_argument("--target", type=int, default=12)
    q = sub.add_parser("queries")
    q.add_argument("--pages", type=Path, required=True)
    c = sub.add_parser("corpus")
    c.add_argument("--bank", action="append", required=True)
    for s in (d, p, q, c):
        s.add_argument("-o", "--out", type=Path)
        s.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    {"docs": cmd_docs, "pages": cmd_pages, "queries": cmd_queries, "corpus": cmd_corpus}[a.cmd](a)


if __name__ == "__main__":
    main()
