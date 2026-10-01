"""Score retain extractions fact by fact: Gemini writes a reference, Jev judges each fact.

    python3 retain/factscore.py DOCS.json OUT_DIR base,cand1,cand2 [--ids d00,d05] [--summary]

The tags name retain runs in $BENCH_WORK/runs/retain/TAG-bN.json.

Two stages, both cached in OUT_DIR so a rerun only calls what is missing:

1. Reference (Gemini, GEMINI_API_KEY): for each document, a list of the key items
   its retain mission asks to keep, written without seeing any model's facts. It
   is the fixed denominator for coverage, the same for every model.
2. Judgments (TypeSafe Jev, TYPESAFE_API_KEY): one System One request per model
   per document, state = {mission, document, facts, key_items}, asking for every
   fact whether the document supports it (supports / contradicts / says_nothing),
   whether it is what the mission asks to keep, whether it is transient chatter,
   whether it restates another fact, and how well it stands on its own; and for
   every key item whether any fact captures it.

--summary prints per-model precision, relevance, chatter, redundancy,
standalone, coverage and the facts per document. The documents are bank content
and both services receive them; keep OUT_DIR under $BENCH_WORK (git-ignored).
GEMINI_MODEL and JEV_MODEL override the models below. Stdlib only.
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bench_config as cfg  # noqa: E402

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")

REFERENCE_PROMPT = """You are building a reference answer key for a memory system's fact extraction.

The system reads the DOCUMENT below and must store, as standalone facts, what this RETAIN MISSION asks it to keep:

RETAIN MISSION:
{mission}

List every distinct item in the document that the mission asks to keep: decisions and their reasons, conventions, tooling or configuration choices, gotchas, outcomes, and the exact identifiers, values, file names and issue references the mission says to preserve. One item per entry, each a single self-contained sentence of at most 40 words that names its subject. Do not include transient chatter (retries, typos, dead ends that were not decisions) or anything the document does not state. If the document holds nothing the mission asks to keep, return an empty list.

