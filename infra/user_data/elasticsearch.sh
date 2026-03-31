#!/bin/bash
set -euo pipefail

# Elasticsearch needs vm.max_map_count >= 262144
sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" >> /etc/sysctl.conf

dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

docker run -d --name elasticsearch --restart always \
  -p 9200:9200 \
  -e discovery.type=single-node \
  -e ELASTIC_PASSWORD=${es_password} \
  -e xpack.security.enabled=true \
  -e xpack.security.http.ssl.enabled=false \
  -e xpack.license.self_generated.type=trial \
  -e xpack.ml.use_auto_machine_memory_percent=true \
  -e "ES_JAVA_OPTS=-Xms2g -Xmx2g" \
  --ulimit memlock=-1:-1 \
  docker.elastic.co/elasticsearch/elasticsearch:9.0.0

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
