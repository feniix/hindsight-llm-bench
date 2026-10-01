#!/bin/bash
# run-followup.sh: does the retain model change what consolidation and reflect produce?
# Reads $BENCH_WORK/followup/plan.json:
#   {"base_model": "...", "candidate_model": "...", "corpora": {"KEY": "SOURCE_BANK", ...}}
# plus corpus.json and config-*.json (prepare/export.py corpus) and gold.json (followup/gold.py).
# Each corpus is retained twice, into fu-KEY-base and fu-KEY-cand, in ABBA order of retain
# model. Then, with the worker on and every step on the base model:
#   consolidate each bank one at a time, rebuild its knowledge pages, answer the gold questions.
# Finally export proxy usage, llm_requests, /metrics and the server log.
set -u
. "$(dirname "$0")/common.sh"
F=$BENCH_WORK/followup; export FU_DIR=$F
need_file "$F/plan.json" "see examples/followup-plan.example.json"
need_file "$F/corpus.json" "python3 prepare/export.py corpus ..."
need_file "$F/gold.json" "python3 followup/gold.py ..."
need_proxy
mkdir -p "$F/out"
plan() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$F/plan.json" "$1"; }
BASE=$(plan base_model); CAND=$(plan candidate_model)
KEYS=$(python3 -c "import json,sys;print(' '.join(json.load(open(sys.argv[1]))['corpora']))" "$F/plan.json")
src() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['corpora'][sys.argv[2]])" "$F/plan.json" "$1"; }

[ -f "$BENCH_WORK/usage.jsonl" ] && mv "$BENCH_WORK/usage.jsonl" "$BENCH_WORK/usage-before-fu-$(date +%Y%m%d-%H%M%S).jsonl"
step "fresh volume"
docker rm -f "$FU_CONTAINER" >/dev/null 2>&1; docker volume rm "$FU_VOLUME" >/dev/null 2>&1
scripts/fu-up.sh "$BASE" false || die "start"
date -u +%Y-%m-%dT%H:%M:%SZ > "$F/out/started_at"
for k in $KEYS; do
  for v in base cand; do python3 followup/fu.py setup "$(src "$k")" "fu-$k-$v" || die "setup fu-$k-$v"; done
done

# ABBA: alternate which retain model goes first per corpus, restarting only when the model changes.
current=$BASE; i=0; ORDER=""
for k in $KEYS; do
  if [ $((i % 2)) -eq 0 ]; then ORDER="$ORDER $k:base $k:cand"; else ORDER="$ORDER $k:cand $k:base"; fi
  i=$((i + 1))
done
for item in $ORDER; do
  k=${item%%:*}; v=${item##*:}; m=$BASE; [ "$v" = cand ] && m=$CAND
  [ "$m" = "$current" ] || { scripts/fu-up.sh "$m" false || die "start"; current=$m; }
  step "retain $k with $m"
  RETAIN_MODEL=$m python3 followup/fu.py retain "$(src "$k")" "fu-$k-$v" 2> "$F/out/retain-$k-$v.log" || die "retain fu-$k-$v"
done
docker logs "$FU_CONTAINER" > "$F/out/retain.server.log" 2>&1

step "worker on"
scripts/fu-up.sh "$BASE" true || die "start"
for item in $ORDER; do
  b="fu-${item%%:*}-${item##*:}"; step "consolidate $b"
  python3 followup/fu.py consolidate "$b" 2> "$F/out/consolidate-$b.log" || die "consolidate $b"
  curl -s "localhost:$BENCH_PORT/metrics" > "$F/out/metrics-after-consolidate-$b.prom"
done
for item in $ORDER; do
  k=${item%%:*}; b="fu-$k-${item##*:}"; step "pages $b"
  python3 followup/fu.py pages "$(src "$k")" "$b" 2> "$F/out/pages-$b.log" || die "pages $b"
done
for k in $KEYS; do
  step "qa $k"
  python3 followup/fu.py qa "$(src "$k")" "fu-$k-base" "fu-$k-cand" 2> "$F/out/qa-$k.log" || die "qa $k"
done

step "export"
cp "$BENCH_WORK/usage.jsonl" "$F/out/usage.jsonl" 2>/dev/null
curl -s "localhost:$BENCH_PORT/metrics" > "$F/out/metrics-final.prom"
docker logs "$FU_CONTAINER" > "$F/out/final.server.log" 2>&1
export_traces "$FU_CONTAINER" "$(cat "$F/out/started_at")" "$F/out/llm_requests.jsonl"
step "done -> $F/out"
