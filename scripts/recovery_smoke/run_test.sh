#!/usr/bin/env bash
# Orchestrated local proof of the requeue mechanism. Solo pool => the task runs
# in the worker's main process, so `kill -9` is a faithful pod-SIGKILL.
set -u
cd "$(dirname "$0")/../.."
export PYTHONPATH="lex:$PWD"
export DJANGO_SETTINGS_MODULE=lex_app.settings
export CELERY_ACTIVE=TRUE
export LEX_TASK_RECOVERY_ENABLED=true
export LEX_TASK_HEARTBEAT_INTERVAL=2
export LEX_TASK_HB_TTL_MULTIPLIER=2          # hb TTL = 4s
export LEX_TASK_SUPERVISOR_SCAN_INTERVAL=3
export LEX_TASK_MAX_RETRIES=2
export CELERY_VALIDATE_CONFIG=False
PY=/opt/anaconda/bin/python
LOG=/tmp/recovery_smoke
rm -rf "$LOG"; mkdir -p "$LOG"
GREP='grep -v -E "NumExpr|Registering [0-9]|get_model_structure|get_widget_structure|get_model_styling"'

echo "### clean any stale recovery keys"
redis-cli -n 1 --scan --pattern '*lex:recover:*' | xargs -r redis-cli -n 1 del >/dev/null

start_worker () {  # $1 = label
  $PY -m celery -A scripts.recovery_smoke.harness worker -P solo -Q celery \
      --loglevel=info >"$LOG/worker_$1.log" 2>&1 &
  echo $!
}

echo "### start supervisor"
$PY -m lex run_recovery_supervisor --interval 3 >"$LOG/supervisor.log" 2>&1 &
SUP=$!

echo "### start worker A"
WA=$(start_worker A)
sleep 6

echo "### enqueue a 40s task"
TID=$($PY scripts/recovery_smoke/harness.py enqueue 40 2>/dev/null | sed -n 's/ENQUEUED task_id=\([^ ]*\).*/\1/p')
echo "TASK_ID=$TID"
sleep 6

echo "### state BEFORE kill (expect hb_ttl>0, task running on worker A):"
redis-cli -n 1 ttl "$(redis-cli -n 1 --scan --pattern "*hb:$TID" | head -1)" 2>/dev/null
grep -h "sleeping" "$LOG/worker_A.log" | tail -1

echo "### KILL -9 worker A (simulate pod SIGKILL) at $(date +%T)"
kill -9 "$WA" 2>/dev/null
sleep 12   # hb expires ~4s after last beat, supervisor scans every 3s

echo "### supervisor log after death (expect 'requeued dead task ... attempt 1/2'):"
eval grep -hE "'requeued|gave up|cancelled|sweep'" "$LOG/supervisor.log" | tail -5

echo "### start worker B (simulates KEDA bringing a fresh worker up) at $(date +%T)"
WB=$(start_worker B)
sleep 8

echo "### worker B log (expect SAME task_id picked up again):"
eval grep -h "'sleeping\|received\|Received'" "$LOG/worker_B.log" | tail -3
echo "### retries count in payload now (expect 1):"
$PY scripts/recovery_smoke/harness.py watch >"$LOG/watch.log" 2>/dev/null &
WPID=$!; sleep 2; kill "$WPID" 2>/dev/null
tail -1 "$LOG/watch.log"

echo "### cleanup"
kill -9 "$WB" "$SUP" 2>/dev/null
pkill -9 -f "recovery_smoke.harness" 2>/dev/null
pkill -9 -f "run_recovery_supervisor" 2>/dev/null
echo "### DONE. Full logs in $LOG/"
