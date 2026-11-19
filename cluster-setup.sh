#!/usr/bin/env bash
# cluster-setup.sh — APO Workshop: one-shot real-cluster setup (Linux / WSL2 / macOS)
# ====================================================================================
# Replaces the copy-paste steps in README.md "Cluster setup (real mode)" and fixes
# the Prometheus step (README applies prometheus-config.yaml directly, which fails
# with "apiVersion not set" and skips the RBAC Prometheus needs for pod discovery).
#
# Safe to re-run: every step is idempotent (reuses cluster, image, venv, resources).
#
# Usage (from the repo root):
#   bash cluster-setup.sh
#
# What it leaves running in the background (survives closing this terminal):
#   - kubectl port-forward  localhost:8080 -> payment-service
#   - kubectl port-forward  localhost:9090 -> Prometheus
#   - lab1/load_generator.py (5 req/s) using .venv
# Logs: lab1/.lab_logs/   PIDs: lab1/.lab_pids
#
# Stop everything and delete the cluster:   bash lab1/teardown.sh
# Pause (keep cluster):                     k3d cluster stop apo-lab   /   k3d cluster start apo-lab
#   (after a start, re-run this script to restore port-forwards + load generator)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_NAME="apo-lab"
IMAGE="payment-service:lab"
VENV_DIR="$REPO_DIR/.venv"
PID_FILE="$REPO_DIR/lab1/.lab_pids"      # shared with lab1/teardown.sh
LOG_DIR="$REPO_DIR/lab1/.lab_logs"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'
info() { echo -e "${CYAN}[setup]${RESET} $*"; }
ok()   { echo -e "${GREEN}[setup] ✓${RESET} $*"; }
warn() { echo -e "${YELLOW}[setup] ⚠${RESET} $*"; }
die()  { echo -e "${RED}[setup] ✗${RESET} $*" >&2; exit 1; }

check_prereqs() {
    info "Checking prerequisites..."
    local cmd
    for cmd in docker k3d kubectl python3 curl; do
        command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' not found on PATH. See README prerequisites."
    done
    docker info >/dev/null 2>&1 \
        || die "Docker daemon not reachable. WSL: start Docker Desktop and enable Settings → Resources → WSL Integration for this distro."
    ok "docker, k3d, kubectl, python3 found; Docker daemon running"
}

ensure_venv() {
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        info "Creating virtual environment at .venv..."
        python3 -m venv "$VENV_DIR"
    fi
    if ! "$VENV_DIR/bin/python" -c "import fastapi, uvicorn, prometheus_client, requests, yaml" 2>/dev/null; then
        info "Installing requirements.txt into .venv..."
        "$VENV_DIR/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"
    fi
    ok "Python venv ready ($("$VENV_DIR/bin/python" --version))"
}

ensure_cluster() {
    if k3d cluster list --no-headers 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER_NAME"; then
        warn "Cluster '$CLUSTER_NAME' exists — reusing it"
        k3d cluster start "$CLUSTER_NAME" >/dev/null 2>&1 || true   # no-op if already running
    else
        info "Creating k3d cluster '$CLUSTER_NAME' (1 server + 2 agents)..."
        k3d cluster create "$CLUSTER_NAME" --agents 2 --wait
    fi
    kubectl config use-context "k3d-$CLUSTER_NAME" >/dev/null
    kubectl wait node --all --for=condition=Ready --timeout=120s >/dev/null
    ok "Cluster ready ($(kubectl get nodes --no-headers | wc -l | tr -d ' ') nodes)"
}

deploy_payment_service() {
    info "Building $IMAGE (cached layers make re-runs fast)..."
    docker build --quiet -t "$IMAGE" "$REPO_DIR/lab1/payment-service" >/dev/null
    info "Importing $IMAGE into the cluster..."
    k3d image import "$IMAGE" -c "$CLUSTER_NAME" >/dev/null
    kubectl apply -f "$REPO_DIR/lab1/payment-service/deployment.yaml" >/dev/null
    kubectl rollout status deployment/payment-service --timeout=180s >/dev/null \
        || die "payment-service rollout timed out. Run: kubectl get pods; kubectl describe pod -l app=payment-service"
    ok "payment-service running"
}

