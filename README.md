# Log Analytics Benchmarking

Benchmarking Elasticsearch, Qdrant, OpenSearch, and pgvector for hybrid vector log search under heavy write load.

<p align="center">
  <img src="assets/overview.svg" alt="Experimental overview" width="100%"/>
</p>

## Motivation

Modern observability stacks increasingly use vector search (dense + sparse embeddings) for semantic log retrieval. But log databases must also handle constant, bursty writes from microservices. This project measures how **query latency degrades during heavy write load** and how quickly each system recovers, comparing four backends side by side.

## How it works

The benchmark has three main components:

1. **[logstorm](https://github.com/qdrant-labs/logstorm)**  — A Rust tool that generates synthetic log entries at a configurable rate across multiple simulated microservices. Each log is embedded via OpenAI (`text-embedding-3-small`) and flushed in batches to one or more sinks.

2. **[qstorm](https://github.com/qdrant-labs/qstorm)** — A query load generator that continuously fires search queries against each backend in headless mode, recording per-burst latency stats (p50, p95, p99, QPS) as JSONL.

3. **Bench runner** (`bench.py`) — Orchestrates the full experiment across three phases:
   - **Steady-state**: queries only, no writes — establishes a baseline.
   - **Heavy-write**: the emitter is started, flooding all backends with ~420 logs/sec while queries continue.
   - **Recovery**: the emitter stops; measures how quickly latency returns to baseline.

## Pipeline

```
terraform apply          # 1. spin up EC2 instances
python generate_dataset.py  # 2. generate pre-embedded parquet dataset
python seed.py data/        # 3. bulk-load into backends
python bench.py             # 4. run the benchmark
python collect_metrics.py results/<run>  # 5. pull CloudWatch CPU/memory
python analysis.py results/<run>         # 6. generate plots
```

## Quick start

### Local (docker)

```bash
# start the backends
docker compose up -d

# install qstorm+logstorm
cargo install qstorm
cargo install logstorm

# generate and seed data
python generate_dataset.py
python seed.py data/

# run the benchmark (~6 min with defaults)
python bench.py

# analyze a run
python analysis.py results/<run-name>
```

### On AWS (reproducible, standardized hardware)

Each database backend runs on its own dedicated EC2 instance (`m6i.large` by default), keeping results reproducible and eliminating resource contention. The benchmark client (logstorm, qstorm, bench.py) still runs locally.

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

Terraform will create a VPC, launch one EC2 instance per backend, and write a `.env` file in the project root with the remote connection URLs. Wait ~2 minutes for Docker to finish pulling images, then seed and run:

```bash
cd ..
python generate_dataset.py
python seed.py data/
python bench.py
```

**SSH into an instance for debugging:**

After `terraform apply`, get the public IPs from the outputs:

```bash
terraform output qdrant_public_ip
terraform output elasticsearch_public_ip
terraform output opensearch_public_ip
terraform output pgvector_public_ip
```

Then SSH in with the key pair you registered:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@<public-ip>

# check that the container is running
sudo docker ps

# tail container logs
sudo docker logs -f qdrant        # or: elasticsearch, opensearch, postgres
```

When you're done, tear everything down:

```bash
cd infra/
terraform destroy
```

**Customizing the deployment:**

| Variable | Default | Description |
|---|---|---|
| `instance_type` | `m6i.large` | EC2 instance type (2 vCPU / 8 GiB) |
| `backends` | `["qdrant", "elasticsearch", "opensearch", "pgvector"]` | Which backends to launch |
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

All benchmark settings live in `bench_config.yaml`:

```yaml
index_mode: hybrid  # "vector" | "keyword" | "hybrid" — single source of truth

backends:
  - qdrant
  - elasticsearch
  - opensearch
  # - pgvector
```

Changing `index_mode` automatically propagates to seeding, qstorm queries, and logstorm sinks. The shared qstorm benchmark parameters (burst size, concurrency, timeout, embedding model) are also defined here — no need to edit per-backend config files.

`seed.py` can read this config directly:

```bash
# seed using backends and index_mode from bench_config.yaml
python seed.py data/ --config bench_config.yaml

# or override on the CLI
python seed.py data/ --backend qdrant --index-mode vector
```

## Project structure

```
bench.py                 # benchmark orchestrator
seed.py                  # async bulk-loader for all backends
generate_dataset.py      # pre-embedded parquet dataset generator
collect_metrics.py       # CloudWatch CPU/memory collector
analysis.py              # plotly visualization
bench_config.yaml        # benchmark parameters + index mode + backends
logstorm_base.yaml       # logstorm template (services/rates/embedding)
qstorm_configs/
  queries.yaml           # shared query corpus (65 pre-embedded queries)
logbench/                # backend module
  backends/
    base.py              # Backend ABC (seed, health_check, config generation)
    qdrant.py            # Qdrant implementation
    elasticsearch.py     # Elasticsearch implementation
    opensearch.py        # OpenSearch implementation
    pgvector.py          # pgvector implementation
  config.py              # BenchConfig dataclass + load_env()
  generators.py          # runtime qstorm/logstorm config generation
infra/                   # Terraform: VPC, EC2 instances, security group
docker-compose.yml       # local development (all four backends)
results/                 # output directory (JSONL + metadata per run)
```
