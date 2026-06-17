import pika
import boto3
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor

# ==========================================
# CONFIGURACIÓN
# ==========================================
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
QUEUE_NAME = "booking_queue"
LAMBDA_NAME = "ticket-worker"

# Variables de tu fórmula de escalado
TARGET_RESPONSE_TIME = 5
MIN_CONCURRENCY = 1
MAX_WORKERS = 9 # Tu límite estricto de Lambdas simultáneas
CAPACITY_PER_WORKER = 8.0

# Cliente de AWS para disparar las Lambdas
lambda_client = boto3.client('lambda', region_name='us-east-1')

# Variables de estado para calcular el Arrival Rate
last_backlog = 0
last_time = time.time()
processed_messages = 0

def conectar_rabbitmq():
    """Establece la conexión con RabbitMQ"""
    credentials = pika.PlainCredentials('admin', 'admin123')
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=5672, credentials=credentials)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    return connection, channel

def get_backlog(channel):
    """Obtiene el número exacto de mensajes esperando en la cola de RabbitMQ"""
    try:
        q = channel.queue_declare(queue=QUEUE_NAME, durable=True, passive=True)
        return q.method.message_count
    except Exception as e:
        print(f"Error leyendo cola RabbitMQ: {e}")
        return 0

def disparar_lambda(payload):
    """Función que ejecuta el hilo en paralelo para invocar la Lambda"""
    try:
        lambda_client.invoke(
            FunctionName=LAMBDA_NAME,
            InvocationType='Event', # Asíncrono: se lo entregamos a AWS y no esperamos respuesta
            Payload=json.dumps(payload)
        )
    except Exception as e:
        print(f"Error invocando Lambda: {e}")

def main():
    global last_backlog, last_time, processed_messages
    print(f"🚀 Iniciando Autoscaler (RabbitMQ -> Lambda: {LAMBDA_NAME})...")
    
    connection, channel = conectar_rabbitmq()
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    while True:
        time.sleep(1) # Evaluar la carga cada segundo
        now = time.time()
        
        # 1. Medir la cola
        current_backlog = get_backlog(channel)

        # 2. Calcular Arrival Rate (Velocidad de llegada)
        dt = now - last_time
        delta = current_backlog - last_backlog
        estimated_arrivals = max(0, delta + processed_messages)
        arrival_rate = estimated_arrivals / dt if dt > 0 else 0

        # 3. FÓRMULA DE ESCALADO MATEMÁTICO
        desired = ((current_backlog / TARGET_RESPONSE_TIME) + arrival_rate) / CAPACITY_PER_WORKER
        workers = max(MIN_CONCURRENCY, min(MAX_WORKERS, int(desired + 0.5)))

        print(f"📊 Backlog: {current_backlog} | Llegadas: {arrival_rate:.1f} msg/s | Lambdas a disparar: {workers}")

        # Reiniciamos métricas para el siguiente ciclo
        last_backlog = current_backlog
        last_time = now
        processed_messages = 0

        # 4. Extraer mensajes y disparar Lambdas
        for _ in range(workers):
            if current_backlog <= 0:
                break # Si ya no hay mensajes, no disparamos lambdas vacías

            batch = []
            # Sacamos un máximo de 10 mensajes de RabbitMQ para esta Lambda
            for _ in range(10):
                method_frame, properties, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=False)
                if method_frame:
                    batch.append((method_frame, body))
                else:
                    break
            
            if batch:
                # Simulamos el formato JSON ('Records') que la Lambda esperaba recibir de SQS
                records = [{'body': body.decode('utf-8'), 'messageId': str(method_frame.delivery_tag)} for method_frame, body in batch]
                payload = {'Records': records}
                
                # Disparamos la Lambda en un hilo paralelo
                executor.submit(disparar_lambda, payload)
                
                # Confirmamos a RabbitMQ que ya hemos cogido los mensajes
                for method_frame, _ in batch:
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                
                processed_messages += len(batch)
                current_backlog -= len(batch)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nApagando Autoscaler...")