deploy_prometheus() {
    local config="$REPO_DIR/lab1/prometheus-config.yaml"
    info "Deploying Prometheus..."
    # --dry-run | apply makes these idempotent (no "AlreadyExists" errors on re-run)
    kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    # RBAC: lets Prometheus list/watch pods for kubernetes_sd_configs (missing from README)
    kubectl apply -f "$REPO_DIR/lab1/k8s/prometheus-rbac.yaml" >/dev/null
    # Whole file as prometheus.yml — Prometheus reads only the first YAML document (scrape config)
    kubectl create configmap prometheus-config --from-file=prometheus.yml="$config" \
        -n monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    # Only the Kubernetes manifests (Deployment + Service) — everything from the first apiVersion: line
    sed -n '/^apiVersion:/,$p' "$config" | kubectl apply -f - >/dev/null
    kubectl rollout status deployment/prometheus -n monitoring --timeout=180s >/dev/null \
        || die "Prometheus rollout timed out. Run: kubectl get pods -n monitoring"
    ok "Prometheus running"
}

stop_background() {
    if [ -f "$PID_FILE" ]; then
        local pid
        while IFS= read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PID_FILE"
        rm -f "$PID_FILE"
    fi
    # Also clear forwards/generators started by hand in other terminals (they hold the ports)
    pkill -f "^kubectl port-forward .*(8080|9090)" 2>/dev/null || true
    pkill -f "^[^ ]*python[0-9.]* [^ ]*load_generator\.py" 2>/dev/null || true
    sleep 1
}

wait_for_url() {
    local url="$1" label="$2" deadline=$((SECONDS + 30))
    until curl -sf -m 2 "$url" >/dev/null 2>&1; do
        [ "$SECONDS" -gt "$deadline" ] && die "$label not reachable at $url (see $LOG_DIR/)"
        sleep 1
    done
}

start_background() {
    info "Starting port-forwards and load generator in the background..."
    stop_background
    mkdir -p "$LOG_DIR"
    nohup kubectl port-forward svc/payment-service 8080:80 >"$LOG_DIR/pf-payment.log" 2>&1 &
    echo $! >>"$PID_FILE"
    nohup kubectl port-forward -n monitoring svc/prometheus 9090:9090 >"$LOG_DIR/pf-prometheus.log" 2>&1 &
    echo $! >>"$PID_FILE"
    wait_for_url "http://localhost:8080/health" "payment-service"
    wait_for_url "http://localhost:9090/-/ready" "Prometheus"
    nohup "$VENV_DIR/bin/python" "$REPO_DIR/lab1/load_generator.py" --quiet >"$LOG_DIR/load-generator.log" 2>&1 &
    echo $! >>"$PID_FILE"
    ok "Port-forwards on 8080 / 9090; load generator running (PIDs in lab1/.lab_pids)"
}

count_up_targets() {
    curl -s -m 5 "http://localhost:9090/api/v1/query?query=up%7Bjob%3D%22payment-service%22%7D" \
        | "$VENV_DIR/bin/python" -c \
            "import sys, json; print(sum(r['value'][1] == '1' for r in json.load(sys.stdin)['data']['result']))" \
        2>/dev/null || echo 0
}

verify() {
    info "Running README 'Verify cluster is ready' checks..."
    local nodes pay prom up deadline=$((SECONDS + 90))
    nodes=$(kubectl get nodes --no-headers | grep -c " Ready " || true)
    pay=$(kubectl get pods -l app=payment-service --no-headers | grep -c Running || true)
    prom=$(kubectl get pods -n monitoring -l app=prometheus --no-headers | grep -c Running || true)
    if [[ "$nodes" -ne 3 ]]; then die "1. Expected 3 Ready nodes, got $nodes"; fi
    ok "1. Nodes Ready: $nodes/3"
    if [[ "$pay" -lt 1 ]]; then die "2. No payment-service pods Running"; fi
    ok "2. payment-service pods Running: $pay"
    if [[ "$prom" -lt 1 ]]; then die "3. Prometheus pod not Running"; fi
    ok "3. Prometheus pod Running"
    ok "4. /health → $(curl -s -m 5 http://localhost:8080/health)"
    ok "5. /-/ready → $(curl -s -m 5 http://localhost:9090/-/ready)"
    info "6. Waiting for Prometheus to scrape payment-service (first scrape can take ~15–30 s)..."
    until up=$(count_up_targets) && [ "$up" -ge 1 ]; do
        [ "$SECONDS" -gt "$deadline" ] && die "6. No payment-service targets UP. Check http://localhost:9090/targets"
        sleep 5
    done
    ok "6. Prometheus scraping payment-service: $up target(s) UP"
}

main() {
    cd "$REPO_DIR"
    check_prereqs
    ensure_venv
    ensure_cluster
    deploy_payment_service
    deploy_prometheus
    start_background
    verify
    echo
    echo -e "${GREEN}════════ APO lab cluster ready ════════${RESET}"
    echo "  payment-service    http://localhost:8080"
    echo "  Prometheus         http://localhost:9090   (targets: /targets)"
    echo "  Activate venv:     source .venv/bin/activate"
    echo "  Tear down:         bash lab1/teardown.sh"
}

main "$@"
