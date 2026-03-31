import time
import urllib.request

from typing import Any

import polars as pl
from tqdm import tqdm

from .base import Backend


class QdrantBackend(Backend):
    name = "qdrant"
    env_prefix = "QDRANT"

    def __init__(self, env: dict[str, str]):
        super().__init__(env)
        raw_url = env.get("QDRANT_URL", "http://localhost:6333")
        # REST port for Python client + health checks
        self._rest_url = raw_url.replace(":6334", ":6333")
        # gRPC port for qstorm
        self._grpc_url = raw_url.replace(":6333", ":6334")
        self._api_key = env.get("QDRANT_API_KEY")

    async def health_check(self) -> bool:
        try:
            url = f"{self._rest_url.rstrip('/')}/healthz"
            resp = urllib.request.urlopen(url, timeout=10)
            return resp.status == 200
        except Exception:
            return False

    async def seed(self, df: pl.DataFrame, index_mode: str, batch_size: int = 1000) -> None:
        from qdrant_client import AsyncQdrantClient, models

        client = AsyncQdrantClient(url=self._rest_url, api_key=self._api_key)
        collection = "logs"

        vectors_config = {}
        sparse_vectors_config = {}

        if index_mode in ("vector", "hybrid"):
            dim = len(df["embedding"][0])
            vectors_config["dense"] = models.VectorParams(
                size=dim, distance=models.Distance.COSINE,
            )
        if index_mode in ("keyword", "hybrid"):
            sparse_vectors_config["bm25"] = models.SparseVectorParams(
                modifier=models.Modifier.IDF,
            )

        if await client.collection_exists(collection):
            await client.delete_collection(collection)

        await client.create_collection(
            collection_name=collection,
            vectors_config=vectors_config or None,
            sparse_vectors_config=sparse_vectors_config or None,
        )

        await client.create_payload_index(collection, "service", models.PayloadSchemaType.KEYWORD)
        await client.create_payload_index(collection, "level", models.PayloadSchemaType.KEYWORD)

        total = len(df)
        print(f"Seeding Qdrant ({self._rest_url}) with {total:,} records (mode={index_mode})...")
        t0 = time.time()

        for i in tqdm(range(0, total, batch_size), desc="Qdrant"):
            batch = df.slice(i, batch_size)
            points = []
            for row in batch.iter_rows(named=True):
                vectors = {}
                if index_mode in ("vector", "hybrid"):
                    vectors["dense"] = row["embedding"]

                points.append(models.PointStruct(
                    id=row["id"],
                    vector=vectors if vectors else {},
                    payload={
                        "service": row["service"],
                        "level": row["level"],
                        "message": row["message"],
                        "timestamp": row["timestamp"].isoformat(),
                    },
                ))
            await client.upsert(collection_name=collection, points=points)

        elapsed = time.time() - t0
        print(f"  Qdrant: {total:,} records in {elapsed:.1f}s ({total/elapsed:.0f} records/s)")
        await client.close()

    def qstorm_provider_config(self) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "name": "qdrant",
            "type": "qdrant",
            "url": self._grpc_url,
            "collection_name": "logs",
            "vector_field": "dense",
            "text_field": "bm25",
        }
        if self._api_key:
            cfg["api_key"] = self._api_key
        return cfg

    def logstorm_sink_config(self, index_mode: str) -> dict[str, Any]:
        return {
            "type": "qdrant",
            "url": "${QDRANT_URL}",
            "api_key": "${QDRANT_API_KEY}",
            "collection_name": "logs",
            "index_mode": index_mode,
        }
