output "qdrant_public_ip" {
  description = "Qdrant instance public IP"
  value       = length(aws_instance.qdrant) > 0 ? aws_instance.qdrant[0].public_ip : null
}

output "qdrant_grpc_url" {
  description = "Qdrant gRPC URL (for qstorm)"
  value       = length(aws_instance.qdrant) > 0 ? "http://${aws_instance.qdrant[0].public_ip}:6334" : null
}

output "qdrant_rest_url" {
  description = "Qdrant REST URL (for logstorm)"
  value       = length(aws_instance.qdrant) > 0 ? "http://${aws_instance.qdrant[0].public_ip}:6333" : null
}

output "elasticsearch_public_ip" {
  description = "Elasticsearch instance public IP"
  value       = length(aws_instance.elasticsearch) > 0 ? aws_instance.elasticsearch[0].public_ip : null
}

output "elasticsearch_url" {
  description = "Elasticsearch URL"
  value       = length(aws_instance.elasticsearch) > 0 ? "http://${aws_instance.elasticsearch[0].public_ip}:9200" : null
}

output "pgvector_public_ip" {
  description = "pgvector instance public IP"
  value       = length(aws_instance.pgvector) > 0 ? aws_instance.pgvector[0].public_ip : null
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.bench.id
}

output "my_ip" {
  description = "Your detected public IP"
  value       = trimspace(data.http.my_ip.response_body)
}
