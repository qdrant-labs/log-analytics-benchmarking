import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def load_env(env_path: str = ".env") -> dict[str, str]:
    """Load environment variables, overlaying values from a .env file."""
    env = os.environ.copy()
    path = Path(env_path)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


@dataclass
class BenchConfig:
    index_mode: str = "hybrid"
    pre_seed_logs: int = 100_000
    steady_state_secs: int = 120
    heavy_write_secs: int = 180
    recovery_secs: int = 180
    results_dir: str = "./results"
    logstorm_base: str = "./logstorm_base.yaml"
    qstorm: dict = field(default_factory=lambda: {
        "queries_file": "./qstorm_configs/queries.yaml",
        "warmup_iterations": 10,
        "burst_size": 100,
        "concurrency": 10,
        "timeout_ms": 5000,
        "top_k": 10,
        "embedding": {
            "model": "openai/text-embedding-3-small",
            "dimensions": 1536,
        },
    })
    backends: list[str] = field(default_factory=lambda: [
        "qdrant", "elasticsearch", "opensearch",
    ])

    @classmethod
    def from_yaml(cls, path: str) -> "BenchConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
