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
  -e "ES_JAVA_OPTS=-Xms4g -Xmx4g" \
  --ulimit memlock=-1:-1 \
  docker.elastic.co/elasticsearch/elasticsearch:9.0.0
