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

2. **[qstorm](https://github.com/qdrant-labs/qstorm)** — A query load generator that continuously fires search queries against each backend in headless mode, recording per-burst latency stats (p50, p95, p99, QPS) as JSONL.

3. **Bench runner** (`bench.py`) — Orchestrates the full experiment across three phases:
   - **Steady-state**: queries only, no writes — establishes a baseline.
   - **Heavy-write**: the emitter is started, flooding all backends with ~420 logs/sec while queries continue.
   - **Recovery**: the emitter stops; measures how quickly latency returns to baseline.

## Key findings

Under concurrent write load, **Qdrant maintains near-baseline query latency** while **Elasticsearch latency spikes dramatically** (p99 jumping from ~20 ms to >2,000 ms). Elasticsearch also recovers slowly after writes stop, with elevated tail latencies persisting well into the recovery window. Qdrant's recovery is near-instantaneous.

## Quick start

### Local (docker)

```bash
# start the backends
docker compose up -d

# install qstorm+logstorm
cargo install qstorm
cargo install logstorm

# run the benchmark (~6 min with defaults)
python bench.py

# skip pre-seeding if databases are already loaded
python bench.py --skip-load

# analyze a run
python analysis.py results/<run-name>
```

### On AWS (reproducible, standardized hardware)

Each database backend runs on its own dedicated EC2 instance (`m6i.xlarge` by default), keeping results reproducible and eliminating resource contention. The benchmark client (logstorm, qstorm, bench.py) still runs locally.

**Prerequisites:** AWS CLI configured (`aws configure`), Terraform installed.

```bash
# import your SSH public key to AWS (one-time setup)
aws ec2 import-key-pair \
  --key-name bench-key \
  --public-key-material fileb://~/.ssh/id_ed25519.pub \
  --region us-east-1

# spin up the backends
cd infra/
terraform init
terraform apply -var="key_pair_name=bench-key"
```

Terraform will create a VPC, launch one EC2 instance per backend, and write a `.env` file in the project root with the remote connection URLs. Wait ~2 minutes for Docker to finish pulling images, then run the benchmark as normal:

```bash
cd ..
python bench.py
```

**SSH into an instance for debugging:**

After `terraform apply`, get the public IPs from the outputs:

```bash
terraform output qdrant_public_ip
terraform output elasticsearch_public_ip
terraform output pgvector_public_ip
```

Then SSH in with the key pair you registered:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@<public-ip>

# check that the container is running
sudo docker ps

# tail container logs
sudo docker logs -f qdrant        # or: elasticsearch, postgres
```

When you're done, tear everything down:

```bash
cd infra/
terraform destroy
```

**Customizing the deployment:**

| Variable | Default | Description |
|---|---|---|
| `instance_type` | `m6i.xlarge` | EC2 instance type (4 vCPU / 16 GiB) |
| `backends` | `["qdrant", "elasticsearch", "pgvector"]` | Which backends to launch |
| `aws_region` | `us-east-1` | AWS region |
| `key_pair_name` | `""` | Key pair for SSH access (optional) |

```bash
# example: only launch qdrant and elasticsearch, in us-west-2
terraform apply \
  -var='backends=["qdrant","elasticsearch"]' \
  -var="aws_region=us-west-2" \
  -var="key_pair_name=bench-key"
```

## Configuration

- `bench_config.yaml` — phase durations, backends, logstorm config path
- `logstorm_config.yaml` — log generation rates, sinks, embedding config
- `qstorm_configs/` — per-backend query configurations
- `infra/` — Terraform config for AWS deployment

## Project structure

```
bench.py                 # benchmark orchestrator
analysis.py              # plotly visualization (Nature-style)
bench_config.yaml        # benchmark parameters
docker-compose.yml       # Elasticsearch, Qdrant, Postgres (local)
logstorm_config.yaml     # logstorm sink + rate configuration
qstorm_configs/          # qstorm query/connection configs
infra/                   # Terraform: VPC, EC2 instances, security group
results/                 # output directory (JSONL + metadata)
```