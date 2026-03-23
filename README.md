# Qdrant vs Elasticsearch Log Search Benchmark

Benchmarking Elasticsearch and Qdrant for hybrid vector log search under heavy write load.

<p align="center">
  <img src="assets/overview.svg" alt="Experimental overview" width="100%"/>
</p>

## Motivation

Modern observability stacks increasingly use vector search (dense + sparse embeddings) for semantic log retrieval. But log databases must also handle constant, bursty writes from microservices. This project measures how **query latency degrades during heavy write load** and how quickly each system recovers, comparing Elasticsearch and Qdrant side by side.

## How it works

The benchmark has three main components:

1. **[logstorm](https://github.com/qdrant-labs/logstorm)**  — A Rust tool that generates synthetic log entries at a configurable rate across multiple simulated microservices. Each log is embedded via OpenAI (`text-embedding-3-small`) and flushed in batches to one or more sinks (It currently supports Elasticsearch, Qdrant, and pgvector).

2. **[qstorm](https://github.com/nleroy917/qstorm)** — A query load generator that continuously fires search queries against each backend in headless mode, recording per-burst latency stats (p50, p95, p99, QPS) as JSONL.

3. **Bench runner** (`bench.py`) — Orchestrates the full experiment across three phases:
   - **Steady-state**: queries only, no writes — establishes a baseline.
   - **Heavy-write**: the emitter is started, flooding all backends with ~420 logs/sec while queries continue.
   - **Recovery**: the emitter stops; measures how quickly latency returns to baseline.

## Key findings

Under concurrent write load, **Qdrant maintains near-baseline query latency** while **Elasticsearch latency spikes dramatically** (p99 jumping from ~20 ms to >2,000 ms). Elasticsearch also recovers slowly after writes stop, with elevated tail latencies persisting well into the recovery window. Qdrant's recovery is near-instantaneous.

## Quick start

```bash
# start the backends
docker compose up -d

# install qstorm
cargo install qstorm

# run the benchmark (~6 min with defaults)
python bench.py

# skip pre-seeding if databases are already loaded
python bench.py --skip-load

# analyze a run
python analysis.py results/<run-name>
```

## Configuration

- `bench_config.yaml` — phase durations, backends, emitter settings
- `emitter/config.yaml` — log generation rates, sinks, embedding config
- `qstorm_configs/` — per-backend query configurations

## Project structure

```
bench.py                 # benchmark orchestrator
analysis.py              # plotly visualization (Nature-style)
bench_config.yaml        # benchmark parameters
docker-compose.yml       # Elasticsearch, Qdrant, Postgres
emitter/                 # synthetic log generator (Rust)
  src/sink/              # pluggable sinks (ES, Qdrant, pgvector)
qstorm_configs/          # qstorm query/connection configs
results/                 # output directory (JSONL + metadata)
```