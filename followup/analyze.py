"""Analyze the downstream follow-up: cost, answer accuracy against the gold set, page pairs.

    python3 followup/analyze.py grade  OUT_DIR             # grade every QA answer against its gold answer (Jev and Gemini)
    python3 followup/analyze.py pages  OUT_DIR PAIRS_DIR   # blind page pairs (base bank vs candidate bank) for judges
    python3 followup/analyze.py report OUT_DIR             # cost and accuracy tables

OUT_DIR is the follow-up's out/ directory (retain-*, consolidate-*, pages-*, qa-* JSON,
usage.jsonl from the proxy, llm_requests.jsonl). Gold questions come from
OUT_DIR/../gold.json and the corpora from OUT_DIR/../plan.json: {"corpora": {KEY:
SOURCE_BANK}}, whose banks are named fu-KEY-base and fu-KEY-cand. Without
llm_requests.jsonl the report skips the cost section.

Cost: llm_requests rows carry bank_id and operation but no Codex token counts; proxy rows
carry exact tokens but no bank. Each llm_requests row is matched to the proxy row with the
same model whose start time is closest (within 3 s), so tokens are attributed per bank and
operation even though the four banks consolidated concurrently.

Grading: each answer is graded on its own, never next to the other bank's answer, as
correct / partial / wrong / declined (says the memory does not contain it). Jev
(jev-1.13.0) and Gemini grade independently; score correct=1, partial=0.5. Per question
the two banks are compared (sign test over questions where they differ). Stdlib only.
"""

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

JEV_MODEL = os.environ.get("JEV_MODEL", "jev-1.13.0")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")
GRADES = ["correct", "partial", "wrong", "declined"]
SCORE = {"correct": 1.0, "partial": 0.5, "wrong": 0.0, "declined": 0.0}
CRITERIA = {
    "correct": "The answer states what the gold answer states, including its key identifiers or values, and contradicts nothing in it",
    "partial": "The answer gets part of the gold answer right but misses or blurs a key part of it, without contradicting it",
    "wrong": "The answer contradicts the gold answer or gives a different value, decision or current state",
    "declined": "The answer says the memory does not contain the information, or gives no substantive answer",
}


