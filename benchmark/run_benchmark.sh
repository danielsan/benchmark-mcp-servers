#!/usr/bin/env bash
set -euo pipefail
# set -xe

# ─── Configuration ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_DIR="$SCRIPT_DIR/results/$TIMESTAMP"
NO_DOCKER=false
DURATION_MINUTES=5

while [ $# -gt 0 ]; do
    case "$1" in
        --no-docker)
            NO_DOCKER=true
            ;;
        --duration=*)
            DURATION_MINUTES="${1#*=}"
            ;;
        --duration)
            if [ $# -lt 2 ]; then
                echo "[ERR] Missing value for --duration" >&2
                exit 1
            fi
            DURATION_MINUTES="$2"
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--no-docker] [--duration=N]"
            echo "  --no-docker   Run each server locally (one at a time) instead of Docker containers"
            echo "  --duration=N  Sustained benchmark duration in minutes (default: 5)"
            exit 0
            ;;
        *)
            echo "[ERR] Unknown option: $1" >&2
            echo "Usage: $0 [--no-docker] [--duration=N]" >&2
            exit 1
            ;;
    esac
    shift
done

if ! [[ "$DURATION_MINUTES" =~ ^[1-9][0-9]*$ ]]; then
    echo "[ERR] --duration must be a positive integer number of minutes" >&2
    exit 1
fi
K6_DURATION="${DURATION_MINUTES}m"

# Servers to benchmark (name:container:port)
declare -A SERVERS=(
    [python]="mcp-python-server:8082"
    [go]="mcp-go-server:8081"
    [nodejs]="mcp-nodejs-server:8083"
    [java]="mcp-java-server:8080"
    [rust]="mcp-rust-server:8084"
)
ALL_SERVICES="rust-server python-server go-server nodejs-server java-server"
declare -A LOCAL_SERVER_DIRS=(
    [python]="$PROJECT_DIR/python-server"
    [go]="$PROJECT_DIR/go-server"
    [nodejs]="$PROJECT_DIR/nodejs-server"
    [java]="$PROJECT_DIR/java-server"
    [rust]="$PROJECT_DIR/rust-server"
)
declare -A LOCAL_SERVER_CMDS=(
    [python]="python3 -m uvicorn main:app --host 0.0.0.0 --port 8082"
    [go]="go run main.go"
    [nodejs]="node index.js"
    [java]="gradle bootRun --no-daemon"
    [rust]="PORT=8084 cargo run --release --quiet"
)
LOCAL_SERVER_PID=""

if [ "$NO_DOCKER" = false ]; then
    if docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD=(docker compose)
    elif docker-compose --version >/dev/null 2>&1; then
        COMPOSE_CMD=(docker-compose)
    else
        echo "[ERR] Docker Compose not found (docker compose or docker-compose)." >&2
        exit 1
    fi
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERR]${NC}  $*"; }

# ─── Functions ────────────────────────────────────────────────────────

wait_for_health() {
    local port=$1
    local name=$2
    local max_wait=60
    local elapsed=0

    info "Waiting for $name to be ready (port $port)..."
    while true; do
        # Try /health (Go, Node.js)
        if curl -sf -m 2 "http://localhost:$port/health" > /dev/null 2>&1; then
            break
        fi
        # Try /actuator/health (Java)
        if curl -sf -m 2 "http://localhost:$port/actuator/health" > /dev/null 2>&1; then
            break
        fi
        # Try MCP endpoint (Python — no health endpoint, but MCP responds)
        if curl -sf -m 2 -X POST "http://localhost:$port/mcp" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"health","version":"1.0"}}}' \
            > /dev/null 2>&1; then
            break
        fi
        sleep 1
        elapsed=$((elapsed + 1))
        if [ $elapsed -ge $max_wait ]; then
            error "$name failed to start after ${max_wait}s"
            return 1
        fi
    done
    ok "$name is ready (${elapsed}s)"
}

warmup() {
    local url=$1
    local name=$2
    info "Warming up $name (10 requests)..."

    for i in $(seq 1 10); do
        curl -sf -X POST "$url" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"warmup","version":"1.0"}}}' \
            > /dev/null 2>&1 || true
    done
    ok "Warmup complete"
}

stop_local_server() {
    if [ -n "$LOCAL_SERVER_PID" ] && kill -0 "$LOCAL_SERVER_PID" >/dev/null 2>&1; then
        info "Stopping local MCP server process (pid=$LOCAL_SERVER_PID)..."
        kill "$LOCAL_SERVER_PID" 2>/dev/null || true
        wait "$LOCAL_SERVER_PID" 2>/dev/null || true
    fi
    LOCAL_SERVER_PID=""
}