DOCUMENT (context: {context}):
<<<
{document}
>>>"""


def post(url: str, body: dict, headers: dict, timeout: float = 300, tries: int = 5) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:400]!r}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
    raise RuntimeError("unreachable")


def reference(doc: dict, out: Path) -> list[str]:
    path = out / "reference" / f"{doc['id']}.json"
    if path.exists():
        return json.loads(path.read_text())["items"]
    prompt = REFERENCE_PROMPT.format(
        mission=doc.get("mission") or "", context=doc.get("context") or "", document=doc["content"]
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {"type": "ARRAY", "items": {"type": "STRING"}},
            "temperature": 0,
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    resp = post(url, body, {"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    text = "".join(p.get("text", "") for p in resp["candidates"][0]["content"]["parts"])
    items = [s.strip() for s in json.loads(text) if s.strip()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": GEMINI_MODEL, "items": items, "usage": resp.get("usageMetadata")}, indent=1))
    return items


def questions(n_facts: int, n_items: int) -> dict:
    q = {}
    for i in range(n_facts):
        f = f"`facts[{i}]`"
        q[f"f{i}_support"] = {
            "type": "choice",
            "instructions": f"How does `document` relate to the fact {f}?",
            "criteria": {
                "supports": f"The document states {f} or directly implies it, including its names, values and attributions",
                "contradicts": f"The document states the opposite of {f}, or gets a name, value, actor or outcome in it wrong",
                "says_nothing": f"The document does not address what {f} asserts",
            },
        }
        q[f"f{i}_relevant"] = {
            "type": "noul",
            "instructions": f"Is {f} something `mission` asks the memory system to keep?",
            "criteria": {"true": "It is the kind of item the mission asks to keep", "false": "The mission does not ask to keep it"},
        }
        q[f"f{i}_chatter"] = {
            "type": "noul",
            "instructions": f"Is {f} transient chatter rather than a lasting fact: a retry, a typo, a dead end that was not a decision, or step-by-step narration of the session?",
        }
        q[f"f{i}_redundant"] = {
            "type": "noul",
            "instructions": f"Does {f} restate what another entry in `facts` already says, adding nothing new?",
        }
        q[f"f{i}_standalone"] = {
            "type": "score",
            "instructions": f"Read {f} alone, without `document`. How well does it stand on its own as a memory someone reads months later?",
            "criteria": [
                "Unclear alone: vague summary, pronouns or references without referents, missing its subject",
                "Mostly clear: the subject is named but a key identifier, value or reason is missing",
                "Fully clear: names its subject, the concrete identifiers or values, and the reason where one exists",
            ],
        }
    for j in range(n_items):
        q[f"k{j}_covered"] = {
            "type": "noul",
            "instructions": f"Is the key item `key_items[{j}]` captured by at least one entry of `facts`, in substance if not in wording?",
        }
    return q


def judge(tag: str, doc: dict, facts: list[str], items: list[str], out: Path) -> dict:
    path = out / "jev" / f"{tag}-{doc['id']}.json"
    if path.exists():
        return json.loads(path.read_text())
    state = {"mission": doc.get("mission") or "", "document": doc["content"], "facts": facts, "key_items": items}
    body = {"state": state, "model": JEV_MODEL, "questions": questions(len(facts), len(items))}
    resp = post("https://api.typesafe.ai/v1/systemone", body, {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"})
    record = {"tag": tag, "id": doc["id"], "facts": facts, "key_items": items, "response": resp}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1))
    return record


def load_facts(tag: str) -> dict[str, list[str]]:
    rows = {}
    for p in sorted(cfg.RETAIN_RUNS.glob(f"{tag}-b*.json")):
        for r in json.loads(p.read_text())["results"]:
            if not r["error"]:
                rows[r["id"]] = [f["text"] for f in r["facts"]]
    return rows


def summarize(tags: list[str], out: Path) -> None:
    print(f"{'model':5s} {'docs':>4s} {'facts':>5s} {'/doc':>5s}  {'supported':>9s} {'contra':>6s} {'relevant':>8s} {'chatter':>7s} {'redund':>6s} {'standalone':>10s}  {'coverage':>8s}")
    for tag in tags:
        recs = [json.loads(p.read_text()) for p in sorted((out / "jev").glob(f"{tag}-d*.json"))]
        if not recs:
            continue
        sup = rel = cha = red = sta = 0.0
        contra = nf = 0
        cov = []
        for r in recs:
            a = r["response"]["answers"]
            for i in range(len(r["facts"])):
                nf += 1
                s = a[f"f{i}_support"]
                sup += s["probabilities"]["supports"]
                contra += s["choice"] == "contradicts"
                rel += a[f"f{i}_relevant"]["noul"]
                cha += a[f"f{i}_chatter"]["noul"]
                red += a[f"f{i}_redundant"]["noul"]
                sta += a[f"f{i}_standalone"]["score"]
            if r["key_items"]:
                cov.append(statistics.fmean(a[f"k{j}_covered"]["noul"] for j in range(len(r["key_items"]))))
        n = max(nf, 1)
        print(
            f"{tag:5s} {len(recs):4d} {nf:5d} {nf / len(recs):5.1f}  {sup / n:9.2f} {contra:6d} {rel / n:8.2f} "
            f"{cha / n:7.2f} {red / n:6.2f} {sta / n:10.2f}  {statistics.fmean(cov) if cov else float('nan'):8.2f}"
        )
    print("supported/relevant/chatter/redund: mean probability per fact; standalone: 0-2; coverage: mean share of key items captured per document")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docs", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("tags")
    ap.add_argument("--ids")
    ap.add_argument("--summary", action="store_true", help="only print the summary from cached results")
    args = ap.parse_args()
    tags = args.tags.split(",")
    if args.summary:
        summarize(tags, args.out)
        return
    docs = {d["id"]: d for d in json.loads(args.docs.read_text())["docs"]}
    ids = args.ids.split(",") if args.ids else sorted(docs)
    facts = {t: load_facts(t) for t in tags}
    ids = [i for i in ids if all(i in facts[t] for t in tags)]
    print(f"{len(ids)} documents extracted by all of {tags}", file=sys.stderr)

    with ThreadPoolExecutor(4) as pool:
        refs = dict(zip(ids, pool.map(lambda i: reference(docs[i], args.out), ids)))
    for i in ids:
        print(f"reference {i}: {len(refs[i])} key items", file=sys.stderr)

    jobs = [(t, i) for t in tags for i in ids]
    with ThreadPoolExecutor(6) as pool:
        for rec in pool.map(lambda ti: judge(ti[0], docs[ti[1]], facts[ti[0]][ti[1]], refs[ti[1]], args.out), jobs):
            u = rec["response"].get("usage") or {}
            print(f"judged {rec['tag']}-{rec['id']}: {len(rec['facts'])} facts, {len(rec['key_items'])} items, {u.get('input_tokens')} input tokens", file=sys.stderr)
    summarize(tags, args.out)


if __name__ == "__main__":
    main()
