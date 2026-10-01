# Sourced by every script: load config.env, apply defaults, resolve paths.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
[ -f config.env ] && { set -a; . ./config.env; set +a; }
: "${BENCH_WORK:=./work}" "${HINDSIGHT_IMAGE:=ghcr.io/vectorize-io/hindsight:0.10.2}"
: "${BENCH_CONTAINER:=hindsight-bench}" "${BENCH_VOLUME:=hindsight-bench-db}" "${BENCH_PORT:=18888}"
: "${FU_CONTAINER:=hindsight-fu}" "${FU_VOLUME:=hindsight-fu-db}"
: "${HINDSIGHT_SOURCE_URL:=http://localhost:8888}" "${BACKUP_ZIP:=$BENCH_WORK/backup/banks.zip}"
: "${SERVER_ENV:=./server.env}" "${CODEX_AUTH_DIR:=$BENCH_WORK/codex-auth}" "${PROXY_PORT:=8090}" "${SETTLE_SECONDS:=20}"
abs() { (cd "$(dirname "$1")" 2>/dev/null && echo "$(pwd)/$(basename "$1")") || echo "$1"; }
mkdir -p "$BENCH_WORK"
BENCH_WORK=$(abs "$BENCH_WORK"); SERVER_ENV=$(abs "$SERVER_ENV"); CODEX_AUTH_DIR=$(abs "$CODEX_AUTH_DIR"); BACKUP_ZIP=$(abs "$BACKUP_ZIP")
: "${HINDSIGHT_BENCH_URL:=http://localhost:$BENCH_PORT}"
export BENCH_WORK HINDSIGHT_BENCH_URL HINDSIGHT_SOURCE_URL BENCH_CONTAINER

step() { echo "== $(date +%T) $*"; }
die() { echo "ABORT: $*" >&2; exit 1; }
need_proxy() { lsof -iTCP:"$PROXY_PORT" -sTCP:LISTEN >/dev/null 2>&1 || die "proxy not listening on $PROXY_PORT (python3 proxy/codex_proxy.py $PROXY_PORT)"; }
need_file() { [ -f "$1" ] || die "missing $1${2:+ ($2)}"; }

# wait_healthy CONTAINER: block until the API answers, or show the logs and fail.
wait_healthy() {
  for _ in $(seq 1 240); do
    curl -sf "localhost:$BENCH_PORT/health" >/dev/null 2>&1 && return 0
    docker ps -q -f name="^$1\$" -f status=running | grep -q . || { docker logs --tail 30 "$1"; return 1; }
    sleep 3
  done
  return 1
}

# export_traces CONTAINER SINCE_ISO OUT.jsonl: dump llm_requests rows written since SINCE.
export_traces() {
  docker exec -i "$1" python - "$2" > "$3" <<'PY'
import asyncio, asyncpg, json, sys
from datetime import datetime
since = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
def enc(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v if isinstance(v, (int, float, str, type(None), dict, list, bool)) else str(v)
async def m():
    c = await asyncpg.connect("postgresql://hindsight:hindsight@127.0.0.1:5432/hindsight")
    async with c.transaction():
        async for r in c.cursor("select * from llm_requests where started_at >= $1 order by started_at", since):
            print(json.dumps({k: enc(v) for k, v in dict(r).items()}, ensure_ascii=False))
asyncio.run(m())
PY
}
