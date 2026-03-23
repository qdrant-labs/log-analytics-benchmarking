#!/bin/bash
set -euo pipefail

dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

docker run -d --name postgres --restart always \
  -p 5432:5432 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=${pg_password} \
  -e POSTGRES_DB=logs \
  pgvector/pgvector:pg17

# Wait for postgres to be ready, then enable pgvector extension
for i in $(seq 1 30); do
  docker exec postgres pg_isready -U postgres && break
  sleep 2
done

docker exec postgres psql -U postgres -d logs -c "CREATE EXTENSION IF NOT EXISTS vector;"
