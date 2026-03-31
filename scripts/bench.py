"""
Benchmark runner for log-analytics-benchmarking.

Orchestrates logstorm and qstorm to measure query latency under write load
across three phases: steady-state, heavy-write, and recovery.

Usage:
    python bench.py                          # run with default config
    python bench.py -c my_config.yaml        # run with custom config
    python bench.py --dry-run                # print plan without running
"""

import argparse
import datetime
import json
import logging
import math
import os
import platform
import signal
import subprocess
import sys
import threading
import time

from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

sys.path.append(str(Path(__file__).parent.parent))  # for imports from logbench

from logbench import BenchConfig, get_backends, load_env
from logbench.generators import generate_qstorm_config, generate_logstorm_config

log = logging.getLogger("bench")


def compute_seed_duration(logstorm_base: str, target_logs: int) -> int:
    """
    Compute how long to run logstorm to produce ~target_logs.
    """
    with open(logstorm_base) as f:
        cfg = yaml.safe_load(f)
    total_rate = sum(s["rate_per_sec"] for s in cfg.get("services", []))
    if total_rate <= 0:
        log.warning("Could not determine log rate, defaulting to 60s seed")
        return 60
    return math.ceil(target_logs / total_rate) + 5


@dataclass
class RunMetadata:
    config: dict = field(default_factory=dict)
    hostname: str = ""
    platform_info: str = ""
    cpu_count: int = 0
    backend_instance_type: str = ""
    emitter_log_rate: float = 0.0
    seed_duration_secs: int = 0
    t_start: str = ""
    t_seed_done: str = ""
    t_qstorm_start: str = ""
    t_steady_end: str = ""
    t_heavy_start: str = ""
    t_heavy_end: str = ""
    t_recovery_end: str = ""
    t_end: str = ""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# track active subprocesses for cleanup on Ctrl-C
_active_procs: list[subprocess.Popen] = []


class _StderrWatcher:
    """
    Drains process stderr in a background thread, recording a timestamp
    when a marker string appears.
    """

    def __init__(self, proc: subprocess.Popen, marker: str):
        self.marker_time: str | None = None
        self._lines: list[str] = []
        stream = proc.stderr
        proc.stderr = None
        self._thread = threading.Thread(
            target=self._drain, args=(stream, marker), daemon=True,
        )
        self._thread.start()

    def _drain(self, stream, marker: str) -> None:
        for raw in stream:
            line = raw.decode(errors="replace")
            self._lines.append(line)
            if self.marker_time is None and marker in line:
                self.marker_time = now_iso()
        stream.close()

    def join(self, timeout: float = 10) -> None:
        self._thread.join(timeout=timeout)

    @property
    def output(self) -> str:
        return "".join(self._lines)


def start_emitter(logstorm_config_path: Path, duration_secs: int, env: dict) -> subprocess.Popen:
    """Start the logstorm process with a specific duration."""
    cmd = [
        "logstorm",
        "-c", str(logstorm_config_path),
        "--duration-secs", str(duration_secs),
    ]
    log.info("Starting logstorm (duration=%ds): %s", duration_secs, " ".join(cmd))
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _active_procs.append(proc)
    return proc


def wait_for_emitter(proc: subprocess.Popen, label: str, timeout: int) -> None:
    """Wait for emitter to finish with a timeout."""
    log.info("Waiting for emitter [%s] to finish (timeout=%ds)...", label, timeout)
    try:
        proc.wait(timeout=timeout)
        log.info("Emitter [%s] exited with code %d", label, proc.returncode)
        if proc.returncode != 0:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            log.error("Emitter [%s] failed with stderr:\n%s", label, stderr)
    except subprocess.TimeoutExpired:
        log.warning("Emitter [%s] did not finish in time, terminating", label)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    finally:
        if proc in _active_procs:
            _active_procs.remove(proc)


