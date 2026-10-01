"""Send one Codex /responses request and print the usage block from response.completed, if any.

    CODEX_AUTH=work/codex-auth/auth.json python3 proxy/codex_usage_probe.py MODEL

Use it to confirm that your Codex endpoint returns usage (including reasoning
tokens) before relying on the proxy. Reads the bench login's access token
read-only; never refreshes it."""
import argparse, json, os, uuid, urllib.request
from pathlib import Path

def call(model, instructions, user, effort="low", summary="concise"):
    auth = json.loads(Path(os.environ.get("CODEX_AUTH", "work/codex-auth/auth.json")).expanduser().read_text())
    tok, acct = auth["tokens"]["access_token"], auth["tokens"].get("account_id", "")
    payload = {"model": model, "instructions": instructions,
               "input": [{"type": "message", "role": "user", "content": user}],
               "tools": [], "tool_choice": "auto", "parallel_tool_calls": True,
               "reasoning": {"effort": effort, "summary": summary}, "store": False, "stream": True,
               "include": ["reasoning.encrypted_content"], "prompt_cache_key": str(uuid.uuid4())}
    req = urllib.request.Request(os.environ.get("CODEX_URL", "https://chatgpt.com/backend-api/codex/responses"), data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "OpenAI-Account-ID": acct,
                 "Origin": "https://chatgpt.com", "originator": "codex_cli_rs", "Accept": "text/event-stream"})
    usage, text, ev = None, [], None
    with urllib.request.urlopen(req, timeout=300) as r:
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line.startswith("event: "): ev = line[7:]
            elif line.startswith("data: "):
                try: d = json.loads(line[6:])
                except ValueError: continue
                if ev == "response.output_text.delta": text.append(d.get("delta", ""))
                if ev == "response.completed": usage = (d.get("response") or {}).get("usage")
    return usage, "".join(text)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("--effort", default="low")
    a = ap.parse_args()
    u, t = call(a.model, "You are terse.", "Reply with the single word: ok", effort=a.effort)
    print(json.dumps(u), "|", t[:40])
