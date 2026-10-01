"""Blind-judge retain pairs with Gemini, as a second judge family next to Claude.

    python3 retain/gemini_judge.py PAIRS_DIR [--model gemini-3.1-pro-preview]

Reads rubric.md and every <id>.json pair written by compare.py --pairs, and writes
PAIRS_DIR/gemini/<id>.verdict.json in the same {"winner", "why"} shape, so
`compare.py BASE CAND --verdicts PAIRS_DIR/gemini` scores it (copy key.json in,
which this does). key.json is never read for judging. Cached: existing verdicts
are kept. The pairs carry bank documents, which Gemini receives. Stdlib only.
"""

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent


def call(model: str, prompt: str, tries: int = 5) -> dict:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {"winner": {"type": "STRING", "enum": ["A", "B", "tie"]}, "why": {"type": "STRING"}},
                "required": ["winner", "why"],
            },
            "temperature": 0,
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for attempt in range(tries):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]}
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                r = json.load(resp)
            return json.loads("".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"]))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
            if attempt < tries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise
    raise RuntimeError("unreachable")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pairs", type=Path)
    ap.add_argument("--model", default="gemini-3.1-pro-preview")
    args = ap.parse_args()
    rubric = (HERE / "rubric.md").read_text()
    out = args.pairs / "gemini"
    out.mkdir(exist_ok=True)
    shutil.copy(args.pairs / "key.json", out / "key.json")
    ids = sorted(p.stem for p in args.pairs.glob("*.json") if p.name != "key.json" and not p.name.endswith(".verdict.json"))

    def one(i: str) -> str:
        target = out / f"{i}.verdict.json"
        if target.exists():
            return f"{i}: cached"
        pair = json.loads((args.pairs / f"{i}.json").read_text())
        prompt = (
            "You are a blind judge. Follow this rubric exactly; ignore its instruction about writing a file and "
            "return the verdict object instead.\n\n" + rubric + "\n\nPAIR FILE:\n" + json.dumps(pair, ensure_ascii=False, indent=1)
        )
        v = call(args.model, prompt)
        target.write_text(json.dumps({"winner": v["winner"], "why": v["why"], "judge": args.model}))
        return f"{i}: {v['winner']}"

    with ThreadPoolExecutor(4) as pool:
        for line in pool.map(one, ids):
            print(line, file=sys.stderr)


if __name__ == "__main__":
    main()
