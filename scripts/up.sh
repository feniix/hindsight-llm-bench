#!/bin/bash
# up.sh MODEL [restore]: (re)create the bench container with MODEL as the model for one operation.
#   ROLE=REFLECT|RETAIN|CONSOLIDATION|MENTAL_MODEL_REFRESH selects HINDSIGHT_API_<ROLE>_LLM_MODEL (default REFLECT).
#   EXTRA_ENV=KEY=VALUE adds one more server variable.
#   restore: wipe nothing, but run migrations and restore BACKUP_ZIP into the volume first.
# Only ever touches $BENCH_CONTAINER and $BENCH_VOLUME.
set -eu
. "$(dirname "$0")/common.sh"
MODEL=${1:?usage: up.sh MODEL [restore]}; RESTORE=${2:-}
need_file "$SERVER_ENV" "copy server.example.env"
CMD="exec /app/start-all.sh"
if [ "$RESTORE" = restore ]; then
  need_file "$BACKUP_ZIP" "hindsight-admin backup of the source install"
  CMD="hindsight-admin run-db-migration && hindsight-admin restore /backup/$(basename "$BACKUP_ZIP") --yes && exec /app/start-all.sh"
fi
docker rm -f "$BENCH_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$BENCH_CONTAINER" --env-file "$SERVER_ENV" \
  -e HINDSIGHT_API_${ROLE:-REFLECT}_LLM_MODEL="$MODEL" ${EXTRA_ENV:+-e "$EXTRA_ENV"} \
  -e HINDSIGHT_API_LLM_BASE_URL="http://host.docker.internal:$PROXY_PORT/backend-api" \
  -p "127.0.0.1:$BENCH_PORT:8888" \
  -v "$BENCH_VOLUME":/home/hindsight/.pg0 \
  -v "$CODEX_AUTH_DIR":/home/hindsight/codex-auth \
  -v "$(dirname "$BACKUP_ZIP")":/backup:ro \
  "$HINDSIGHT_IMAGE" sh -c "$CMD" >/dev/null
wait_healthy "$BENCH_CONTAINER" || die "bench not healthy"
echo "bench up: $(curl -s "localhost:$BENCH_PORT/version" | python3 -c 'import json,sys;print(json.load(sys.stdin)["api_version"])') ${ROLE:-REFLECT}=$(docker exec "$BENCH_CONTAINER" printenv HINDSIGHT_API_${ROLE:-REFLECT}_LLM_MODEL)"
