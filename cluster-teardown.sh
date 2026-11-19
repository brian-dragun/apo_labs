#!/usr/bin/env bash
# cluster-teardown.sh — APO Workshop: undo everything cluster-setup.sh started
# =============================================================================
# Usage (from the repo root):
#   bash cluster-teardown.sh          # stop port-forwards + load generator, DELETE the cluster
#   bash cluster-teardown.sh --keep   # stop port-forwards + load generator, PAUSE the cluster
#                                     # (resume later with: bash cluster-setup.sh)
#
# Never touches: your code, .venv, lab audit files, or the payment-service:lab Docker image
# (remove that manually with: docker rmi payment-service:lab).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_NAME="apo-lab"
PID_FILE="$REPO_DIR/lab1/.lab_pids"      # written by cluster-setup.sh and lab1/setup.sh
LOG_DIR="$REPO_DIR/lab1/.lab_logs"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info() { echo -e "${CYAN}[teardown]${RESET} $*"; }
ok()   { echo -e "${GREEN}[teardown] ✓${RESET} $*"; }
warn() { echo -e "${YELLOW}[teardown] ⚠${RESET} $*"; }
die()  { echo -e "${RED}[teardown] ✗${RESET} $*" >&2; exit 1; }

usage() {
    sed -n '4,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

stop_background() {
    info "Stopping port-forwards and load generator..."
    if [[ -f "$PID_FILE" ]]; then
        local pid
        while IFS= read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PID_FILE"
        rm -f "$PID_FILE"
    fi
    # Catch any started by hand in other terminals
    pkill -f "^kubectl port-forward .*(8080|9090)" 2>/dev/null || true
    pkill -f "^[^ ]*python[0-9.]* [^ ]*load_generator\.py" 2>/dev/null || true
    rm -rf "$LOG_DIR"
    ok "Background processes stopped; ports 8080 / 9090 released"
}

cluster_exists() {
    k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER_NAME"
}

main() {
    local mode="delete"
    case "${1:-}" in
        "")          ;;
        --keep)      mode="stop" ;;
        -h|--help)   usage; exit 0 ;;
        *)           usage; die "Unknown option: $1" ;;
    esac

    command -v k3d >/dev/null 2>&1 || die "'k3d' not found on PATH"
    stop_background

    if ! cluster_exists; then
        warn "Cluster '$CLUSTER_NAME' not found — nothing else to do"
        exit 0
    fi

    if [[ "$mode" == "stop" ]]; then
        info "Stopping cluster '$CLUSTER_NAME' (state is kept)..."
        k3d cluster stop "$CLUSTER_NAME" >/dev/null
        ok "Cluster paused. Resume with: bash cluster-setup.sh"
    else
        info "Deleting cluster '$CLUSTER_NAME'..."
        k3d cluster delete "$CLUSTER_NAME" >/dev/null
        ok "Cluster deleted (containers, network, volumes, kubeconfig entry)"
    fi
}

main "$@"
