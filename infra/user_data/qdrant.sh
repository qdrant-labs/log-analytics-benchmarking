#!/bin/bash
set -euo pipefail

dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

docker run -d --name qdrant --restart always \
  -p 6333:6333 -p 6334:6334 \
  -e QDRANT__SERVICE__GRPC_PORT=6334 \
  qdrant/qdrant:latest

# CloudWatch agent for memory metrics
dnf install -y amazon-cloudwatch-agent
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CWEOF'
{
  "metrics": {
    "append_dimensions": {
      "InstanceId": "${aws:InstanceId}"
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
