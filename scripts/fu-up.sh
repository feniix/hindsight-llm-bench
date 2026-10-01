#!/bin/bash
# fu-up.sh RETAIN_MODEL WORKER(true|false): (re)create the follow-up container on its own volume.
# Only RETAIN_LLM_MODEL varies; reflect, consolidation and refresh use the server's global model.
set -eu
. "$(dirname "$0")/common.sh"
need_file "$SERVER_ENV" "copy server.example.env"
docker rm -f "$FU_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$FU_CONTAINER" --env-file "$SERVER_ENV" \
  -e HINDSIGHT_API_RETAIN_LLM_MODEL="${1:?usage: fu-up.sh RETAIN_MODEL true|false}" \
  -e HINDSIGHT_API_WORKER_ENABLED="${2:?usage: fu-up.sh RETAIN_MODEL true|false}" \
  -e HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION=false \
  -e HINDSIGHT_API_WORKER_ID="$FU_CONTAINER" \
  -e HINDSIGHT_API_LLM_BASE_URL="http://host.docker.internal:$PROXY_PORT/backend-api" \
  -p "127.0.0.1:$BENCH_PORT:8888" \
  -v "$FU_VOLUME":/home/hindsight/.pg0 \
  -v "$CODEX_AUTH_DIR":/home/hindsight/codex-auth \
  "$HINDSIGHT_IMAGE" >/dev/null
wait_healthy "$FU_CONTAINER" || die "follow-up container not healthy"
echo "fu up: retain=$(docker exec "$FU_CONTAINER" printenv HINDSIGHT_API_RETAIN_LLM_MODEL) worker=$2"
