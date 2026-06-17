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

# ==========================================
# SECURITY GROUPS (Arquitectura Indirecta)
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
  } # Panel Web UI
  ingress { 
    from_port   = 5672
    to_port     = 5672
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] 
  } # AMQP para Workers y Cliente
  egress { 
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"] 
  }
}

resource "aws_security_group" "worker_sg" {
  name = "indirect_worker_sg"
  
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

resource "aws_security_group" "postgres_sg" {
  name = "indirect_postgres_sg"
  
  ingress { 
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] 
  }
  ingress { 
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.worker_sg.id, aws_security_group.client_sg.id] 
  }
  egress { 
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"] 
  }
}

# ==========================================
# INSTANCIAS EC2
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
    docker run -d --name mi-postgres --restart unless-stopped -e POSTGRES_USER=admin -e POSTGRES_PASSWORD=admin123 -e POSTGRES_DB=ticketdb -p 5432:5432 postgres:latest
    
    while pidof dnf > /dev/null; do sleep 5; done
    dnf update -y && dnf install -y python3 python3-pip git
    
    cd /home/ec2-user 
    git clone https://github.com/Waliguren/practica2_sd.git repo
    mv repo/archivosPostgres ./
    rm -rf repo

    cd /home/ec2-user/archivosPostgres
    
    # Crear entorno virtual e instalar el conector de PostgreSQL
    python3 -m venv venv
    venv/bin/pip install psycopg2-binary
    
    # Ejecutar el script para crear las tablas y datos iniciales
    venv/bin/python init_db.py
  EOF
}

resource "aws_instance" "rabbitmq" {
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = "t3.small"
  key_name               = "clave-rabbitmq-server"
  vpc_security_group_ids = [aws_security_group.rabbitmq_sg.id]
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
    dnf update -y && dnf install docker -y
    systemctl start docker && systemctl enable docker
    
    docker run -d --name mi-rabbit --restart unless-stopped --ulimit nofile=65535:65535 -e RABBITMQ_DEFAULT_USER=admin -e RABBITMQ_DEFAULT_PASS=admin123 -p 5672:5672 -p 15672:15672 rabbitmq:3-management
  EOF
}

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
    python3 -m venv venv && venv/bin/pip install pika redis aiohttp uvloop
    
    echo "export RABBITMQ_HOST=${aws_instance.rabbitmq.private_ip}" >> /home/ec2-user/.bashrc
    echo "ulimit -n 65535" >> /home/ec2-user/.bashrc
  EOF
}

# ==========================================
# AWS LAMBDA (Escalado Dinámico Máx 9)
# ==========================================
data "aws_iam_role" "lab_role" {
  name = "LabRole"
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

# 2. La función Lambda con la limitación de concurrencia
resource "aws_lambda_function" "worker" {
  # Cambiamos esto para que lea directamente el archivo que has creado
  filename                       = "../archivosWorker/dummy_worker.zip" 
  
  # Añadimos esto para que Terraform detecte si haces cambios en el ZIP en el futuro
  source_code_hash               = filebase64sha256("../archivosWorker/dummy_worker.zip") 
  
  function_name                  = "ticket-worker"
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
      RABBITMQ_HOST = aws_instance.rabbitmq.private_ip
    }
  }
}

# 3. Credenciales para que Lambda pueda leer de RabbitMQ (Secrets Manager)
resource "aws_secretsmanager_secret" "rabbitmq_secret" {
  name                    = "rabbitmq_auth_lambda"
  recovery_window_in_days = 0 # Permite borrarlo y recrearlo rápidamente
}

resource "aws_secretsmanager_secret_version" "rabbitmq_secret_val" {
  secret_id     = aws_secretsmanager_secret.rabbitmq_secret.id
  secret_string = jsonencode({
    username = "admin"
    password = "admin123"
  })
}

# 4. El "Gatillo": Conecta RabbitMQ con tu Lambda
resource "aws_lambda_event_source_mapping" "rabbitmq_trigger" {
  function_name = aws_lambda_function.worker.arn
  queues        = ["booking_queue"]
  batch_size    = 100 # Coge 100 mensajes de golpe por cada Lambda

  self_managed_event_source {
    endpoints = {
      URI = "amqp://${aws_instance.rabbitmq.private_ip}:5672"
    }
  }

  source_access_configuration {
    type = "BASIC_AUTH"
    uri  = aws_secretsmanager_secret.rabbitmq_secret.arn
  }

  source_access_configuration {
    type = "VIRTUAL_HOST"
    uri  = "/"
  }
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