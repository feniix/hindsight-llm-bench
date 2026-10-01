#!/bin/bash
# run-main.sh: the main comparison on the throwaway bench, from a fresh restore.
#   phase A: reflect queries + knowledge-page dry-run refreshes, switching REFLECT_LLM_MODEL
#   phase B: retain dry-run extraction on real documents, switching RETAIN_LLM_MODEL
# Blocks, model order and item ids come from $BENCH_WORK/plan.json (prepare/plan.py).
# Every LLM call goes through the proxy (usage.jsonl: exact input/cached/output/reasoning
# tokens); llm_requests keeps full prompts and outputs; /metrics and the server log are
# saved per run. Set PHASES="A" or PHASES="B" to run one phase.
set -u
. "$(dirname "$0")/common.sh"
PLAN=$BENCH_WORK/plan.json
OUT=$BENCH_WORK/main-$(date +%Y%m%d-%H%M)
need_file "$PLAN" "python3 prepare/plan.py ..."
need_proxy
mkdir -p "$OUT/server" "$OUT/metrics" "$BENCH_WORK/logs"

model()  { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['models'][sys.argv[2]])" "$PLAN" "$1"; }
order()  { python3 -c "import json,sys;print(' '.join(json.load(open(sys.argv[1]))['orders'][int(sys.argv[2])-1]))" "$PLAN" "$1"; }
ids()    { python3 -c "import json,sys;print(','.join(json.load(open(sys.argv[1])).get(sys.argv[2],[[]]*99)[int(sys.argv[3])-1]))" "$PLAN" "$1" "$2"; }
nblocks(){ python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['orders']))" "$PLAN"; }
base()   { python3 -c "import json,sys;p=json.load(open(sys.argv[1]));print(p['models'][p['base']])" "$PLAN"; }
snap()   { curl -s "localhost:$BENCH_PORT/metrics" > "$OUT/metrics/$1.prom" 2>/dev/null; docker logs "$BENCH_CONTAINER" > "$OUT/server/$1.log" 2>&1; }
okcount(){ python3 -c "import json,sys;r=json.load(open(sys.argv[1]))['results'];print(sum(1 for x in r if not x.get('error')))" "$1" 2>/dev/null || echo 0; }
check()  { n=$(okcount "$1"); echo "   $(basename "$1"): $n ok"; [ "$n" -gt 0 ] || { snap abort; die "0 ok in $1"; }; }

[ -f "$BENCH_WORK/usage.jsonl" ] && mv "$BENCH_WORK/usage.jsonl" "$BENCH_WORK/usage-before-$(date +%Y%m%d-%H%M%S).jsonl"
step "fresh restore -> $OUT"
docker rm -f "$BENCH_CONTAINER" >/dev/null 2>&1; docker volume rm "$BENCH_VOLUME" >/dev/null 2>&1
ROLE=REFLECT scripts/up.sh "$(base)" restore || die "restore failed"
snap restore
RESTORE_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "$RESTORE_AT" > "$OUT/restore_at"
B=$(nblocks)

if [[ "${PHASES:-A B}" == *A* ]]; then
  need_file "$BENCH_WORK/queries.json"; need_file "$BENCH_WORK/pages.json"
  for b in $(seq 1 "$B"); do
    for tag in $(order "$b"); do
      m=$(model "$tag"); step "A b$b $tag ($m)"
      ROLE=REFLECT scripts/up.sh "$m" || die "bench failed to start"
      sleep "$SETTLE_SECONDS"
      q=$(ids reflect "$b"); p=$(ids pages "$b")
      [ -n "$q" ] && { python3 reflect/run.py "$tag-b$b" --ids "$q" 2> "$BENCH_WORK/logs/$tag-b$b.log"; check "$BENCH_WORK/runs/reflect/$tag-b$b.json"; }
      [ -n "$p" ] && { python3 reflect/refresh.py "$tag-pg-b$b" "$BENCH_WORK/pages.json" --ids "$p" 2> "$BENCH_WORK/logs/$tag-pg-b$b.log"; check "$BENCH_WORK/runs/reflect/$tag-pg-b$b.json"; }
      snap "A-$tag-b$b"
    done
  done
fi

if [[ "${PHASES:-A B}" == *B* ]]; then
  need_file "$BENCH_WORK/docs.json"
  for b in $(seq 1 "$B"); do
    for tag in $(order "$b"); do
      m=$(model "$tag"); step "B b$b $tag ($m)"
      ROLE=RETAIN EXTRA_ENV=HINDSIGHT_API_ENABLE_DRY_RUN_EXTRACT=true scripts/up.sh "$m" || die "bench failed to start"
      sleep "$SETTLE_SECONDS"
      python3 retain/run.py "$tag-b$b" "$BENCH_WORK/docs.json" --ids "$(ids retain "$b")" 2> "$BENCH_WORK/logs/retain-$tag-b$b.log"
      check "$BENCH_WORK/runs/retain/$tag-b$b.json"
      snap "B-$tag-b$b"
    done
  done
fi

step "export"
cp "$BENCH_WORK/usage.jsonl" "$OUT/usage.jsonl" 2>/dev/null
export_traces "$BENCH_CONTAINER" "$RESTORE_AT" "$OUT/llm_requests.jsonl"
step "done -> $OUT"
