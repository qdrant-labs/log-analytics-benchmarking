import base64
import sys
import time
import urllib.request
from typing import Any
from urllib.parse import urlparse

import polars as pl
from tqdm import tqdm

from .base import Backend


class OpenSearchBackend(Backend):
    name = "opensearch"
    env_prefix = "OPENSEARCH"

    def __init__(self, env: dict[str, str]):
        super().__init__(env)
        self._url = env.get("OPENSEARCH_URL", "http://localhost:9200")
        self._user = env.get("OPENSEARCH_USER", "admin")
        self._password = env.get("OPENSEARCH_PASSWORD", "Changeme1!")

    async def health_check(self) -> bool:
        try:
            url = f"{self._url.rstrip('/')}/_cluster/health"
            req = urllib.request.Request(url)
            creds = base64.b64encode(f"{self._user}:{self._password}".encode()).decode()
            req.add_header("Authorization", f"Basic {creds}")
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.status == 200
        except Exception:
            return False

    async def seed(self, df: pl.DataFrame, index_mode: str, batch_size: int = 1000) -> None:
        from opensearchpy import AsyncOpenSearch, helpers

        parsed = urlparse(self._url)
        client = AsyncOpenSearch(
            hosts=[{"host": parsed.hostname, "port": parsed.port or 9200}],
            http_auth=(self._user, self._password),
            use_ssl=parsed.scheme == "https",
            verify_certs=False,
        )
        index = "logs"

        properties: dict[str, Any] = {
            "timestamp": {"type": "date"},
            "service": {"type": "keyword"},
            "level": {"type": "keyword"},
            "message": {"type": "text"},
        }
        settings: dict[str, Any] = {}
        if index_mode in ("vector", "hybrid"):
            dim = len(df["embedding"][0])
            properties["dense"] = {
                "type": "knn_vector",
                "dimension": dim,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                },
            }
            settings["index"] = {"knn": True}

        if await client.indices.exists(index=index):
            await client.indices.delete(index=index)

        body: dict[str, Any] = {"mappings": {"properties": properties}}
        if settings:
            body["settings"] = settings
        await client.indices.create(index=index, body=body)

        total = len(df)
        print(f"Seeding OpenSearch ({self._url}) with {total:,} records (mode={index_mode})...")
        t0 = time.time()

        def gen_actions():
            for row in df.iter_rows(named=True):
                doc = {
                    "_index": index,
                    "_id": row["id"],
                    "timestamp": row["timestamp"].isoformat(),
                    "service": row["service"],
                    "level": row["level"],
                    "message": row["message"],
                }
                if index_mode in ("vector", "hybrid"):
                    doc["dense"] = row["embedding"]
                yield doc

        successes, errors = 0, []
        async for ok, item in helpers.async_streaming_bulk(
            client, gen_actions(), chunk_size=batch_size, raise_on_error=False
        ):
            if ok:
                successes += 1
            else:
                errors.append(item)

        elapsed = time.time() - t0
        print(f"  OpenSearch: {successes:,} records in {elapsed:.1f}s ({successes/elapsed:.0f} records/s)")
        if errors:
            print(f"  {len(errors)} errors (first: {errors[0]})", file=sys.stderr)
        await client.close()

    def qstorm_provider_config(self) -> dict[str, Any]:
        return {
            "name": "opensearch",
            "type": "opensearch",
            "url": self._url,
            "index_name": "logs",
            "credentials": {
                "type": "basic",
                "username": self._user,
                "password": self._password,
            },
            "vector_field": "dense",
            "text_field": "message",
        }

    def logstorm_sink_config(self, index_mode: str) -> dict[str, Any]:
        return {
            "type": "opensearch",
            "url": "${OPENSEARCH_URL}",
            "user": "${OPENSEARCH_USER}",
            "password": "${OPENSEARCH_PASSWORD}",
            "index_name": "logs",
            "index_mode": index_mode,
            "skip_cert": True,
        }
