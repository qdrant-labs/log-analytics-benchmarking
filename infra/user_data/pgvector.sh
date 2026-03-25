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

# CloudWatch agent for memory metrics
dnf install -y amazon-cloudwatch-agent
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CWEOF'
{
  "metrics": {
    "append_dimensions": {
      "InstanceId": "$${aws:InstanceId}"
    },
    "metrics_collected": {
      "mem": {
        "measurement": ["mem_used_percent"],
        "metrics_collection_interval": 10
      }
    }
  }
}
CWEOF
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s
