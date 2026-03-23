terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# grab my current ip address at apply time
# so we can update sucurity group rules accordingly
data "http" "my_ip" {
  url = "https://checkip.amazonaws.com"
}

locals {
  my_ip = "${trimspace(data.http.my_ip.response_body)}/32"
  tags  = { Project = "log-analytics-benchmarking" }
  has   = { for b in var.backends : b => true }
}

# latest amazon linux 2023 ami
data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ---------------------------------------------------------------------------
# Networking — self-contained VPC so we don't depend on a default VPC
# ---------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "bench" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "bench-vpc" })
}

resource "aws_subnet" "bench" {
  vpc_id                  = aws_vpc.bench.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "bench-subnet" })
}

resource "aws_internet_gateway" "bench" {
  vpc_id = aws_vpc.bench.id
  tags   = merge(local.tags, { Name = "bench-igw" })
}

resource "aws_route_table" "bench" {
  vpc_id = aws_vpc.bench.id
  tags   = merge(local.tags, { Name = "bench-rt" })

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.bench.id
  }
}

resource "aws_route_table_association" "bench" {
  subnet_id      = aws_subnet.bench.id
  route_table_id = aws_route_table.bench.id
}

# ---------------------------------------------------------------------------
# Security group — inbound from caller's IP only
# ---------------------------------------------------------------------------

resource "aws_security_group" "bench" {
  name_prefix = "bench-"
  description = "Allow benchmark traffic from operator IP"
  vpc_id      = aws_vpc.bench.id
  tags        = local.tags

  # open ssh for debugging
  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [local.my_ip]
  }

  # elasticsearch
  ingress {
    description = "Elasticsearch"
    from_port   = 9200
    to_port     = 9200
    protocol    = "tcp"
    cidr_blocks = [local.my_ip]
  }

  # kibana
  ingress {
    description = "Kibana"
    from_port   = 5601
    to_port     = 5601
    protocol    = "tcp"
    cidr_blocks = [local.my_ip]
  }

  # qdrant
  ingress {
    description = "Qdrant REST"
    from_port   = 6333
    to_port     = 6333
    protocol    = "tcp"
    cidr_blocks = [local.my_ip]
  }

  # qdrant
  ingress {
    description = "Qdrant gRPC"
    from_port   = 6334
    to_port     = 6334
    protocol    = "tcp"
    cidr_blocks = [local.my_ip]
  }

  # pg
  ingress {
    description = "PostgreSQL"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [local.my_ip]
  }

  # allow outbound access so instances can download packages, etc
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ec2 instances
resource "aws_instance" "qdrant" {
  count = lookup(local.has, "qdrant", false) ? 1 : 0

  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.bench.id
  vpc_security_group_ids      = [aws_security_group.bench.id]
  associate_public_ip_address = true
  key_name                    = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = file("${path.module}/user_data/qdrant.sh")

  tags = merge(local.tags, { Name = "bench-qdrant" })
}

resource "aws_instance" "elasticsearch" {
  count = lookup(local.has, "elasticsearch", false) ? 1 : 0

  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.bench.id
  vpc_security_group_ids      = [aws_security_group.bench.id]
  associate_public_ip_address = true
  key_name                    = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = templatefile("${path.module}/user_data/elasticsearch.sh", {
    es_password = var.es_password
  })

  tags = merge(local.tags, { Name = "bench-elasticsearch" })
}

resource "aws_instance" "pgvector" {
  count = lookup(local.has, "pgvector", false) ? 1 : 0

  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.bench.id
  vpc_security_group_ids      = [aws_security_group.bench.id]
  associate_public_ip_address = true
  key_name                    = var.key_pair_name != "" ? var.key_pair_name : null

  user_data = templatefile("${path.module}/user_data/pgvector.sh", {
    pg_password = var.pg_password
  })

  tags = merge(local.tags, { Name = "bench-pgvector" })
}
