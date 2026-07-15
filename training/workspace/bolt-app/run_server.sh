#!/usr/bin/env bash
# Dohledová smyčka: drží server.py naživu. Když spadne, restartuje ho.
cd "$(dirname "$0")" || exit 1
while true; do
  echo "[$(date -Is)] start server.py" >> server.log
  python3 server.py >> server.log 2>&1
  echo "[$(date -Is)] server.py skončil (exit $?), restart za 2 s" >> server.log
  sleep 2
done
