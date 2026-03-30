"""
Generate a pre-embedded log dataset for benchmarking.

Replicates logstorm's message generation patterns and embeds messages via
OpenAI, producing a parquet file that can be bulk-loaded into Qdrant/Elasticsearch
using seed.py.

Usage:
    python generate_dataset.py --output logs-1m.parquet --count 1000000
    python generate_dataset.py --output test.parquet --count 1000 --pool-size 500
"""

import argparse
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import polars as pl
from openai import OpenAI
from tqdm import tqdm


COMPONENTS = [
    "ConnectionPool","QueryExecutor", "AuthManager", "CacheLayer",
    "LoadBalancer", "RateLimiter", "SchemaValidator", "EventBus",
    "HealthMonitor", "SessionStore", "CircuitBreaker", "JobScheduler",
    "ReplicaManager", "PartitionConsumer", "TLSHandler", "GarbageCollector",
    "DiskMonitor", "DeadLetterQueue", "IdempotencyFilter", "TraceContext",
]

ACTIONS = [
    "detected threshold breach", "completed successfully", "failed after retries",
    "initiated graceful recovery", "rejected invalid request", "triggered rebalance",
    "exceeded soft limit", "evicted stale entry", "propagated context",
    "acquired resource handle", "flushed pending writes", "rotated credentials",
    "promoted fallback path", "checkpointed at offset", "enqueued background task",
    "resolved after backoff", "timed out waiting", "received reset signal",
    "applied migration", "scheduled maintenance",
]

METRICS = [
    "latency=2340ms", "count=184302", "ratio=0.94", "depth=500",
    "attempt=3/5", "usage=85%", "lag=500ms", "size=1.2MB",
    "rate=120/s", "ttl=3600s", "connections=980/1024", "duration=30s",
    "retries=3", "offset=48291", "queue_depth=1247", "p99=450ms",
    "batch_size=500", "memory=2.4GB", "iops=12000", "threads=48",
]

TARGETS = [
    "on orders table", "for payments-api", "from upstream host",
    "in consumer group", "on /data volume", "for tenant af923c",
    "to downstream service", "across service boundary", "in container cgroup",
    "from replica node-3", "on topic order.completed", "for client session",
    "in write-ahead log", "on port 8443", "from discovery endpoint",
    "in ring buffer", "for user session", "on primary shard",
    "to dead letter queue", "from environment config",
]

CONTEXTS = [
    "(retrying)", "(non-blocking)", "(scheduled)", "(cached response)",
    "(dark_launch=true)", "(read-only)", "(best-effort)", "(idempotent)",
    "(correlation_id=missing)", "(circuit=open)", "(degraded mode)",
    "(cold start)", "(warm path)", "(fallback)", "(async)",
    "(batched)", "(compressed)", "(encrypted)", "(sampled)", "(throttled)",
]

SERVICES = [
    {"name": "api-gateway", "rate_weight": 0.36, "levels": {"DEBUG": 0.1, "INFO": 0.7, "WARN": 0.15, "ERROR": 0.05}},
    {"name": "auth-service", "rate_weight": 0.12, "levels": {"DEBUG": 0.05, "INFO": 0.6, "WARN": 0.2, "ERROR": 0.15}},
    {"name": "payment-service", "rate_weight": 0.04, "levels": {"DEBUG": 0.05, "INFO": 0.5, "WARN": 0.25, "ERROR": 0.2}},
    {"name": "user-service", "rate_weight": 0.48, "levels": {"DEBUG": 0.1, "INFO": 0.65, "WARN": 0.15, "ERROR": 0.1}},
]


def generate_message(rng: random.Random) -> str:
    component = rng.choice(COMPONENTS)
    action = rng.choice(ACTIONS)
    metric = rng.choice(METRICS)
    target = rng.choice(TARGETS)
    context = rng.choice(CONTEXTS)

    pattern = rng.randint(0, 3)
    if pattern == 0:
        return f"{component}: {action} {target} {context}"
    elif pattern == 1:
        return f"{component}: {action} [{metric}] {target}"
    elif pattern == 2:
        return f"{component}: {action} [{metric}]"
    else:
        return f"{component}: {action} {target} [{metric}] {context}"


def build_message_pool(rng: random.Random, size: int) -> list[str]:
    pool: set[str] = set()
    while len(pool) < size:
        pool.add(generate_message(rng))
    return list(pool)


def weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    items = list(weights.keys())
    vals = list(weights.values())
    total = sum(vals)
    roll = rng.random() * total
    cumulative = 0.0
    for item, w in zip(items, vals):
        cumulative += w
        if roll < cumulative:
            return item
    return items[-1]


def embed_messages(
    messages: list[str],
    model: str = "text-embedding-3-small",
    batch_size: int = 2048
) -> dict[str, list[float]]:
    client = OpenAI()
    embeddings = {}
    for i in tqdm(range(0, len(messages), batch_size), desc="Embedding"):
        batch = messages[i:i + batch_size]
        resp = client.embeddings.create(input=batch, model=model)
        for j, item in enumerate(resp.data):
            embeddings[batch[j]] = item.embedding
    return embeddings


def jitter_embedding(
    embedding: list[float],
    rng: random.Random,
    scale: float = 0.01
) -> list[float]:
    arr = np.array(embedding, dtype=np.float32)
    noise = np.array([rng.gauss(0, scale) for _ in range(len(arr))], dtype=np.float32)
    result = arr + noise
    # re-normalize to unit length
    norm = np.linalg.norm(result)
    if norm > 0:
        result = result / norm
    return result.tolist()


def main():
    parser = argparse.ArgumentParser(description="Generate pre-embedded log dataset")
    parser.add_argument("-o", "--output", default="logs-1m.parquet", help="Output parquet path")
    parser.add_argument("-n", "--count", type=int, default=1_000_000, help="Number of log entries")
    parser.add_argument("--pool-size", type=int, default=50_000, help="Unique message pool size")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--model", default="text-embedding-3-small", help="Embedding model")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    # 1. generate message pool
    print(f"Generating {args.pool_size:,} unique messages...")
    pool = build_message_pool(rng, args.pool_size)
    print(f"  Pool size: {len(pool):,} unique messages")

    # 2. embed all unique messages
    print(f"Embedding {len(pool):,} messages via {args.model}...")
    embeddings = embed_messages(pool, model=args.model)
    print(f"  Embedded {len(embeddings):,} messages ({len(next(iter(embeddings.values())))} dims)")

    # 3. generate log entries in chunks, writing temp parquet files
    chunk_size = 50_000
    print(f"Generating {args.count:,} log entries in chunks of {chunk_size:,}...")
    service_weights = [s["rate_weight"] for s in SERVICES]
    total_weight = sum(service_weights)
    service_probs = [w / total_weight for w in service_weights]

    base_time = datetime.now(timezone.utc) - timedelta(hours=1)
    tmp_dir = Path(args.output).parent / ".tmp_chunks"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    chunk_files = []

    for chunk_start in tqdm(range(0, args.count, chunk_size), desc="Generating"):
        chunk_end = min(chunk_start + chunk_size, args.count)
        ids = []
        timestamps = []
        services = []
        levels_list = []
        messages_list = []
        entry_embeddings = []

        for i in range(chunk_start, chunk_end):
            service = rng.choices(SERVICES, weights=service_probs, k=1)[0]
            level = weighted_choice(rng, service["levels"])
            message = rng.choice(pool)
            base_emb = embeddings[message]
            emb = jitter_embedding(base_emb, rng, scale=0.01)
            ts = base_time + timedelta(seconds=i * (3600 / args.count))

            ids.append(str(uuid.uuid4()))
            timestamps.append(ts)
            services.append(service["name"])
            levels_list.append(level)
            messages_list.append(message)
            entry_embeddings.append(emb)

        chunk_path = tmp_dir / f"chunk_{chunk_start:010d}.parquet"
        pl.DataFrame({
            "id": ids,
            "timestamp": timestamps,
            "service": services,
            "level": levels_list,
            "message": messages_list,
            "embedding": entry_embeddings,
        }).write_parquet(chunk_path)
        chunk_files.append(chunk_path)

    # 4. merge chunks into final parquet
    print(f"Merging {len(chunk_files)} chunks into {args.output}...")
    pl.scan_parquet(tmp_dir / "*.parquet").sink_parquet(args.output)

    # cleanup temp files
    for f in chunk_files:
        f.unlink()
    tmp_dir.rmdir()

    file_size_mb = Path(args.output).stat().st_size / (1024 * 1024)
    print(f"Done: {args.output} ({file_size_mb:.0f} MB, {args.count:,} rows)")


if __name__ == "__main__":
    main()
