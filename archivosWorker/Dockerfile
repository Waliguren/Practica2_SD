# Usamos una imagen oficial de Python ligera
FROM python:3.10-slim

# Creamos la carpeta de trabajo
WORKDIR /app

# Instalamos las dependencias del sistema necesarias para compilar psycopg2
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Instalamos las librerías de Python exactas que necesita tu worker
RUN pip install --no-cache-dir pika psycopg2-binary

# Copiamos nuestro script del worker al contenedor
COPY indirect_worker.py .

# Comando que se ejecutará cuando Fargate encienda el contenedor
CMD ["python", "indirect_worker.py"]