def start_qstorm(backend, config: BenchConfig, output_file: Path, env: dict) -> subprocess.Popen:
    """Start a qstorm process in headless mode, writing JSONL to output_file."""
    config_path = str(generate_qstorm_config(backend, config))
    queries_path = str(Path(config.qstorm["queries_file"]).resolve())

    cmd = [
        "qstorm",
        "-c", config_path,
        "-q", queries_path,
        "--headless",
        "--output", "json",
        "--bursts", "0",
    ]
    log.info("Starting qstorm [%s]: %s → %s", backend.name, " ".join(cmd), output_file)
    fh = open(output_file, "w")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.PIPE, env=env)
    proc._output_fh = fh  # type: ignore[attr-defined]
    _active_procs.append(proc)
    return proc


def stop_qstorm(proc: subprocess.Popen, backend_name: str) -> None:
    """Gracefully stop a qstorm process."""
    log.info("Stopping qstorm [%s] (pid=%d)...", backend_name, proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=15)
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.returncode != 0:
            log.error("qstorm [%s] exited with code %d:\n%s", backend_name, proc.returncode, stderr)
        else:
            log.info("qstorm [%s] exited with code %d", backend_name, proc.returncode)
            if stderr:
                log.debug("qstorm [%s] stderr:\n%s", backend_name, stderr)
    except subprocess.TimeoutExpired:
        log.warning("qstorm [%s] did not exit, killing", backend_name)
        proc.kill()
        proc.wait()
    finally:
        if hasattr(proc, "_output_fh"):
            proc._output_fh.close()  # type: ignore[attr-defined]
        if proc in _active_procs:
            _active_procs.remove(proc)


async def check_backends_healthy(backends) -> bool:
    """Run health checks against all backends."""
    all_ok = True
    for b in backends:
        ok = await b.health_check()
        if ok:
            log.info("  %s: OK", b.name)
        else:
            log.error("  %s: FAILED", b.name)
            all_ok = False
    return all_ok


