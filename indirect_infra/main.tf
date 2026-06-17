provider "aws" {
  region = "us-east-1"
}

data "aws_ami" "amazon_linux" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

data "aws_iam_instance_profile" "lab_profile" {
  name = "LabInstanceProfile"
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_caller_identity" "current" {}

locals {
  sqs_queue_name = "ticket-queue"
  sqs_dlq_name   = "ticket-dlq"
  lambda_name    = "ticket-worker"
  scaling_lambda = "scaling-controller"
  s3_bucket_name = "ticket-logs-${data.aws_caller_identity.current.account_id}"
}

# ==========================================
# SQS DEAD-LETTER QUEUE
# ==========================================
resource "aws_sqs_queue" "dlq" {
  name                      = local.sqs_dlq_name
  message_retention_seconds = 86400
}

# ==========================================
# SQS MAIN QUEUE
# ==========================================
resource "aws_sqs_queue" "main" {
  name                       = local.sqs_queue_name
  visibility_timeout_seconds = 30
  message_retention_seconds  = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 10
  })
}

# ==========================================
# S3 BUCKET (logs)
# ==========================================
resource "aws_s3_bucket" "logs" {
  bucket = local.s3_bucket_name
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ==========================================
# SECURITY GROUPS
# ==========================================
resource "aws_security_group" "client_sg" {
  name = "indirect_client_sg"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rabbitmq_sg" {
  name = "indirect_rabbitmq_sg"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 15672
    to_port     = 15672
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 5672
    to_port     = 5672
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "worker_sg" {
  name = "indirect_worker_sg_v2"

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "postgres_sg" {
  name = "indirect_postgres_sg"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port = 5432
    to_port   = 5432
    protocol  = "tcp"
    security_groups = [
      aws_security_group.worker_sg.id,
      aws_security_group.client_sg.id
    ]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ==========================================
# EC2: POSTGRESQL
# ==========================================
resource "aws_instance" "postgres" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = "t3.micro"
  key_name               = "clave-rabbitmq-server"
  vpc_security_group_ids = [aws_security_group.postgres_sg.id]
  tags                   = { Name = "Postgres-Indirect" }

  user_data = <<-EOF
    #!/bin/bash
    while pidof dnf > /dev/null; do sleep 5; done
    dnf update -y && dnf install docker -y
    systemctl start docker && systemctl enable docker
    docker run -d --name mi-postgres --restart unless-stopped \
      -e POSTGRES_USER=admin -e POSTGRES_PASSWORD=admin123 \
      -e POSTGRES_DB=ticketdb -p 5432:5432 postgres:latest

    while pidof dnf > /dev/null; do sleep 5; done
    dnf update -y && dnf install -y python3 python3-pip git

    cd /home/ec2-user
    git clone https://github.com/Waliguren/practica2_sd.git repo
    mv repo/archivosPostgres ./
    rm -rf repo

    cd /home/ec2-user/archivosPostgres
    python3 -m venv venv
    venv/bin/pip install psycopg2-binary
    venv/bin/python init_db.py
  EOF
}

# ==========================================
# EC2: RABBITMQ + FORWARDER
# ==========================================
resource "aws_instance" "rabbitmq" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = "t3.small"
  key_name               = "clave-rabbitmq-server"
  vpc_security_group_ids = [aws_security_group.rabbitmq_sg.id]
  iam_instance_profile   = data.aws_iam_instance_profile.lab_profile.name
  tags                   = { Name = "RabbitMQ-Server" }

  user_data = <<-EOF
    #!/bin/bash
    echo "fs.file-max = 2097152" >> /etc/sysctl.conf
    echo "net.core.somaxconn = 65535" >> /etc/sysctl.conf
    echo "net.ipv4.tcp_max_syn_backlog = 65535" >> /etc/sysctl.conf
    echo "net.ipv4.ip_local_port_range = 1024 65535" >> /etc/sysctl.conf
    sysctl -p
    echo "* soft nofile 65535" >> /etc/security/limits.conf
    echo "* hard nofile 65535" >> /etc/security/limits.conf

    while pidof dnf > /dev/null; do sleep 5; done
    dnf install -y docker
    systemctl start docker && systemctl enable docker

    docker run -d --name mi-rabbit --restart unless-stopped \
      --ulimit nofile=65535:65535 \
      -e RABBITMQ_DEFAULT_USER=admin -e RABBITMQ_DEFAULT_PASS=admin123 \
      -p 5672:5672 -p 15672:15672 rabbitmq:3-management

    dnf install -y python3 python3-pip git
    pip3 install boto3 pika

    cd /home/ec2-user
    git clone https://github.com/Waliguren/practica2_sd.git repo
    mv repo/archivosRabbit ./
    rm -rf repo

    export SQS_QUEUE_URL=${aws_sqs_queue.main.url}
    echo "export SQS_QUEUE_URL=${aws_sqs_queue.main.url}" >> /home/ec2-user/.bashrc
    nohup python3 /home/ec2-user/archivosRabbit/ec2_autoscaler.py > /home/ec2-user/ec2_autoscaler.log 2>&1 &
  EOF
}

# ==========================================
# EC2: CLIENTE
# ==========================================
resource "aws_instance" "client" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = "t3.micro"
  key_name               = "clave-rabbitmq-server"
  vpc_security_group_ids = [aws_security_group.client_sg.id]
  tags                   = { Name = "Client-Indirect" }

  user_data = <<-EOF
    #!/bin/bash
    echo "fs.file-max = 2097152" >> /etc/sysctl.conf
    echo "net.core.somaxconn = 65535" >> /etc/sysctl.conf
    echo "net.ipv4.tcp_max_syn_backlog = 65535" >> /etc/sysctl.conf
    echo "net.ipv4.ip_local_port_range = 1024 65535" >> /etc/sysctl.conf
    sysctl -p
    echo "* soft nofile 65535" >> /etc/security/limits.conf
    echo "* hard nofile 65535" >> /etc/security/limits.conf

    while pidof dnf > /dev/null; do sleep 5; done
    dnf update -y && dnf install -y python3 python3-pip git

    cd /home/ec2-user
    git clone https://github.com/Waliguren/practica2_sd.git repo
    mv repo/archivosCliente ./
    rm -rf repo

    cd archivosCliente
    python3 -m venv venv
    venv/bin/pip install pika psycopg2-binary

    echo "export RABBITMQ_HOST=${aws_instance.rabbitmq.private_ip}" >> /home/ec2-user/.bashrc
    echo "export DB_HOST=${aws_instance.postgres.private_ip}" >> /home/ec2-user/.bashrc
    echo "ulimit -n 65535" >> /home/ec2-user/.bashrc
  EOF
}

# ==========================================
# LAMBDA: WORKER (procesa compras desde SQS)
# ==========================================
resource "aws_lambda_function" "worker" {
  filename                       = "../archivosWorker/dummy_worker.zip"
  source_code_hash               = filebase64sha256("../archivosWorker/dummy_worker.zip")
  function_name                  = local.lambda_name
  role                           = data.aws_iam_role.lab_role.arn
  handler                        = "indirect_worker.lambda_handler"
  runtime                        = "python3.10"
  timeout                        = 30
  reserved_concurrent_executions = 9

  vpc_config {
    subnet_ids         = data.aws_subnets.default.ids
    security_group_ids = [aws_security_group.worker_sg.id]
  }

  environment {
    variables = {
      DB_HOST       = aws_instance.postgres.private_ip
      SQS_QUEUE_URL = aws_sqs_queue.main.url
    }
  }
}

# ==========================================
# CLOUDWATCH DASHBOARD
# ==========================================
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "TicketSystem-Dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["TicketSystem", "QueueBacklog", { stat = "Average", label = "Backlog RabbitMQ" }]
          ]
          period = 30
          stat   = "Average"
          region = "us-east-1"
          title  = "Queue Backlog"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "ConcurrentExecutions", { label = "Concurrent executions", stat = "Maximum" }],
            ["TicketSystem", "DesiredConcurrency", { label = "Desired concurrency", stat = "Average" }]
          ]
          period = 30
          stat   = "Average"
          region = "us-east-1"
          title  = "Lambda Concurrency"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "Invocations", { stat = "Sum", label = "Invocations" }]
          ]
          period = 30
          stat   = "Sum"
          region = "us-east-1"
          title  = "Lambda Invocations"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/Lambda", "Duration", { stat = "Average", label = "Avg duration (ms)" }]
          ]
          period = 30
          stat   = "Average"
          region = "us-east-1"
          title  = "Lambda Duration"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["TicketSystem", "ArrivalRate", { stat = "Average", label = "Arrival rate (msg/s)" }]
          ]
          period = 30
          stat   = "Average"
          region = "us-east-1"
          title  = "Arrival Rate"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["TicketSystem", "WorkerCapacity", { stat = "Average", label = "Capacity (msg/s)" }]
          ]
          period = 30
          stat   = "Average"
          region = "us-east-1"
          title  = "Worker Capacity"
        }
      }
    ]
  })
}

# ==========================================
# OUTPUTS
# ==========================================
output "RABBITMQ_PANEL_WEB" {
  value = "http://${aws_instance.rabbitmq.public_ip}:15672 (user: admin / pass: admin123)"
}

output "CLIENTE_SSH" {
  value = "ssh -i clave-rabbitmq-server.pem ec2-user@${aws_instance.client.public_ip}"
}

output "POSTGRES_SSH" {
  value = "ssh -i clave-rabbitmq-server.pem ec2-user@${aws_instance.postgres.public_ip}"
}

output "SQS_QUEUE_URL" {
  value = aws_sqs_queue.main.url
}

output "S3_BUCKET" {
  value = aws_s3_bucket.logs.bucket
}

output "DASHBOARD_URL" {
  value = "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=TicketSystem-Dashboard"
}
