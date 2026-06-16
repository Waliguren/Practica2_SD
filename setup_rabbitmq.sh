sudo dnf update -y
sudo dnf install -y docker
sudo systemctl start docker
sudo systemctl enable docker

sudo docker run -d \
  --name rabbitmq \
  --hostname rabbitmq-server \
  -p 5672:5672 \
  -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=admin \
  -e RABBITMQ_DEFAULT_PASS=admin123 \
  rabbitmq:4-management