def run_benchmark(config: BenchConfig, skip_load: bool = False) -> None:
    import asyncio

    run_name = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out_dir = Path(config.results_dir).resolve() / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Benchmark run: %s", run_name)
    log.info("Output: %s", out_dir)
    log.info("Index mode: %s", config.index_mode)
    log.info("=" * 60)

    env = load_env()
    backends = get_backends(config.backends, env)

    # generate logstorm config
    logstorm_config_path = generate_logstorm_config(backends, config)
    log.info("Generated logstorm config: %s", logstorm_config_path)

    # compute seed duration from logstorm base
    seed_secs = compute_seed_duration(config.logstorm_base, config.pre_seed_logs)
    base_cfg = yaml.safe_load(Path(config.logstorm_base).read_text())
    total_rate = sum(s["rate_per_sec"] for s in base_cfg.get("services", []))

    metadata = RunMetadata(
        config=asdict(config),
        hostname=platform.node(),
        platform_info=f"{platform.system()} {platform.release()}",
        cpu_count=os.cpu_count() or 0,
        backend_instance_type=env.get("BACKEND_INSTANCE_TYPE", "local"),
        emitter_log_rate=total_rate,
        seed_duration_secs=seed_secs,
    )

    # --- Health check ---
    log.info("Checking service health...")
    if not asyncio.run(check_backends_healthy(backends)):
        log.error("Service health checks failed. Are backends running?")
        sys.exit(1)

    # --- Phase 0: Pre-seed ---
    metadata.t_start = now_iso()
    if skip_load:
        log.info("Phase 0: Skipping pre-seed (--skip-load)")
        metadata.t_seed_done = metadata.t_start
    else:
        log.info(
            "Phase 0: Pre-seeding ~%d logs (%ds at ~%.0f logs/s)...",
            config.pre_seed_logs, seed_secs, total_rate,
        )
        emitter_seed = start_emitter(logstorm_config_path, seed_secs, env)
        wait_for_emitter(emitter_seed, "pre-seed", timeout=seed_secs + 60)
        metadata.t_seed_done = now_iso()

        log.info("Sleeping 30s for index settle...")
        time.sleep(30)

    # --- Phase 1: Start qstorm ---
    log.info("Phase 1: Starting qstorm against all backends...")
    qstorm_procs = {}
    for backend in backends:
        output_file = out_dir / f"{backend.name}.jsonl"
        qstorm_procs[backend.name] = start_qstorm(backend, config, output_file, env)
    metadata.t_qstorm_start = now_iso()

    # --- Phase 2: Steady-state ---
    log.info("Phase 2: Steady-state measurement for %ds...", config.steady_state_secs)
    time.sleep(config.steady_state_secs)
    metadata.t_steady_end = now_iso()
    log.info("Phase 2 complete (t_on)")

    # --- Phase 3+4: Heavy write ---
    log.info("Phase 3: Starting heavy write load (%ds)...", config.heavy_write_secs)
    emitter_heavy = start_emitter(logstorm_config_path, config.heavy_write_secs, env)
    watcher = _StderrWatcher(emitter_heavy, "Emitter running")
    log.info("Phase 4: Heavy write measurement in progress...")
    wait_for_emitter(emitter_heavy, "heavy-write", timeout=config.heavy_write_secs + 60)
    watcher.join()
    metadata.t_heavy_start = watcher.marker_time or metadata.t_steady_end
    metadata.t_heavy_end = now_iso()
    log.info(
        "Phase 5: Emitter stopped (t_off), write start detected at %s",
        metadata.t_heavy_start,
    )

    # --- Phase 6: Recovery ---
    log.info("Phase 6: Recovery measurement for %ds...", config.recovery_secs)
    time.sleep(config.recovery_secs)
    metadata.t_recovery_end = now_iso()
    log.info("Phase 6 complete")

    # --- Phase 7: Stop qstorm ---
    log.info("Phase 7: Stopping qstorm processes...")
    for name, proc in qstorm_procs.items():
        stop_qstorm(proc, name)
    metadata.t_end = now_iso()

    # --- Write outputs ---
    metadata_path = out_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(asdict(metadata), f, indent=2)

    config_copy = out_dir / "bench_config.yaml"
    with open(config_copy, "w") as f:
        yaml.dump(asdict(config), f, default_flow_style=False)

    # --- Summary ---
    log.info("=" * 60)
    log.info("Benchmark complete: %s", run_name)
    log.info("Results: %s", out_dir)
    for backend in backends:
        jsonl_path = out_dir / f"{backend.name}.jsonl"
        lines = sum(1 for _ in open(jsonl_path)) if jsonl_path.exists() else 0
        log.info("  %s: %d bursts recorded", backend.name, lines)
    log.info("=" * 60)


def cleanup_handler(signum, frame):
    log.warning("Received signal %d, cleaning up...", signum)
    for proc in list(_active_procs):
        try:
            proc.terminate()
        except Exception:
            pass
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark runner for log-analytics-benchmarking",
    )
    parser.add_argument(
        "-c", "--config",
        default="bench_config.yaml",
        help="Path to benchmark config (default: bench_config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan and exit without running",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed databases via logstorm before benchmarking (default: skip, use seed.py instead)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    signal.signal(signal.SIGINT, cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)

    config = BenchConfig.from_yaml(args.config)

    if args.dry_run:
        seed_secs = compute_seed_duration(config.logstorm_base, config.pre_seed_logs)
        total = seed_secs + 5 + config.steady_state_secs + config.heavy_write_secs + config.recovery_secs
        print("=== DRY RUN ===")
        print(f"Index mode:    {config.index_mode}")
        print(f"Pre-seed:      ~{config.pre_seed_logs:,} logs ({seed_secs}s)")
        print(f"Steady state:  {config.steady_state_secs}s")
        print(f"Heavy write:   {config.heavy_write_secs}s")
        print(f"Recovery:      {config.recovery_secs}s")
        print(f"Total:         ~{total}s ({total // 60}m {total % 60}s)")
        print(f"Backends:      {config.backends}")
        return

    run_benchmark(config, skip_load=not args.seed)


if __name__ == "__main__":
    main()
