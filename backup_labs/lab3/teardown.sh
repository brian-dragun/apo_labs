#!/usr/bin/env bash
# teardown.sh — APO Workshop Lab 3

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.lab_pids"

echo "[teardown] Stopping background processes..."
if [ -f "$PID_FILE" ]; then
    while IFS= read -r pid; do
        kill "$pid" 2>/dev/null && echo "[teardown] Killed PID $pid" || true
    done < "$PID_FILE"
    rm -f "$PID_FILE"
fi
pkill -f "kubectl port-forward" 2>/dev/null || true
pkill -f "python3.*load_generator.py" 2>/dev/null || true
echo "[teardown] Done. Run ../lab1/teardown.sh to delete the cluster."
