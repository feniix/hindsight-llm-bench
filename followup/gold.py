"""Write a gold question set for the downstream follow-up from a whole corpus, with Gemini.

    python3 followup/gold.py CORPUS.json OUT.json [--n 25]

For each bank in CORPUS.json ({bank: {"docs": [{document_id, content, timestamp, ...}]}}),
Gemini reads every document in date order and writes N questions a teammate would
ask the project's memory, each with a gold answer and the ids of the documents
that support it. About a third ask for the current state of something that
changed across documents, the case consolidation is for. The questions never
see any model's extracted facts. Stdlib only; needs GEMINI_API_KEY (GEMINI_MODEL
overrides the model). The prompt sends whole corpora in one request; at about
200k tokens per corpus a request can take tens of minutes.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")
PROMPT = """You are writing an evaluation set for a project memory system. Below are all the documents (coding-agent session transcripts, notes, commit histories) the memory was built from, in date order, for the project `{bank}`.

Write {n} questions a teammate would realistically ask this project's memory, with a gold answer for each, answerable from these documents alone:
- about one third ask for the CURRENT state of something that changed over time across documents (a setting, a decision that was later revised, a status that moved), where only the latest document gives the right answer;
- about one third ask WHY a decision was made (the rationale stated in the documents);
- the rest ask for a specific fact: an identifier, value, file, command, issue/PR number or outcome.
Spread the questions across the documents and the time range. Each gold answer must be short (one to three sentences), correct as of the LAST document, and include the exact identifiers or values. Do not ask about anything the documents do not settle.

Return JSON: a list of objects with keys "question", "answer", "type" ("current_state" | "why" | "fact"), and "doc_ids" (the document_id values that support the answer).

DOCUMENTS:
{docs}"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--n", type=int, default=25)
    args = ap.parse_args()
    corpus = json.loads(args.corpus.read_text())
    out = json.loads(args.out.read_text()) if args.out.exists() else {}
    for bank, v in corpus.items():
        if bank in out:
            continue
        docs = "\n\n".join(
            f"<document document_id=\"{d['document_id']}\" date=\"{d['timestamp']}\" context=\"{d.get('context') or ''}\">\n{d['content']}\n</document>"
            for d in v["docs"]
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": PROMPT.format(bank=bank, n=args.n, docs=docs)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "question": {"type": "STRING"},
                            "answer": {"type": "STRING"},
                            "type": {"type": "STRING", "enum": ["current_state", "why", "fact"]},
                            "doc_ids": {"type": "ARRAY", "items": {"type": "STRING"}},
                        },
                        "required": ["question", "answer", "type", "doc_ids"],
                    },
                },
                "temperature": 0,
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
        r: dict = {}
        for attempt in range(5):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
            try:
                with urllib.request.urlopen(req, timeout=3000) as resp:
                    r = json.load(resp)
                break
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 4:
                    raise
                print(f"{bank}: retry after {e!r}", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        items = json.loads("".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"]))
        for i, q in enumerate(items):
            q["id"] = f"{bank.split('::')[-1][:3]}-q{i:02d}"
        out[bank] = {"model": MODEL, "usage": r.get("usageMetadata"), "questions": items}
        args.out.write_text(json.dumps(out, indent=1, ensure_ascii=False))
        print(f"{bank}: {len(items)} questions, {r.get('usageMetadata', {}).get('promptTokenCount')} prompt tokens", file=sys.stderr)


if __name__ == "__main__":
    main()
