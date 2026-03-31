import socket
import time
from typing import Any

import polars as pl
from tqdm import tqdm

from .base import Backend


class PgvectorBackend(Backend):
    name = "pgvector"
    env_prefix = "PGVECTOR"

    def __init__(self, env: dict[str, str]):
        super().__init__(env)
        self._host = env.get("PGVECTOR_HOST", "localhost")
        self._user = env.get("PGVECTOR_USER", "postgres")
        self._password = env.get("PGVECTOR_PASSWORD", "changeme")

    async def health_check(self) -> bool:
        try:
            s = socket.create_connection((self._host, 5432), timeout=5)
            s.close()
            return True
        except Exception:
            return False

    async def seed(self, df: pl.DataFrame, index_mode: str, batch_size: int = 1000) -> None:
        import numpy as np
        from psycopg import AsyncConnection
        from pgvector.psycopg import register_vector_async

        conn = await AsyncConnection.connect(
            host=self._host, user=self._user, password=self._password,
            dbname="logs", port=5432, autocommit=False,
        )
        await register_vector_async(conn)
        cur = conn.cursor()

        dim = len(df["embedding"][0])
        await cur.execute(f"""
            CREATE TABLE IF NOT EXISTS logs (
                id TEXT PRIMARY KEY,
                timestamp TIMESTAMPTZ,
                service TEXT,
                level TEXT,
                message TEXT,
                embedding vector({dim})
            )
        """)
        await cur.execute("TRUNCATE logs")
        await conn.commit()

        total = len(df)
        print(f"Seeding pgvector ({self._host}) with {total:,} records...")
        t0 = time.time()

        for i in tqdm(range(0, total, batch_size), desc="pgvector"):
            batch = df.slice(i, batch_size)
            async with cur.copy("COPY logs (id, timestamp, service, level, message, embedding) FROM STDIN") as copy:
                for row in batch.iter_rows(named=True):
                    emb = np.array(row["embedding"], dtype=np.float32)
                    await copy.write_row((
                        row["id"],
                        row["timestamp"].isoformat(),
                        row["service"],
                        row["level"],
                        row["message"],
                        emb,
                    ))
            await conn.commit()

        elapsed = time.time() - t0
        print(f"  pgvector: {total:,} records in {elapsed:.1f}s ({total/elapsed:.0f} records/s)")
        await cur.close()
        await conn.close()

    def qstorm_provider_config(self) -> dict[str, Any]:
        return {
            "name": "pgvector",
            "type": "pgvector",
            "url": f"postgresql://{self._user}:{self._password}@{self._host}:5432/logs",
            "table_name": "logs",
            "vector_field": "embedding",
            "text_field": "message",
        }

    def logstorm_sink_config(self, index_mode: str) -> dict[str, Any]:
        return {
            "type": "pgvector",
            "host": "${PGVECTOR_HOST}",
            "user": "${PGVECTOR_USER}",
            "password": "${PGVECTOR_PASSWORD}",
            "table_name": "logs",
        }
