#!/usr/bin/env bash
# setup.sh — APO Workshop Lab 3
# ==============================
# Ensures the lab environment is ready. Reuses the Lab 1 cluster.
# Run once at the start of Lab 3.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# Clean up at the end of the lab:
#   ./teardown.sh
#   ../lab1/teardown.sh  (deletes the cluster — only after all labs are done)

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[setup]${RESET} $*"; }
success() { echo -e "${GREEN}[setup] ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}[setup] ⚠${RESET} $*"; }
die()     { echo -e "${RED}[setup] ✗ FATAL:${RESET} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB1_DIR="$SCRIPT_DIR/../lab1"
PID_FILE="$SCRIPT_DIR/.lab_pids"

# ── Ensure Lab 1 cluster exists ───────────────────────────────────────────────
info "Checking Lab 1 cluster..."
if ! k3d cluster list 2>/dev/null | grep -q "^apo-lab"; then
    warn "Lab 1 cluster not found — running lab1/setup.sh first"
    bash "$LAB1_DIR/setup.sh" || die "Lab 1 setup failed — fix it before continuing to Lab 3"
    success "Lab 1 cluster ready"
else
    kubectl config use-context k3d-apo-lab >/dev/null
    kubectl wait node --all --for=condition=Ready --timeout=30s >/dev/null
    success "Lab 1 cluster already running"
fi

# ── Kill stale port-forwards ──────────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    info "Stopping stale port-forwards from a previous run..."
    while IFS= read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PID_FILE"
    rm -f "$PID_FILE"
fi
pkill -f "kubectl port-forward.*8080" 2>/dev/null || true
pkill -f "kubectl port-forward.*9090" 2>/dev/null || true
sleep 1

# ── Port-forwards ─────────────────────────────────────────────────────────────
info "Starting port-forwards..."

PAY_POD=$(kubectl get pod -l app=payment-service -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward "pod/$PAY_POD" 8080:8080 >/dev/null 2>&1 &
echo "$!" > "$PID_FILE"
kubectl port-forward -n monitoring svc/prometheus 9090:9090 >/dev/null 2>&1 &
echo "$!" >> "$PID_FILE"

for port in 8080 9090; do
    deadline=$((SECONDS + 20))
    while ! curl -s "http://localhost:$port" >/dev/null 2>&1; do
        [ $SECONDS -gt $deadline ] && die "Port $port did not open in 20 s"
        sleep 1
    done
done
success "Port-forwards ready (8080 → payment-service, 9090 → Prometheus)"

# ── Load generator ────────────────────────────────────────────────────────────
if ! pgrep -f "python3.*load_generator.py" >/dev/null 2>&1; then
    info "Starting load generator..."
    python3 "$LAB1_DIR/load_generator.py" --quiet &
    echo "$!" >> "$PID_FILE"
    success "Load generator started"
else
    success "Load generator already running"
fi

# ── Verify asyncio is available (Python 3.4+) ────────────────────────────────
python3 -c "import asyncio" 2>/dev/null || die "asyncio not available — Python 3.4+ required"
success "asyncio available"

# ── Verify previous labs ──────────────────────────────────────────────────────
info "Verifying Lab 1 gates (simulate mode)..."
OUT=$(python3 "$LAB1_DIR/gate_starter.py" --simulate 2>&1 || true)
if echo "$OUT" | grep -q "4/4 tests passed"; then
    success "Lab 1 gates: 4/4 passed"
else
    warn "Lab 1 gates did not all pass — open lab1/gate_starter.py and check your implementation"
    warn "Lab 3 will still work in simulate mode"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  Lab 3 environment ready                           ${RESET}"
echo -e "${GREEN}════════════════════════════════════════════════════${RESET}"
echo ""
echo "  payment-service  →  http://localhost:8080"
echo "  Prometheus       →  http://localhost:9090"
echo ""
echo -e "  ${CYAN}Next step: open orchestrator_starter.py and implement MetricsAgent.diagnose()${RESET}"
echo ""
echo "  To stop port-forwards + load generator:  ./teardown.sh"
echo "  To delete the cluster when done:         ../lab1/teardown.sh"
echo ""
