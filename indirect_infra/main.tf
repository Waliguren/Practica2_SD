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

#Editate

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
    git clone https://github.com/waliguren/nuevapractica1_sd.git repo
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
    # Límites de Kernel para absorber la cola masiva
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
    
    # Arrancamos RabbitMQ con credenciales personalizadas para evitar el bloqueo remoto de "guest"
    docker run -d --name mi-rabbit --restart unless-stopped --ulimit nofile=65535:65535 -e RABBITMQ_DEFAULT_USER=admin -e RABBITMQ_DEFAULT_PASS=admin123 -p 5672:5672 -p 15672:15672 rabbitmq:3-management
  EOF
}

resource "aws_instance" "worker" {
  count                  = 4
  ami                    = data.aws_ami.amazon_linux.id
  instance_type          = "t3.micro"
  key_name               = "clave-rabbitmq-server"
  vpc_security_group_ids = [aws_security_group.worker_sg.id]
  tags                   = { Name = "Worker-Indirect-${count.index + 1}" }
  
  user_data = <<-EOF
    #!/bin/bash
    # Límites de red para los workers
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
    git clone https://github.com/waliguren/nuevapractica1_sd.git repo
    mv repo/archivosWorker ./
    rm -rf repo
    
    cd archivosWorker
    
    # Añadimos variables de entorno
    echo "export DB_HOST=${aws_instance.postgres.private_ip}" >> /home/ec2-user/.bashrc
    echo "export RABBITMQ_HOST=${aws_instance.rabbitmq.private_ip}" >> /home/ec2-user/.bashrc
    export DB_HOST=${aws_instance.postgres.private_ip}
    export RABBITMQ_HOST=${aws_instance.rabbitmq.private_ip}
    
    # INSTALAMOS psycopg2-binary en vez de redis
    python3 -m venv venv && venv/bin/pip install psycopg2-binary pika
    
    # Arrancar API y configurar reinicios con systemd de forma segura (CAMBIADO A DB_HOST y postgres.private_ip)
    sudo bash -c "echo -e '[Unit]\nDescription=Indirect Worker\nAfter=network.target\n\n[Service]\nType=simple\nUser=ec2-user\nWorkingDirectory=/home/ec2-user/archivosWorker\nEnvironment=DB_HOST=${aws_instance.postgres.private_ip}\nEnvironment=RABBITMQ_HOST=${aws_instance.rabbitmq.private_ip}\nExecStart=/home/ec2-user/archivosWorker/venv/bin/python3 indirect_worker.py\nRestart=always\nRestartSec=5\nLimitNOFILE=65535\n\n[Install]\nWantedBy=multi-user.target' > /etc/systemd/system/indirect-worker.service"
    sudo systemctl daemon-reload
    sudo systemctl enable indirect-worker
    sudo systemctl start indirect-worker
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
    # Límites de red para el cliente
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
    git clone https://github.com/waliguren/nuevapractica1_sd.git repo
    mv repo/archivosCliente ./
    rm -rf repo
    
    cd archivosCliente
    python3 -m venv venv && venv/bin/pip install pika redis aiohttp uvloop
    
    # Guardamos las IPs para el cliente y el ulimit automático
    echo "export RABBITMQ_HOST=${aws_instance.rabbitmq.private_ip}" >> /home/ec2-user/.bashrc
    echo "ulimit -n 65535" >> /home/ec2-user/.bashrc
  EOF
}

output "RABBITMQ_PANEL_WEB" { 
  value = "http://${aws_instance.rabbitmq.public_ip}:15672 (user: admin / pass: admin123)" 
}

output "CLIENTE_SSH" { 
  value = "ssh -i clave-rabbitmq-server.pem ec2-user@${aws_instance.client.public_ip}" 
}