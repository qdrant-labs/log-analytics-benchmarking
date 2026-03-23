"""
Benchmark runner for es-qdrant-log-demo.

Orchestrates the emitter and qstorm tools to measure Elasticsearch and Qdrant
query latency under write load across three phases: steady-state, heavy-write,
and recovery.

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
import tempfile
import threading
import time

from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

log = logging.getLogger("bench")

@dataclass
class BenchConfig:
    pre_seed_logs: int = 100_000
    steady_state_secs: int = 60
    heavy_write_secs: int = 120
    recovery_secs: int = 60
    emitter_dir: str = "./emitter"
    emitter_features: str = "qdrant,elasticsearch,dashboard"
    qstorm_configs_dir: str = "./qstorm_configs"
    results_dir: str = "./results"
    backends: dict = field(default_factory=lambda: {
        "qdrant": {"config": "qdrant.yaml", "queries": "queries.yaml"},
        "elasticsearch": {"config": "elastic.yaml", "queries": "queries.yaml"},
    })

    @classmethod
    def from_yaml(cls, path: str) -> "BenchConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def compute_seed_duration(emitter_dir: str, target_logs: int) -> int:
    """
    Read emitter config.yaml and compute how long to run to produce ~target_logs.
    """
    config_path = Path(emitter_dir) / "config.yaml"
    with open(config_path) as f:
        raw = f.read()
    # crude parse — env vars won't affect rates, which are plain numbers
    cfg = yaml.safe_load(raw)
    total_rate = sum(s["rate_per_sec"] for s in cfg.get("services", []))
    if total_rate <= 0:
        log.warning("Could not determine log rate, defaulting to 60s seed")
        return 60
    secs = math.ceil(target_logs / total_rate) + 5  # +5s buffer for final flush
    return secs


@dataclass
class RunMetadata:
    config: dict = field(default_factory=dict)
    hostname: str = ""
    platform_info: str = ""
    cpu_count: int = 0
    emitter_log_rate: float = 0.0
    seed_duration_secs: int = 0
    t_start: str = ""
    t_seed_done: str = ""
    t_qstorm_start: str = ""
    t_steady_end: str = ""
    t_heavy_start: str = ""      # actual write start (after embedding)
    t_heavy_end: str = ""
    t_recovery_end: str = ""
    t_end: str = ""


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# track active subprocesses for cleanup on Ctrl-C
_active_procs: list[subprocess.Popen] = []


def _make_env() -> dict:
    """
    Build subprocess environment: inherit current env + source .env file.
    """
    env = os.environ.copy()
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _render_qstorm_config(config_path: Path, env: dict) -> Path:
    """
    Override provider URLs in a qstorm config from environment variables.

    If no relevant env vars are set, returns the original path unchanged.
    Otherwise writes a modified copy to a temp directory and returns that path.
    """
    cfg = yaml.safe_load(config_path.read_text())
    provider_type = cfg.get("provider", {}).get("type", "")
    modified = False

    if provider_type == "elasticsearch" and env.get("ELASTIC_URL"):
        cfg["provider"]["url"] = env["ELASTIC_URL"]
        if "credentials" not in cfg["provider"]:
            cfg["provider"]["credentials"] = {"type": "basic"}
        cfg["provider"]["credentials"]["username"] = env.get("ELASTIC_USER", "elastic")
        cfg["provider"]["credentials"]["password"] = env.get("ELASTIC_PASSWORD", "changeme")
        modified = True
    elif provider_type == "qdrant" and env.get("QDRANT_URL"):
        url = env["QDRANT_URL"]
        # qstorm uses gRPC (6334), QDRANT_URL may point to REST (6333)
        cfg["provider"]["url"] = url.replace(":6333", ":6334")
        modified = True
    elif provider_type == "pgvector" and env.get("PGVECTOR_HOST"):
        user = env.get("PGVECTOR_USER", "postgres")
        pw = env.get("PGVECTOR_PASSWORD", "changeme")
        host = env["PGVECTOR_HOST"]
        cfg["provider"]["url"] = f"postgresql://{user}:{pw}@{host}:5432/logs"
        modified = True

    if not modified:
        return config_path

    tmp_dir = Path(tempfile.mkdtemp(prefix="qstorm_"))
    tmp_path = tmp_dir / config_path.name
    with open(tmp_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
    log.info("Rendered qstorm config %s → %s", config_path.name, tmp_path)
    return tmp_path


class _StderrWatcher:
    """
    Drains process stderr in a background thread, recording a timestamp
    when a marker string appears.  Takes ownership of proc.stderr so that
    wait_for_emitter (which checks ``proc.stderr``) won't conflict.
    """

    def __init__(self, proc: subprocess.Popen, marker: str):
        self.marker_time: str | None = None
        self._lines: list[str] = []
        stream = proc.stderr
        proc.stderr = None  # prevent double-read in wait_for_emitter
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


def start_emitter(config: BenchConfig, duration_secs: int, env: dict) -> subprocess.Popen:
    """
    Start the emitter process with a specific duration.
    """
    emitter_dir = Path(config.emitter_dir).resolve()
    cmd = [
        "cargo", "run", "--release",
        "--features", config.emitter_features,
        "--", "--duration-secs", str(duration_secs),
    ]
    log.info("Starting emitter (duration=%ds): %s", duration_secs, " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(emitter_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    _active_procs.append(proc)
    return proc


def wait_for_emitter(proc: subprocess.Popen, label: str, timeout: int) -> None:
    """
    Wait for emitter to finish with a timeout.
    """
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


def start_qstorm(
    config: BenchConfig,
    backend_name: str,
    output_file: Path,
    env: dict,
) -> subprocess.Popen:
    """
    Start a qstorm process in headless mode, writing JSONL to output_file.
    """
    configs_dir = Path(config.qstorm_configs_dir).resolve()
    backend_cfg = config.backends[backend_name]
    raw_config_path = configs_dir / backend_cfg["config"]
    config_path = str(_render_qstorm_config(raw_config_path, env))
    queries_path = str(configs_dir / backend_cfg["queries"])

    cmd = [
        "qstorm",
        "-c", config_path,
        "-q", queries_path,
        "--headless",
        "--output", "json",
        "--bursts", "0",
    ]
    log.info("Starting qstorm [%s]: %s → %s", backend_name, " ".join(cmd), output_file)
    fh = open(output_file, "w")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.PIPE, env=env)
    proc._output_fh = fh  # type: ignore[attr-defined]
    _active_procs.append(proc)
    return proc


def stop_qstorm(proc: subprocess.Popen, backend_name: str) -> None:
    """
    Gracefully stop a qstorm process.
    """
    log.info("Stopping qstorm [%s] (pid=%d)...", backend_name, proc.pid)
    proc.terminate()
    try:
        proc.wait(timeout=15)
        log.info("qstorm [%s] exited with code %d", backend_name, proc.returncode)
    except subprocess.TimeoutExpired:
        log.warning("qstorm [%s] did not exit, killing", backend_name)
        proc.kill()
        proc.wait()
    finally:
        if hasattr(proc, "_output_fh"):
            proc._output_fh.close()  # type: ignore[attr-defined]
        if proc in _active_procs:
            _active_procs.remove(proc)


def check_services_healthy(env: dict) -> bool:
    """
    Quick health check that ES and Qdrant are reachable.
    Reads connection URLs from env with localhost fallbacks.
    """
    import urllib.request
    import base64

    qdrant_base = env.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
    # Health endpoint is on REST port (6333), not gRPC (6334)
    qdrant_health = qdrant_base.replace(":6334", ":6333") + "/healthz"

    elastic_base = env.get("ELASTIC_URL", "http://localhost:9200").rstrip("/")
    elastic_user = env.get("ELASTIC_USER", "elastic")
    elastic_pass = env.get("ELASTIC_PASSWORD", "changeme")

    checks = {
        "Qdrant": (qdrant_health, None),
        "Elasticsearch": (
            f"{elastic_base}/_cluster/health",
            "Basic " + base64.b64encode(f"{elastic_user}:{elastic_pass}".encode()).decode(),
        ),
    }
    all_ok = True
    for name, (url, auth) in checks.items():
        try:
            req = urllib.request.Request(url)
            if auth:
                req.add_header("Authorization", auth)
            resp = urllib.request.urlopen(req, timeout=10)
            log.info("  %s: OK (%d)", name, resp.status)
        except Exception as e:
            log.error("  %s: FAILED (%s)", name, e)
            all_ok = False
    return all_ok


def run_benchmark(config: BenchConfig, skip_load: bool = False) -> None:
    run_name = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    out_dir = Path(config.results_dir).resolve() / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Benchmark run: %s", run_name)
    log.info("Output: %s", out_dir)
    log.info("=" * 60)

    env = _make_env()

    # compute seed duration from emitter config
    seed_secs = compute_seed_duration(config.emitter_dir, config.pre_seed_logs)
    emitter_cfg = yaml.safe_load(
        (Path(config.emitter_dir) / "config.yaml").read_text()
    )
    total_rate = sum(s["rate_per_sec"] for s in emitter_cfg.get("services", []))

    metadata = RunMetadata(
        config=asdict(config),
        hostname=platform.node(),
        platform_info=f"{platform.system()} {platform.release()}",
        cpu_count=os.cpu_count() or 0,
        emitter_log_rate=total_rate,
        seed_duration_secs=seed_secs,
    )

    # --- Health check ---
    log.info("Checking service health...")
    if not check_services_healthy(env):
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
        emitter_seed = start_emitter(config, seed_secs, env)
        wait_for_emitter(emitter_seed, "pre-seed", timeout=seed_secs + 60)
        metadata.t_seed_done = now_iso()

        log.info("Sleeping 5s for index settle...")
        time.sleep(5)

    # --- Phase 1: Start qstorm ---
    log.info("Phase 1: Starting qstorm against all backends...")
    qstorm_procs = {}
    for backend_name in config.backends:
        output_file = out_dir / f"{backend_name}.jsonl"
        qstorm_procs[backend_name] = start_qstorm(config, backend_name, output_file, env)
    metadata.t_qstorm_start = now_iso()

    # --- Phase 2: Steady-state ---
    log.info("Phase 2: Steady-state measurement for %ds...", config.steady_state_secs)
    time.sleep(config.steady_state_secs)
    metadata.t_steady_end = now_iso()
    log.info("Phase 2 complete (t_on)")

    # --- Phase 3+4: Heavy write ---
    log.info("Phase 3: Starting heavy write load (%ds)...", config.heavy_write_secs)
    emitter_heavy = start_emitter(config, config.heavy_write_secs, env)
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
    for backend_name, proc in qstorm_procs.items():
        stop_qstorm(proc, backend_name)
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
    for backend_name in config.backends:
        jsonl_path = out_dir / f"{backend_name}.jsonl"
        lines = sum(1 for _ in open(jsonl_path)) if jsonl_path.exists() else 0
        log.info("  %s: %d bursts recorded", backend_name, lines)
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
        description="Benchmark runner for es-qdrant-log-demo",
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
        "--skip-load",
        action="store_true",
        help="Skip the pre-seed data loading phase (use when databases are already populated)",
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
        seed_secs = compute_seed_duration(config.emitter_dir, config.pre_seed_logs)
        total = seed_secs + 5 + config.steady_state_secs + config.heavy_write_secs + config.recovery_secs
        print("=== DRY RUN ===")
        print(f"Pre-seed:      ~{config.pre_seed_logs:,} logs ({seed_secs}s)")
        print(f"Steady state:  {config.steady_state_secs}s")
        print(f"Heavy write:   {config.heavy_write_secs}s")
        print(f"Recovery:      {config.recovery_secs}s")
        print(f"Total:         ~{total}s ({total // 60}m {total % 60}s)")
        print(f"Backends:      {list(config.backends.keys())}")
        return

    run_benchmark(config, skip_load=args.skip_load)


if __name__ == "__main__":
    main()