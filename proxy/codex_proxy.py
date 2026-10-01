"""Logging pass-through proxy for the Codex /responses API (bench only).

    python3 proxy/codex_proxy.py [PORT]      # default 8090; log: $PROXY_LOG or $BENCH_WORK/usage.jsonl

The bench's Hindsight points HINDSIGHT_API_LLM_BASE_URL at
http://host.docker.internal:PORT/backend-api; this forwards every request to
https://chatgpt.com unchanged, streams the SSE response back as it arrives, and
appends one JSON line per call to usage.jsonl with the model, timings and the
`usage` block from `response.completed` (input, cached, output and reasoning
tokens), which Hindsight's Codex provider does not record. Stdlib only.
"""

import http.client
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "chatgpt.com"
LOG = Path(os.environ.get("PROXY_LOG") or Path(os.environ.get("BENCH_WORK", Path(__file__).resolve().parent.parent / "work")) / "usage.jsonl").expanduser()
LOCK = threading.Lock()
HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding", "keep-alive"}


def log(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, LOG.open("a") as f:
        f.write(json.dumps(row) + "\n")


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - quiet
        pass

    def _forward(self, method: str) -> None:
        started = time.time()
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else None
        try:
            req = json.loads(body) if body else {}
        except ValueError:
            req = {}
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Accept-Encoding"] = "identity"
        conn = http.client.HTTPSConnection(UPSTREAM, timeout=600)
        row = {
            "ts": datetime.now().astimezone().isoformat(),
            "path": self.path,
            "model": req.get("model"),
            "effort": (req.get("reasoning") or {}).get("effort"),
            "tool_choice": req.get("tool_choice") if isinstance(req.get("tool_choice"), str) else (req.get("tool_choice") or {}).get("name"),
            "n_tools": len(req.get("tools") or []),
            "instructions_chars": len(req.get("instructions") or ""),
            "input_chars": len(json.dumps(req.get("input") or "")),
        }
        first_byte = None
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            row["status"] = resp.status
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            event, buf = None, b""
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                if first_byte is None:
                    first_byte = time.time()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode("utf-8", "replace").rstrip("\r")
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: ") and event in ("response.completed", "response.failed", "error"):
                        try:
                            data = json.loads(line[6:])
                        except ValueError:
                            continue
                        r = data.get("response") or {}
                        if event == "response.completed":
                            u = r.get("usage") or {}
                            u.pop("attribution", None)
                            row["usage"] = u
                            row["resp_model"] = r.get("model")
                        else:
                            row["error"] = json.dumps(data)[:500]
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            if resp.status >= 400:
                row.setdefault("error", buf.decode("utf-8", "replace")[:500])
        except Exception as e:  # noqa: BLE001 - log and surface as 502
            row["error"] = repr(e)[:500]
            try:
                self.send_error(502, "proxy error")
            except Exception:  # noqa: BLE001
                pass
        finally:
            conn.close()
            row["seconds"] = round(time.time() - started, 3)
            row["ttfb"] = round(first_byte - started, 3) if first_byte else None
            log(row)

    def do_POST(self):
        self._forward("POST")

    def do_GET(self):
        self._forward("GET")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    ThreadingHTTPServer(("127.0.0.1", port), Proxy).serve_forever()