def post(url: str, body: dict, headers: dict, timeout: float = 600) -> dict:
    for attempt in range(6):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < 5:
                time.sleep(3 * 2**attempt)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError):
            if attempt < 5:
                time.sleep(3 * 2**attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def grade_jev(question: str, gold: str, answer: str) -> dict:
    body = {
        "state": {"question": question, "gold_answer": gold, "answer": answer or ""},
        "model": JEV_MODEL,
        "questions": {"grade": {"type": "choice", "instructions": "Grade `answer` to `question` against `gold_answer`, which is correct as of the latest information.", "criteria": CRITERIA}},
    }
    r = post("https://api.typesafe.ai/v1/systemone", body, {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"})
    a = r["answers"]["grade"]
    return {"grade": a["choice"], "probabilities": a["probabilities"], "confidence": a["confidence"]}


def grade_gemini(question: str, gold: str, answer: str) -> dict:
    prompt = (
        "Grade an answer from a project memory system against a gold answer that is correct as of the latest information.\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in CRITERIA.items())
        + f"\n\nQUESTION:\n{question}\n\nGOLD ANSWER:\n{gold}\n\nANSWER TO GRADE:\n{answer or '(empty)'}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {"type": "OBJECT", "properties": {"grade": {"type": "STRING", "enum": GRADES}, "why": {"type": "STRING"}}, "required": ["grade", "why"]},
            "temperature": 0,
        },
    }
    r = post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent", body, {"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    return json.loads("".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"]))


def corpora(out: Path) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """(banks per corpus key as (base, candidate), source bank per corpus key) from OUT_DIR/../plan.json."""
    plan = json.loads((out.parent / "plan.json").read_text())["corpora"]
    return {k: (f"fu-{k}-base", f"fu-{k}-cand") for k in plan}, dict(plan)


def grade(out: Path) -> None:
    BANKS, GOLD_BANK = corpora(out)
    gold = json.loads((out.parent / "gold.json").read_text())
    path = out / "grades.json"
    grades = json.loads(path.read_text()) if path.exists() else {}
    jobs = []
    for corpus, banks in BANKS.items():
        gq = {q["id"]: q for q in gold[GOLD_BANK[corpus]]["questions"]}
        for bank in banks:
            for row in json.loads((out / f"qa-{bank}.json").read_text())["answers"]:
                key = f"{bank}/{row['id']}"
                if key not in grades:
                    jobs.append((key, gq[row["id"]], row))

    def one(job):
        key, q, row = job
        ans = row["answer"] if not row["error"] else ""
        return key, {"type": q["type"], "jev": grade_jev(q["question"], q["answer"], ans), "gemini": grade_gemini(q["question"], q["answer"], ans), "error": row["error"]}

    with ThreadPoolExecutor(6) as pool:
        for key, g in pool.map(one, jobs):
            grades[key] = g
            path.write_text(json.dumps(grades, indent=1, ensure_ascii=False))
            print(f"{key}: jev={g['jev']['grade']} gemini={g['gemini']['grade']}", file=sys.stderr)
    print(f"{len(grades)} answers graded")


def sign_p(b: int, w: int) -> float:
    n, k = b + w, min(b, w)
    return min(1.0, 2 * sum(math.comb(n, j) for j in range(k + 1)) / 2**n) if n else 1.0


def boot_ci(d: list[float], n: int = 20_000) -> tuple[float, float]:
    """95% bootstrap interval of the mean, questions resampled."""
    rng = random.Random(0)
    means = sorted(sum(rng.choices(d, k=len(d))) / len(d) for _ in range(n))
    return means[int(n * 0.025)], means[int(n * 0.975)]


def pages(out: Path, pairs: Path) -> None:
    BANKS, _ = corpora(out)
    pairs.mkdir(parents=True, exist_ok=True)
    rng, key = random.Random(42), {}
    for corpus, (base, cand) in BANKS.items():
        a = {p["name"]: p for p in json.loads((out / f"pages-{base}.json").read_text())["pages"]}
        b = {p["name"]: p for p in json.loads((out / f"pages-{cand}.json").read_text())["pages"]}
        for i, name in enumerate(sorted(set(a) & set(b))):
            pid = f"{corpus}-p{i:02d}"
            flip = rng.random() < 0.5
            x, y = (b[name], a[name]) if flip else (a[name], b[name])
            key[pid] = "A" if flip else "B"  # letter of the candidate-retained bank
            (pairs / f"{pid}.json").write_text(json.dumps({"id": pid, "kind": "knowledge_page", "question": a[name]["question"], "A": x["content"], "B": y["content"]}, indent=1, ensure_ascii=False))
    (pairs / "key.json").write_text(json.dumps(key, indent=1))
    print(f"wrote {len(key)} page pairs to {pairs}")


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def report(out: Path) -> None:
    BANKS, _ = corpora(out)
    if (out / "llm_requests.jsonl").exists():
        cost(out)
    else:
        print("## Cost: skipped (no llm_requests.jsonl)")
    bank_state(out, BANKS)
    accuracy(out, BANKS)


def cost(out: Path) -> None:
    # --- cost per bank and operation -------------------------------------------------
    proxy = [json.loads(l) for l in (out / "usage.jsonl").read_text().splitlines() if l.strip()]
    by_model = defaultdict(list)
    for p in proxy:
        by_model[p.get("model")].append((ts(p["ts"]), p))
    for v in by_model.values():
        v.sort(key=lambda t: t[0])
    reqs = [json.loads(l) for l in (out / "llm_requests.jsonl").read_text().splitlines() if l.strip()]
    used, agg = set(), defaultdict(lambda: defaultdict(float))
    for r in reqs:
        bank, op = r.get("bank_id"), r.get("operation")
        if not bank or not bank.startswith("fu-"):
            continue
        a = agg[(bank, op)]
        a["calls"] += 1
        a["llm_seconds"] += (r.get("duration_ms") or 0) / 1000
        start, best = ts(r["started_at"]), None
        for t, p in by_model.get(r.get("model"), []):
            if abs(t - start) <= 3 and id(p) not in used and (best is None or abs(t - start) < abs(best[0] - start)):
                best = (t, p)
        if best:
            used.add(id(best[1]))
            u = best[1].get("usage") or {}
            a["matched"] += 1
            a["input"] += u.get("input_tokens", 0)
            a["output"] += u.get("output_tokens", 0)
            a["reasoning"] += (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
    print("## Cost by bank and operation (calls and LLM seconds from llm_requests; tokens from the proxy match)")
    print(f"{'bank':11s} {'operation':22s} {'calls':>6s} {'matched':>7s} {'LLM s':>8s} {'input':>10s} {'output':>9s} {'reasoning':>9s}")
    for (bank, op), a in sorted(agg.items()):
        print(f"{bank:11s} {op or '-':22s} {a['calls']:6.0f} {a['matched']:7.0f} {a['llm_seconds']:8.0f} {a['input']:10,.0f} {a['output']:9,.0f} {a['reasoning']:9,.0f}")
    print(f"proxy rows matched: {len(used)} of {len(proxy)}")


def bank_state(out: Path, BANKS: dict) -> None:
    print("\n## Banks after consolidation")
    for corpus, banks in BANKS.items():
        for bank in banks:
            c = json.loads((out / f"consolidate-{bank}.json").read_text())["stats_after"]
            units = sum((c.get("nodes_by_fact_type") or {}).values())
            print(f"{bank:11s} nodes {units:4d}  observations {c.get('total_observations'):4d}  failed_consolidation {c.get('failed_consolidation')}  documents {c.get('total_documents')}")


def accuracy(out: Path, BANKS: dict) -> None:
    gpath = out / "grades.json"
    if not gpath.exists():
        return
    grades = json.loads(gpath.read_text())
    print("\n## Gold Q&A accuracy (score: correct 1, partial 0.5)")
    for judge in ("jev", "gemini"):
        print(f"\n### graded by {judge}")
        for corpus, (base_bank, cand_bank) in BANKS.items():
            ids = sorted({k.split("/")[1] for k in grades if k.startswith(base_bank + "/")} & {k.split("/")[1] for k in grades if k.startswith(cand_bank + "/")})
            types = defaultdict(lambda: [0, 0, 0])
            sa = sb = 0.0
            dist = {base_bank: defaultdict(int), cand_bank: defaultdict(int)}
            for i in ids:
                ga, gb = grades[f"{base_bank}/{i}"], grades[f"{cand_bank}/{i}"]
                xa, xb = SCORE[ga[judge]["grade"]], SCORE[gb[judge]["grade"]]
                sa, sb = sa + xa, sb + xb
                dist[base_bank][ga[judge]["grade"]] += 1
                dist[cand_bank][gb[judge]["grade"]] += 1
                t = types[ga["type"]]
                t[0 if xb > xa else 2 if xb < xa else 1] += 1
            tot = [sum(v[j] for v in types.values()) for j in range(3)]
            print(f"{corpus}: n={len(ids)}  base {sa / len(ids):.2f}  cand {sb / len(ids):.2f}  cand better/same/worse {tot[0]}/{tot[1]}/{tot[2]} p={sign_p(tot[0], tot[2]):.3f}")
            print(f"     base grades {dict(dist[base_bank])}  cand grades {dict(dist[cand_bank])}")
            for ty, v in sorted(types.items()):
                print(f"     {ty:14s} cand better/same/worse {v[0]}/{v[1]}/{v[2]}")
    # --- pooled over corpora: per-question score difference, candidate minus base -------
    print("\n## All corpora pooled (candidate-retained minus base-retained, per question)")
    diffs = {}
    for judge in ("jev", "gemini"):
        d = []
        for base_bank, cand_bank in BANKS.values():
            for k in sorted(grades):
                if k.startswith(base_bank + "/") and f"{cand_bank}/{k.split('/')[1]}" in grades:
                    d.append(SCORE[grades[f"{cand_bank}/{k.split('/')[1]}"][judge]["grade"]] - SCORE[grades[k][judge]["grade"]])
        diffs[judge] = d
    diffs["mean of both"] = [(a + b) / 2 for a, b in zip(diffs["jev"], diffs["gemini"])]
    for name, d in diffs.items():
        b, s_, w = sum(x > 0 for x in d), sum(x == 0 for x in d), sum(x < 0 for x in d)
        lo, hi = boot_ci(d)
        print(f"{name:13s} n={len(d)}  {sum(d) / len(d):+.3f} [{lo:+.3f}, {hi:+.3f}]  better/same/worse {b}/{s_}/{w} p={sign_p(b, w):.3f}")

    agree = sum(1 for g in grades.values() if g["jev"]["grade"] == g["gemini"]["grade"])
    print(f"\nJev and Gemini give the same grade on {agree}/{len(grades)} answers")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["grade", "pages", "report"])
    ap.add_argument("out", type=Path)
    ap.add_argument("pairs", type=Path, nargs="?")
    a = ap.parse_args()
    if a.step == "grade":
        grade(a.out)
    elif a.step == "pages":
        pages(a.out, a.pairs)
    else:
        report(a.out)


if __name__ == "__main__":
    main()
