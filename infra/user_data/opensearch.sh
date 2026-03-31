#!/bin/bash
set -euo pipefail

dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

docker run -d --name opensearch --restart always \
  -p 9200:9200 -p 9600:9600 \
  -e discovery.type=single-node \
  -e OPENSEARCH_INITIAL_ADMIN_PASSWORD=${opensearch_password} \
  -e DISABLE_SECURITY_PLUGIN=true \
  -e "OPENSEARCH_JAVA_OPTS=-Xms4g -Xmx4g" \
  opensearchproject/opensearch:latest

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
