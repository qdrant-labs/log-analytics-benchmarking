from .base import Backend
from .qdrant import QdrantBackend
from .elasticsearch import ElasticsearchBackend
from .opensearch import OpenSearchBackend
from .pgvector import PgvectorBackend

REGISTRY: dict[str, type[Backend]] = {
    "qdrant": QdrantBackend,
    "elasticsearch": ElasticsearchBackend,
    "opensearch": OpenSearchBackend,
    "pgvector": PgvectorBackend,
}


def get_backend(name: str, env: dict[str, str]) -> Backend:
    """Instantiate a single backend by name."""
    return REGISTRY[name].from_env(env)


def get_backends(names: list[str], env: dict[str, str]) -> list[Backend]:
    """Instantiate multiple backends by name."""
    return [get_backend(n, env) for n in names]


__all__ = [
    "Backend",
    "REGISTRY",
    "get_backend",
    "get_backends",
    "QdrantBackend",
    "ElasticsearchBackend",
    "OpenSearchBackend",
    "PgvectorBackend",
]
