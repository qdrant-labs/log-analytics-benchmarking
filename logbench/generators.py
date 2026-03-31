"""Generate qstorm and logstorm config files at runtime."""

import tempfile
from pathlib import Path

import yaml

from .backends.base import Backend
from .config import BenchConfig


def generate_qstorm_config(backend: Backend, config: BenchConfig) -> Path:
    """
    Build a complete qstorm config by merging:
      - backend-specific provider block
      - shared benchmark block (with index_mode injected)
      - shared embedding block
    Writes to a temp file and returns its path.
    """
    qstorm = config.qstorm
    cfg = {
        "provider": backend.qstorm_provider_config(),
        "benchmark": {
            "mode": config.index_mode,
            "warmup_iterations": qstorm["warmup_iterations"],
            "burst_size": qstorm["burst_size"],
            "concurrency": qstorm["concurrency"],
            "timeout_ms": qstorm["timeout_ms"],
            "top_k": qstorm["top_k"],
        },
        "embedding": qstorm["embedding"],
    }

    tmp_dir = Path(tempfile.mkdtemp(prefix="qstorm_"))
    tmp_path = tmp_dir / f"{backend.name}.yaml"
    with open(tmp_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
    return tmp_path


def generate_logstorm_config(backends: list[Backend], config: BenchConfig) -> Path:
    """
    Build a complete logstorm config by reading the base template
    and appending dynamically generated sinks for active backends.
    Writes to a temp file and returns its path.
    """
    base_path = Path(config.logstorm_base)
    with open(base_path) as f:
        base = yaml.safe_load(f)

    # build sinks from active backends
    sinks = [b.logstorm_sink_config(config.index_mode) for b in backends]
    sinks.append({"type": "dashboard", "port": 3000})
    base["sinks"] = sinks

    tmp_dir = Path(tempfile.mkdtemp(prefix="logstorm_"))
    tmp_path = tmp_dir / "logstorm_config.yaml"
    with open(tmp_path, "w") as f:
        yaml.dump(base, f, default_flow_style=False)
    return tmp_path
