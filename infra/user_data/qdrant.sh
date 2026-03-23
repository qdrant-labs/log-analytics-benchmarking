#!/bin/bash
set -euo pipefail

dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

docker run -d --name qdrant --restart always \
  -p 6333:6333 -p 6334:6334 \
  -e QDRANT__SERVICE__GRPC_PORT=6334 \
  qdrant/qdrant:latest
