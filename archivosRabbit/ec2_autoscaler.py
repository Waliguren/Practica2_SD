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

TARGET_RESPONSE_TIME = 5
MIN_CONCURRENCY = 1
MAX_WORKERS = 9 
CAPACITY_PER_WORKER = 8.0

# Clientes de AWS
lambda_client = boto3.client('lambda', region_name='us-east-1')
cloudwatch_client = boto3.client('cloudwatch', region_name='us-east-1') # NUEVO CLIENTE

last_backlog = 0
last_time = time.time()
processed_messages = 0

def conectar_rabbitmq():
    credentials = pika.PlainCredentials('admin', 'admin123')
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=5672, credentials=credentials)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True)
    return connection, channel

def get_backlog(channel):
    try:
        q = channel.queue_declare(queue=QUEUE_NAME, durable=True, passive=True)
        return q.method.message_count
    except Exception as e:
        print(f"Error leyendo cola RabbitMQ: {e}")
        return 0

def enviar_metricas_cloudwatch(backlog, arrival_rate, desired_workers, capacity):
    """Envía los cálculos en tiempo real al Dashboard de AWS"""
    try:
        cloudwatch_client.put_metric_data(
            Namespace='TicketSystem',
            MetricData=[
                {'MetricName': 'QueueBacklog', 'Value': backlog, 'Unit': 'Count'},
                {'MetricName': 'ArrivalRate', 'Value': arrival_rate, 'Unit': 'Count/Second'},
                {'MetricName': 'DesiredConcurrency', 'Value': desired_workers, 'Unit': 'Count'},
                {'MetricName': 'WorkerCapacity', 'Value': capacity, 'Unit': 'Count/Second'} # <-- Añadido
            ]
        )
    except Exception as e:
        print(f"Error enviando a CloudWatch: {e}")

def disparar_lambda(payload):
    try:
        lambda_client.invoke(
            FunctionName=LAMBDA_NAME,
            InvocationType='Event', 
            Payload=json.dumps(payload)
        )
    except Exception as e:
        print(f"Error invocando Lambda: {e}")

def main():
    global last_backlog, last_time, processed_messages
    print(f"🚀 Iniciando Autoscaler con Monitorización CloudWatch...")
    
    connection, channel = conectar_rabbitmq()
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    while True:
        time.sleep(1) 
        now = time.time()
        
        current_backlog = get_backlog(channel)

        dt = now - last_time
        delta = current_backlog - last_backlog
        estimated_arrivals = max(0, delta + processed_messages)
        arrival_rate = estimated_arrivals / dt if dt > 0 else 0

        desired = ((current_backlog / TARGET_RESPONSE_TIME) + arrival_rate) / CAPACITY_PER_WORKER
        workers = max(MIN_CONCURRENCY, min(MAX_WORKERS, int(desired + 0.5)))

        print(f"📊 Backlog: {current_backlog} | Llegadas: {arrival_rate:.1f} msg/s | Lambdas: {workers}")

        # ENVIAMOS LAS MÉTRICAS A CLOUDWATCH
        enviar_metricas_cloudwatch(current_backlog, arrival_rate, workers, CAPACITY_PER_WORKER)

        last_backlog = current_backlog
        last_time = now
        processed_messages = 0

        for _ in range(workers):
            if current_backlog <= 0:
                break 

            batch = []
            for _ in range(10):
                method_frame, properties, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=False)
                if method_frame:
                    batch.append((method_frame, body))
                else:
                    break
            
            if batch:
                records = [{'body': body.decode('utf-8'), 'messageId': str(method_frame.delivery_tag)} for method_frame, body in batch]
                payload = {'Records': records}
                
                executor.submit(disparar_lambda, payload)
                
                for method_frame, _ in batch:
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                
                processed_messages += len(batch)
                current_backlog -= len(batch)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nApagando Autoscaler...")