#!/usr/bin/env bash
# setup.sh — APO Workshop Lab 1
# ==============================
# One command to set up the entire lab environment.
# Run once at the start of Lab 1; everything else is Python coding.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
#
# What it does (you do not need to understand or troubleshoot any of this):
#   1. Creates a k3d cluster (k8s-in-Docker)
#   2. Builds and loads the payment-service image
#   3. Deploys payment-service (2 replicas) + Prometheus
#   4. Applies RBAC so Prometheus can discover pods
#   5. Starts background port-forwards (8080 → payment-service, 9090 → Prometheus)
#   6. Starts the load generator in the background
#   7. Verifies everything is green
#
# Clean up at the end of the lab:
#   ./teardown.sh

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[setup]${RESET} $*"; }
success() { echo -e "${GREEN}[setup] ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}[setup] ⚠${RESET} $*"; }
die()     { echo -e "${RED}[setup] ✗ FATAL:${RESET} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CLUSTER_NAME="apo-lab"
PID_FILE="$SCRIPT_DIR/.lab_pids"

# ── Prerequisite check ────────────────────────────────────────────────────────
info "Checking prerequisites..."
for cmd in docker k3d kubectl python3; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' not found. See README prerequisites."
done
docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker Desktop / Colima first."
success "Prerequisites OK"

# ── macOS / Colima: raise inotify limits ─────────────────────────────────────
if command -v colima >/dev/null 2>&1 && colima status 2>/dev/null | grep -q "Running"; then
    info "Colima detected — raising inotify limits..."
    colima ssh -- 'sudo sysctl -w fs.inotify.max_user_watches=1048576 fs.inotify.max_user_instances=512' \
        >/dev/null 2>&1 || warn "Could not raise inotify limits — continuing anyway"
fi

# ── Cluster ───────────────────────────────────────────────────────────────────
if k3d cluster list 2>/dev/null | grep -q "^$CLUSTER_NAME"; then
    warn "Cluster '$CLUSTER_NAME' already exists — reusing it"
else
    info "Creating k3d cluster '$CLUSTER_NAME' (1 server + 2 agents)..."
    k3d cluster create "$CLUSTER_NAME" --agents 2 --wait
    success "Cluster created"
fi
kubectl config use-context "k3d-$CLUSTER_NAME" >/dev/null
kubectl wait node --all --for=condition=Ready --timeout=90s >/dev/null
success "Cluster nodes ready"

# ── Build + load payment-service image ───────────────────────────────────────
info "Building payment-service image..."
if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q '^payment-service:lab$'; then
    warn "Image 'payment-service:lab' already built — skipping build (delete with 'docker rmi payment-service:lab' to rebuild)"
else
    docker build -t payment-service:lab payment-service/ \
        || die "Docker build failed. Check Docker Hub connectivity or use a cached image."
    success "Image built"
fi

info "Loading image into k3d cluster..."
k3d image import payment-service:lab -c "$CLUSTER_NAME"
success "Image loaded"

# ── Deploy payment-service ────────────────────────────────────────────────────
info "Deploying payment-service..."
kubectl apply -f payment-service/deployment.yaml >/dev/null
kubectl rollout status deployment/payment-service --timeout=120s \
    || die "payment-service rollout timed out. Run: kubectl get pods"
success "payment-service deployed ($(kubectl get pods -l app=payment-service --no-headers | grep Running | wc -l | tr -d ' ') pods running)"

# ── Pull + load Prometheus image ─────────────────────────────────────────────
PROM_IMAGE="prom/prometheus:v2.47.0"
info "Checking Prometheus image..."
# Check k3d node containerd first (image may already be there from a prior run)
if docker exec "k3d-${CLUSTER_NAME}-server-0" crictl images 2>/dev/null | grep -q "prom/prometheus.*v2.47.0"; then
    info "Prometheus image already in k3d — skipping import"
elif docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^prom/prometheus:v2.47.0$"; then
    info "Prometheus image in Docker Desktop — importing into k3d..."
    k3d image import "$PROM_IMAGE" -c "$CLUSTER_NAME" >/dev/null \
        || die "Failed to import Prometheus image into k3d"
    success "Prometheus image loaded"
else
    info "Pulling $PROM_IMAGE from Docker Hub..."
    docker pull "$PROM_IMAGE" \
        || die "Failed to pull $PROM_IMAGE — Docker Hub unreachable and no local cache.\n  Fix: connect to a network with Docker Hub access and rerun setup.sh."
    k3d image import "$PROM_IMAGE" -c "$CLUSTER_NAME" >/dev/null \
        || die "Failed to import Prometheus image into k3d"
    success "Prometheus image loaded"
fi

# ── Deploy Prometheus ─────────────────────────────────────────────────────────
info "Deploying Prometheus..."
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Apply RBAC first so Prometheus can discover pods immediately on start
kubectl apply -f k8s/prometheus-rbac.yaml >/dev/null
success "RBAC applied (Prometheus can discover pods)"

# ConfigMap — idempotent
kubectl create configmap prometheus-config \
    --from-file=prometheus.yml=prometheus-config.yaml \
    -n monitoring \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Prometheus Deployment + Service (second document in prometheus-config.yaml)
sed -n '/^apiVersion:/,$p' prometheus-config.yaml | kubectl apply -f - >/dev/null
kubectl rollout status deployment/prometheus -n monitoring --timeout=120s \
    || die "Prometheus rollout timed out. Run: kubectl get pods -n monitoring"
success "Prometheus deployed"

# ── Kill any stale port-forwards ─────────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    info "Stopping stale port-forwards from a previous run..."
    while IFS= read -r pid; do
        kill "$pid" 2>/dev/null || true
    done < "$PID_FILE"
    rm -f "$PID_FILE"
fi
# Belt-and-suspenders: kill anything holding 8080 or 9090
pkill -f "kubectl port-forward.*8080" 2>/dev/null || true
pkill -f "kubectl port-forward.*9090" 2>/dev/null || true
sleep 1

# ── Port-forwards ─────────────────────────────────────────────────────────────
info "Starting port-forwards..."

# Forward to pod by label so it survives a pod replacement
PAY_POD=$(kubectl get pod -l app=payment-service -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward "pod/$PAY_POD" 8080:8080 >/dev/null 2>&1 &
PAY_PF_PID=$!

kubectl port-forward -n monitoring svc/prometheus 9090:9090 >/dev/null 2>&1 &
PROM_PF_PID=$!

echo "$PAY_PF_PID"  > "$PID_FILE"
echo "$PROM_PF_PID" >> "$PID_FILE"

# Wait for ports to open
for port in 8080 9090; do
    deadline=$((SECONDS + 20))
    while ! curl -s "http://localhost:$port" >/dev/null 2>&1; do
        [ $SECONDS -gt $deadline ] && die "Port $port did not open in 20 s"
        sleep 1
    done
done
success "Port-forwards ready (8080 → payment-service, 9090 → Prometheus)"

# ── Load generator ────────────────────────────────────────────────────────────
info "Starting load generator (5 req/s background)..."
pkill -f "python3.*load_generator.py" 2>/dev/null || true
sleep 1
python3 load_generator.py --quiet &
LG_PID=$!
echo "$LG_PID" >> "$PID_FILE"
success "Load generator started (PID $LG_PID)"

# ── Wait for Prometheus to scrape payment-service ────────────────────────────
info "Waiting for Prometheus to scrape payment-service (up to 60 s)..."
deadline=$((SECONDS + 60))
while true; do
    result=$(curl -s "http://localhost:9090/api/v1/query?query=up%7Bjob%3D~%22payment.*%22%7D" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['result'][0]['value'][1])" 2>/dev/null || echo "")
    [ "$result" = "1" ] && break
    [ $SECONDS -gt $deadline ] && {
        warn "Prometheus has not yet shown payment-service as UP (this is normal if the first scrape hasn't run)"
        warn "Check http://localhost:9090/targets in ~30 s"
        break
    }
    sleep 5
done
[ "$result" = "1" ] && success "Prometheus: payment-service is UP"

# ── Verify fault injector is reachable ────────────────────────────────────────
if curl -sf -X POST http://localhost:8080/toggle_fault >/dev/null 2>&1; then
    # Toggle it back off immediately
    curl -sf -X POST http://localhost:8080/toggle_fault >/dev/null 2>&1 || true
    success "Fault injector reachable"
else
    warn "Fault injector POST returned an error — payment-service may still be warming up"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  Lab 1 environment ready                           ${RESET}"
echo -e "${GREEN}════════════════════════════════════════════════════${RESET}"
echo ""
echo "  payment-service  →  http://localhost:8080"
echo "  Prometheus       →  http://localhost:9090"
echo "  Prometheus targets →  http://localhost:9090/targets"
echo ""
echo "  Load generator running in background ($(cat "$PID_FILE" | tail -1 | xargs ps -p 2>/dev/null | tail -1 | awk '{print $1}' || echo 'check .lab_pids'))"
echo ""
echo -e "  ${CYAN}Next step: open gate_starter.py and implement the four gates${RESET}"
echo ""
echo "  To stop everything and clean up:  ./teardown.sh"
echo ""
