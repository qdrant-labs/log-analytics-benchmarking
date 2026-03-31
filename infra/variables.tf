variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type for each backend (2 vCPU / 8 GiB)"
  type        = string
  default     = "m6i.large"
}

variable "backends" {
  description = "Which database backends to launch"
  type        = list(string)
  default     = ["qdrant", "elasticsearch", "opensearch", "pgvector"]

  validation {
    condition     = alltrue([for b in var.backends : contains(["qdrant", "elasticsearch", "opensearch", "pgvector"], b)])
    error_message = "Valid backends are: qdrant, elasticsearch, opensearch, pgvector."
  }
}

variable "es_password" {
  description = "Elasticsearch password"
  type        = string
  default     = "changeme"
  sensitive   = true
}

variable "pg_password" {
  description = "PostgreSQL password"
  type        = string
  default     = "changeme"
  sensitive   = true
}

variable "opensearch_password" {
  description = "OpenSearch admin password"
  type        = string
  default     = "Changeme1!"
  sensitive   = true
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access (optional, leave empty to disable SSH)"
  type        = string
  default     = ""
}

variable "enable_cloudwatch" {
  description = "Create IAM role for CloudWatch agent (requires iam:CreateRole permission)"
  type        = bool
  default     = false
}

variable "volume_size" {
  description = "Root EBS volume size in GiB"
  type        = number
  default     = 30
}
