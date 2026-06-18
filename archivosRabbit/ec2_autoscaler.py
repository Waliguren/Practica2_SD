import pika
import boto3
import time
import json
import os

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
QUEUE_NAME = "booking_queue"
LAMBDA_NAME = "ticket-worker"

TARGET_RESPONSE_TIME = 5
MIN_CONCURRENCY = 0
MAX_WORKERS = 9
CAPACITY_PER_WORKER = 8.0

lambda_client = boto3.client('lambda', region_name='us-east-1')
cloudwatch_client = boto3.client('cloudwatch', region_name='us-east-1')

# ESTADO LOCAL DEL WORKER POOL
active_workers_timestamps = []
last_backlog = 0
last_time = time.time()

def conectar_rabbitmq():
    credentials = pika.PlainCredentials('admin', 'admin123')
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=5672, credentials=credentials)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    
    # Activamos las colas de prioridad (x-max-priority)
    channel.queue_declare(queue=QUEUE_NAME, durable=True, arguments={'x-max-priority': 10})
    return connection, channel

def get_backlog(channel):
    try:
        q = channel.queue_declare(queue=QUEUE_NAME, durable=True, passive=True)
        return q.method.message_count
    except:
        return 0

import urllib.request
import base64

def get_rabbitmq_ack_rate():
    try:
        url = f"http://{RABBITMQ_HOST}:15672/api/queues/%2F/{QUEUE_NAME}"
        req = urllib.request.Request(url)
        auth = base64.b64encode(b"admin:admin123").decode("utf-8")
        req.add_header("Authorization", f"Basic {auth}")
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
            stats = data.get("message_stats", {})
            ack_details = stats.get("ack_details", {})
            return float(ack_details.get("rate", 0.0))
    except Exception:
        return 0.0

def enviar_metricas_cloudwatch(backlog, arrival_rate, desired_workers, capacity):
    try:
        cloudwatch_client.put_metric_data(
            Namespace='TicketSystem',
            MetricData=[
                {'MetricName': 'QueueBacklog', 'Value': backlog, 'Unit': 'Count'},
                {'MetricName': 'ArrivalRate', 'Value': arrival_rate, 'Unit': 'Count/Second'},
                {'MetricName': 'DesiredConcurrency', 'Value': desired_workers, 'Unit': 'Count'},
                {'MetricName': 'WorkerCapacity', 'Value': capacity, 'Unit': 'Count/Second'}
            ]
        )
    except Exception as e:
        pass

def main():
    global last_backlog, last_time, active_workers_timestamps
    print(f"🚀 Iniciando Autoscaler (Worker Pool + Poison Pill)...")
    
    connection, channel = conectar_rabbitmq()

    while True:
        time.sleep(1) 
        now = time.time()
        
        # Limpiar workers muertos (TTL = 25 segundos)
        active_workers_timestamps = [ts for ts in active_workers_timestamps if now - ts < 25]
        current_active_workers = len(active_workers_timestamps)
        
        current_backlog = get_backlog(channel)
        ack_rate = get_rabbitmq_ack_rate()

        if current_active_workers > 0 and ack_rate > 0:
            real_capacity_per_worker = ack_rate / current_active_workers
            real_capacity_per_worker = max(1.0, min(20.0, real_capacity_per_worker))
        else:
            real_capacity_per_worker = CAPACITY_PER_WORKER

        dt = now - last_time
        delta = current_backlog - last_backlog
        
        arrival_rate = max(0, (delta / dt) + ack_rate)

        desired = ((current_backlog / TARGET_RESPONSE_TIME) + arrival_rate) / real_capacity_per_worker
        workers_needed = max(MIN_CONCURRENCY, min(MAX_WORKERS, int(desired + 0.5)))

        print(f"📊 Backlog: {current_backlog} | Llegadas: {arrival_rate:.1f} | Activas: {current_active_workers} | Deseadas: {workers_needed} | CapacidadReal: {real_capacity_per_worker:.1f}")
        enviar_metricas_cloudwatch(current_backlog, arrival_rate, workers_needed, real_capacity_per_worker)

        if workers_needed > current_active_workers:
            a_invocar = workers_needed - current_active_workers
            print(f"🔼 Naciendo {a_invocar} Lambdas...")
            for _ in range(a_invocar):
                try:
                    lambda_client.invoke(FunctionName=LAMBDA_NAME, InvocationType='Event', Payload=json.dumps({}))
                    active_workers_timestamps.append(now)
                except: pass
                
        elif workers_needed < current_active_workers:
            a_matar = current_active_workers - workers_needed
            print(f"🔽 Matando {a_matar} Lambdas (Poison Pill)...")
            for _ in range(a_matar):
                try:
                    channel.basic_publish(
                        exchange='',
                        routing_key=QUEUE_NAME,
                        body=json.dumps({"comando": "KILL"}),
                        properties=pika.BasicProperties(priority=10) 
                    )
                    if active_workers_timestamps:
                        active_workers_timestamps.pop(0)
                except: pass

        last_backlog = current_backlog
        last_time = now

if __name__ == '__main__':
    main()