#!/usr/bin/env bash
# Pustí DOM testy proti izolované kopii appky.
#
# Zvedne druhou instanci server.py na portu 8002 nad KOPIÍ databáze, počká na
# ni, pustí testy a zase po sobě uklidí. Ostrá DB ani instance na 8001 se
# nedotknou — testy posouvají kolečka a ukládají geometrii, takže na ostrých
# datech nemají co dělat.
#
#   ./run.sh              # vše
#   ./run.sh test_view.py # jeden test
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$(dirname "$HERE")"
WS="$(dirname "$APP")"

PORT="${PORT:-8002}"
LIVE_DB="${BOLT_DB:-$WS/data/climbing_paths.sqlite}"
IMG_DIR="${BOLT_IMG_DIR:-$WS/data/photos}"
TMP_DB="$(mktemp /tmp/bolt-domtest-XXXXXX.sqlite)"
SRV_LOG="/tmp/bolt-domtest-server.log"

[ -f "$LIVE_DB" ] || { echo "DB nenalezena: $LIVE_DB" >&2; exit 1; }

echo "== kopie DB   : $LIVE_DB -> $TMP_DB"
python3 - "$LIVE_DB" "$TMP_DB" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
PY

echo "== server     : http://127.0.0.1:$PORT (log $SRV_LOG)"
BOLT_PORT="$PORT" BOLT_DB="$TMP_DB" BOLT_IMG_DIR="$IMG_DIR" \
  python3 "$APP/server.py" > "$SRV_LOG" 2>&1 &
SRV_PID=$!

cleanup() {
  kill "$SRV_PID" 2>/dev/null || true
  wait "$SRV_PID" 2>/dev/null || true
  rm -f "$TMP_DB"
}
trap cleanup EXIT

for _ in $(seq 40); do
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/crops"; then break; fi
  sleep 0.5
done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/crops" || {
  echo "server nenaskočil, log:" >&2; tail -20 "$SRV_LOG" >&2; exit 1; }

export BASE_URL="http://127.0.0.1:$PORT"
export PYTHONPATH="$HERE:${PYTHONPATH:-}"

if [ $# -gt 0 ]; then
  TESTS=("$HERE/$1")
else
  TESTS=("$HERE"/test_*.py)
fi

rc=0
for t in "${TESTS[@]}"; do
  echo
  echo "== $(basename "$t")"
  python3 "$t" || rc=1
done

echo
[ "$rc" -eq 0 ] && echo "== VŠE OK" || echo "== NĚCO SPADLO"
exit "$rc"
