# Multi-Language MCP Server Performance Benchmark

A comprehensive experimental analysis comparing Model Context Protocol (MCP) server implementations across Java, Go, Node.js, Python, and Rust. Testing millions of requests over benchmark rounds to measure latency, throughput, resource efficiency, and production-readiness characteristics.

## Objective

This repository contains the source code and benchmark suite for a comprehensive performance analysis of Model Context Protocol (MCP) server implementations across five major programming ecosystems:

- **Java**: Spring Boot + Spring AI
- **Go**: Official SDK
- **Node.js**: Official SDK
- **Python**: FastMCP
- **Rust**: Official MCP Rust SDK (`rmcp`) + Axum + Tokio

The goal is to provide empirical data to inform architectural decisions for production MCP deployments by measuring latency, throughput, resource consumption, and reliability.

## Results & Analysis

For the full detailed results, analysis, and recommendations, please visit the experiment post:
**[https://www.tmdevlab.com/mcp-server-performance-benchmark.html](https://www.tmdevlab.com/mcp-server-performance-benchmark.html)**

### Key Findings Summary
- **Java and Go** demonstrated sub-millisecond average latencies (~0.8ms) with throughput >1,600 RPS.
- **Go** showed the highest resource efficiency (18MB memory vs Java's 220MB).
- **Node.js and Python** showed higher latencies (10-30x) but are suitable for development or moderate workloads.
- All implementations achieved **0% error rates** across 3.9 million requests.

## Project Structure

```
benchmark-mcp-servers/
├── java-server/    # Spring Boot 4.0.0 + Spring AI 2.0.0-M2
├── go-server/      # Official MCP SDK v1.2.0
├── nodejs-server/  # SDK v1.26.0 (with CVE-2026-25536 mitigation)
├── python-server/  # FastMCP 2.12.0+ + FastAPI
├── benchmark/      # k6 load testing scripts and tools
├── rust-server/    # Official MCP Rust SDK (`rmcp`) + Axum + Tokio server
└── docker-compose.yml
```

## Benchmark Tools

Each server implements four identical tools for fair comparison:

1.  **`calculate_fibonacci`**: CPU-intensive recursive computation.
2.  **`fetch_external_data`**: I/O-intensive HTTP GET request.
3.  **`process_json_data`**: specific data transformation.
4.  **`simulate_database_query`**: Controlled latency simulation.

## Running the Benchmark

### Prerequisites
- Docker & Docker Compose
- k6 (for running load tests locally if not using the containerized runner)

### Build and Start Servers

```bash
# Build all server images
docker-compose build

# Start all servers
docker-compose up -d

# Check status
docker-compose ps
```

The servers will be available at:
- Java: `http://localhost:8080`
- Go: `http://localhost:8081`
- Python: `http://localhost:8082`
- Node.js: `http://localhost:8083`
- Rust: `http://localhost:8084`

### Run Load Tests

**Option 1: Full Automated Benchmark**

Run the complete benchmark suite (all servers) using the orchestration script:

```bash
cd benchmark
./run_benchmark.sh
```

To run without Docker for the language servers (one local server at a time, same benchmark ports), use:

```bash
cd benchmark
./run_benchmark.sh --no-docker
```

To change sustained benchmark duration, pass minutes with `--duration=N` (works with or without Docker):

```bash
cd benchmark
./run_benchmark.sh --duration=1
```

**Option 2: Manual Single Server Test**

You can run k6 against a specific running server:

```bash
cd benchmark
k6 run -e SERVER_URL=http://localhost:8080/mcp benchmark.js
```

### Stop Servers

```bash
docker-compose down
```
