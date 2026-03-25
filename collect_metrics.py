#!/usr/bin/env python3
"""
Collect CPU and memory metrics from CloudWatch for benchmark EC2 instances.

Reads instance IDs and region from .env (written by terraform), and reads
the time range from metadata.json in the results directory (written by bench.py).

Usage:
    # collect metrics for a benchmark run (reads times from metadata.json)
    python collect_metrics.py results/2026-03-24T16-17-30

    # override the time range manually
    python collect_metrics.py results/2026-03-24T16-17-30
"""

import argparse
import csv
import json
import os
import sys

from datetime import datetime
from pathlib import Path

try:
    import boto3
except ImportError:
    print("boto3 is required: pip install boto3", file=sys.stderr)
    sys.exit(1)


def load_env(env_path: str = ".env") -> dict:
    env = {}
    path = Path(env_path)
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def get_metric(cw, namespace: str, metric_name: str, instance_id: str,
               start: datetime, end: datetime, period: int = 60) -> list[dict]:
    resp = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=period,
        Statistics=["Average"],
    )
    points = resp.get("Datapoints", [])
    points.sort(key=lambda p: p["Timestamp"])
    return points


def write_csv(points: list[dict], path: Path, metric_name: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", metric_name])
        for p in points:
            writer.writerow([p["Timestamp"].isoformat(), round(p["Average"], 2)])


def main():
    parser = argparse.ArgumentParser(description="Collect CloudWatch metrics for benchmark instances")
    parser.add_argument("results_dir", help="Path to results directory (e.g. results/2026-03-24T16-17-30)")
    parser.add_argument("--start", help="Override start time (ISO 8601)")
    parser.add_argument("--end", help="Override end time (ISO 8601)")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--period", type=int, default=60, help="Metric period in seconds (default: 60)")
    args = parser.parse_args()

    env = load_env(args.env)
    region = env.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))

    # read time range from metadata.json unless overridden
    out_dir = Path(args.results_dir)
    metadata_path = out_dir / "metadata.json"
    if args.start and args.end:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
    elif metadata_path.exists():
        with open(metadata_path) as f:
            metadata = json.load(f)
        start = datetime.fromisoformat(metadata["t_start"])
        end = datetime.fromisoformat(metadata["t_end"])
        print(f"Read time range from metadata.json: {start} → {end}")
    else:
        print(f"No metadata.json in {out_dir} and no --start/--end provided.", file=sys.stderr)
        sys.exit(1)

    # discover which instances we have
    instances = {}
    for name, env_key in [("qdrant", "QDRANT_INSTANCE_ID"),
                           ("elasticsearch", "ELASTIC_INSTANCE_ID"),
                           ("pgvector", "PGVECTOR_INSTANCE_ID")]:
        iid = env.get(env_key, os.environ.get(env_key, ""))
        if iid:
            instances[name] = iid

    if not instances:
        print("No instance IDs found in .env — is terraform infrastructure running?", file=sys.stderr)
        sys.exit(1)

    cw = boto3.client("cloudwatch", region_name=region)
    out_dir.mkdir(parents=True, exist_ok=True)

    metrics_summary = {}

    for backend, instance_id in instances.items():
        print(f"Collecting metrics for {backend} ({instance_id})...")

        # CPU (built-in EC2 metric)
        cpu_points = get_metric(cw, "AWS/EC2", "CPUUtilization", instance_id,
                                start, end, args.period)
        if cpu_points:
            csv_path = out_dir / f"{backend}_cpu.csv"
            write_csv(cpu_points, csv_path, "cpu_percent")
            print(f"  CPU: {len(cpu_points)} datapoints → {csv_path}")
        else:
            print("  CPU: no datapoints found")

        # Memory (CloudWatch agent metric)
        mem_points = get_metric(cw, "CWAgent", "mem_used_percent", instance_id,
                                start, end, args.period)
        if mem_points:
            csv_path = out_dir / f"{backend}_memory.csv"
            write_csv(mem_points, csv_path, "memory_percent")
            print(f"  Memory: {len(mem_points)} datapoints → {csv_path}")
        else:
            print("  Memory: no datapoints (CloudWatch agent may need a few minutes to start reporting)")

        metrics_summary[backend] = {
            "instance_id": instance_id,
            "cpu_datapoints": len(cpu_points),
            "memory_datapoints": len(mem_points),
        }

    # write summary
    summary_path = out_dir / "metrics_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "region": region,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "period_seconds": args.period,
            "backends": metrics_summary,
        }, f, indent=2)
    print(f"\nSummary → {summary_path}")


if __name__ == "__main__":
    main()