stop_all_servers() {
    if [ "$NO_DOCKER" = true ]; then
        stop_local_server
        ok "All local servers stopped"
        return
    fi

    info "Stopping all MCP server containers..."
    cd "$PROJECT_DIR"
    "${COMPOSE_CMD[@]}" stop $ALL_SERVICES 2>/dev/null || true
    sleep 2
    ok "All servers stopped"
}

start_server() {
    local service=$1
    info "Starting $service..."
    cd "$PROJECT_DIR"
    "${COMPOSE_CMD[@]}" up -d "$service" 2>/dev/null
}

start_local_server() {
    local name=$1
    local server_results=$2
    local dir="${LOCAL_SERVER_DIRS[$name]}"
    local cmd="${LOCAL_SERVER_CMDS[$name]}"
    local log_file="$server_results/server.log"

    info "Starting local $name server..."
    "${BASH:-bash}" -lc "source ~/.bashrc >/dev/null 2>&1 || true; cd \"$dir\" && $cmd" > "$log_file" 2>&1 &
    LOCAL_SERVER_PID=$!
}

benchmark_server() {
    local name=$1
    local container_port=${SERVERS[$name]}
    local container="${container_port%%:*}"
    local port="${container_port##*:}"
    local service="${name}-server"
    local url="http://localhost:$port/mcp"
    local server_results="$RESULTS_DIR/$name"

    mkdir -p "$server_results"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  BENCHMARKING: ${name^^}"
    echo "  Container: $container | Port: $port | URL: $url"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local fetch_endpoint="http://mock-api:1080/api"
    if [ "$NO_DOCKER" = true ]; then
        fetch_endpoint="http://localhost:$port/mcp"
    fi

    # 1. Stop all servers, start only the target
    stop_all_servers
    if [ "$NO_DOCKER" = true ]; then
        start_local_server "$name" "$server_results"
    else
        start_server "$service"
    fi

    # 2. Wait for health
    if ! wait_for_health "$port" "$name"; then
        error "Skipping $name — failed to start"
        return 1
    fi

    # 3. Warmup
    warmup "$url" "$name"

    local stats_pid=""
    # 4. Start stats collector in background
    if [ "$NO_DOCKER" = true ]; then
        info "Skipping Docker stats collector (--no-docker)"
        printf '{"mode":"no-docker"}\n' > "$server_results/stats.json"
    else
        info "Starting Docker stats collector..."
        python3 "$SCRIPT_DIR/collect_stats.py" "$container" "$server_results/stats.json" 1.0 &
        stats_pid=$!
        sleep 1
    fi

    # 5. Run k6 benchmark
    info "Running k6 benchmark (10 VUs, $K6_DURATION)..."
    k6 run \
        --env SERVER_URL="$url" \
        --env SERVER_NAME="$name" \
        --env K6_DURATION="$K6_DURATION" \
        --env FETCH_ENDPOINT="$fetch_endpoint" \
        --env OUTPUT_PATH="$server_results/k6.json" \
        "$SCRIPT_DIR/benchmark.js" \
        2>&1 | tee "$server_results/k6_console.log"

    # 6. Stop stats collector
    if [ -n "$stats_pid" ]; then
        info "Stopping stats collector..."
        kill "$stats_pid" 2>/dev/null || true
        wait "$stats_pid" 2>/dev/null || true
    fi

    ok "Benchmark complete for ${name^^}"
}

# ─── Main ─────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║           MCP SERVERS BENCHMARK SUITE                      ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  VUs: 10 | Duration: $K6_DURATION | CPU: 1 core | RAM: 1GB          ║"
    echo "║  Servers: python, go, nodejs, java, rust                   ║"
    echo "║  Mode: $( [ "$NO_DOCKER" = true ] && echo "local (--no-docker)" || echo "docker compose" )"
    echo "║  Results: $RESULTS_DIR"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    mkdir -p "$RESULTS_DIR"

    if [ "$NO_DOCKER" = false ]; then
        # Ensure mock-api is running
        info "Ensuring mock-api is running..."
        cd "$PROJECT_DIR"
        "${COMPOSE_CMD[@]}" up -d mock-api 2>/dev/null
        ok "mock-api is up"
    else
        info "Running in local mode (--no-docker); using per-server localhost fetch endpoint"
    fi

    # Benchmark each server
    for name in python go nodejs java rust; do
        benchmark_server "$name" || warn "Failed to benchmark $name, continuing..."
    done

    # Stop all servers
    stop_all_servers

    # Consolidate results
    echo ""
    info "Consolidating results..."
    python3 "$SCRIPT_DIR/consolidate.py" "$RESULTS_DIR"

    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  BENCHMARK COMPLETE                                        ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  Results: $RESULTS_DIR"
    echo "║  Summary: $RESULTS_DIR/summary.json"
    echo "╚══════════════════════════════════════════════════════════════╝"
}

cleanup() {
    if [ "$NO_DOCKER" = true ]; then
        stop_local_server
    fi
}

trap cleanup EXIT INT TERM

main "$@"
