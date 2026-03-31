#!/usr/bin/env python3
"""
Extract steady-state descriptive statistics for each benchmark phase.

Segments qstorm JSONL data into three phases (pre-write, during-write,
post-write) using metadata timestamps, computes descriptive stats for
each backend, and appends to a CSV for cross-run comparison.

Usage:
    python extract_steady_state.py results/2026-03-31T20-00-00 --log-rate 41
    python extract_steady_state.py results/2026-03-31T20-00-00 --log-rate 410 --output steady_state.csv
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np


PHASES = {
    "pre_write": ("t_qstorm_start", "t_steady_end"),
    "during_write": ("t_heavy_start", "t_heavy_end"),
    "post_write": ("t_heavy_end", "t_recovery_end"),
}

METRICS = ["qps", "mean_us", "p50_us", "p95_us", "p99_us", "min_us", "max_us"]

CSV_COLUMNS = [
    "log_rate",
    "backend",
    "phase",
    "metric",
    "count",
    "mean",
    "std",
    "min",
    "p25",
    "median",
    "p75",
    "max",
]


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def extract_metric(record: dict, metric: str) -> float:
    if metric == "qps":
        return record["qps"]
    return record["latency"][metric]


def segment_by_phase(
    records: list[dict], metadata: dict
) -> dict[str, list[dict]]:
    """
    Split records into pre_write, during_write, post_write.
    """
    segments = {}
    for phase, (start_key, end_key) in PHASES.items():
        t_start = parse_ts(metadata[start_key])
        t_end = parse_ts(metadata[end_key])
        segments[phase] = [
            r for r in records
            if t_start <= parse_ts(r["timestamp"]) < t_end
        ]
    return segments


def compute_stats(values: list[float]) -> dict:
    if not values:
        return {k: None for k in ["count", "mean", "std", "min", "p25", "median", "p75", "max"]}
    arr = np.array(values)
    return {
        "count": len(arr),
        "mean": round(float(np.mean(arr)), 2),
        "std": round(float(np.std(arr, ddof=1)), 2) if len(arr) > 1 else 0.0,
        "min": round(float(np.min(arr)), 2),
        "p25": round(float(np.percentile(arr, 25)), 2),
        "median": round(float(np.median(arr)), 2),
        "p75": round(float(np.percentile(arr, 75)), 2),
        "max": round(float(np.max(arr)), 2),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract steady-state stats per phase from a benchmark run"
    )
    parser.add_argument("results_dir", help="Path to results directory")
    parser.add_argument(
        "--log-rate",
        type=float,
        required=True,
        help="Log emission rate (logs/sec) for this run — used as the grouping key in the CSV"
    )
    parser.add_argument(
        "--output",
        default="steady_state.csv",
        help="Output CSV path (appends if exists, default: steady_state.csv)"
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    metadata_path = results_dir / "metadata.json"
    if not metadata_path.exists():
        print(f"No metadata.json in {results_dir}", file=sys.stderr)
        sys.exit(1)

    with open(metadata_path) as f:
        metadata = json.load(f)

    # discover backends from JSONL files
    jsonl_files = sorted(results_dir.glob("*.jsonl"))
    if not jsonl_files:
        print(f"No .jsonl files in {results_dir}", file=sys.stderr)
        sys.exit(1)

    # prepare output
    output_path = Path(args.output)
    file_exists = output_path.exists()

    # check if this log_rate already has entries in the CSV
    if file_exists:
        existing = list(csv.DictReader(open(output_path)))
        has_rate = any(float(r["log_rate"]) == args.log_rate for r in existing)
        if has_rate:
            print(f"Warning: log_rate {args.log_rate} already has entries in {output_path}", file=sys.stderr)
            res = input("Overwrite? (y/N) ")
            if res.lower() != "y":
                print("Aborting.")
                sys.exit(1)
            # strip existing entries with this log_rate
            remaining = [r for r in existing if float(r["log_rate"]) != args.log_rate]
            with open(output_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                writer.writerows(remaining)

    rows = []

    for jsonl_path in jsonl_files:
        backend = jsonl_path.stem
        records = load_jsonl(jsonl_path)
        if not records:
            print(f"  {backend}: no records, skipping")
            continue

        segments = segment_by_phase(records, metadata)

        for phase, phase_records in segments.items():
            for metric in METRICS:
                values = [extract_metric(r, metric) for r in phase_records]
                # convert latency from microseconds to milliseconds
                if metric != "qps":
                    values = [v / 1000.0 for v in values]
                    metric_name = metric.replace("_us", "_ms")
                else:
                    metric_name = metric

                stats = compute_stats(values)
                rows.append({
                    "log_rate": args.log_rate,
                    "backend": backend,
                    "phase": phase,
                    "metric": metric_name,
                    **stats,
                })

    # append to CSV
    with open(output_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    # print summary
    print(f"Extracted stats for {len(jsonl_files)} backends × {len(PHASES)} phases × {len(METRICS)} metrics")
    print(f"  → {len(rows)} rows appended to {output_path}")

    # quick summary table
    print(f"\n{'Backend':<16} {'Phase':<15} {'QPS mean':>10} {'Mean ms':>10} {'p99 ms':>10}")
    print("-" * 65)
    for row in rows:
        if row["metric"] in ("qps", "mean_ms", "p99_ms") and row["count"]:
            pass  # we'll print grouped below

    # group by backend+phase, print key metrics
    from itertools import groupby
    keyfn = lambda r: (r["backend"], r["phase"])
    for (backend, phase), group in groupby(sorted(rows, key=keyfn), key=keyfn):
        metrics = {r["metric"]: r for r in group}
        qps = metrics.get("qps", {}).get("mean", "—")
        mean = metrics.get("mean_ms", {}).get("mean", "—")
        p99 = metrics.get("p99_ms", {}).get("mean", "—")
        print(f"{backend:<16} {phase:<15} {qps:>10} {mean:>10} {p99:>10}")


if __name__ == "__main__":
    main()