import pika
import time
import uuid
import os
import sys
import json
from datetime import datetime, timezone

RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = 'booking_queue'

class BenchmarkClient:
    def __init__(self):
        print(f"Conectando a RabbitMQ en {RABBITMQ_HOST}...")
        credentials = pika.PlainCredentials('admin', 'admin123')
        parameters = pika.ConnectionParameters(
            host=RABBITMQ_HOST, port=5672, credentials=credentials
        )
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()
        self.channel.confirm_delivery()

        self.channel.queue_declare(queue=QUEUE_NAME, durable=True, arguments={'x-max-priority': 10})

    def _tasa_zt(self, t):
        if t < 30:
            return 50
        elif t < 60:
            return int(50 + ((t - 30) / 30) * 950)
        elif t < 90:
            return 1000
        elif t < 150:
            return 500
        else:
            return max(50, int(500 - ((t - 150) / 30) * 450))

    def enviar(self, payloads, duracion=180):
        total = len(payloads)
        experiment_id = payloads[0].get('experiment_id', 'unknown') if payloads else 'unknown'
        print(f"\nEnviando {total} mensajes con carga Z(t) ({duracion}s)...")
        inicio = time.time()
        fallos = 0
        idx = 0

        while idx < total:
            elapsed = time.time() - inicio
            if elapsed >= duracion:
                break

            payload = payloads[idx]
            try:
                self.channel.basic_publish(
                    exchange='',
                    routing_key=QUEUE_NAME,
                    mandatory=True,
                    properties=pika.BasicProperties(delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE),
                    body=json.dumps(payload)
                )
            except pika.exceptions.UnroutableError:
                fallos += 1

            idx += 1

            if idx % 1000 == 0:
                rate = idx / (time.time() - inicio) if (time.time() - inicio) > 0 else 0
                print(f"  Enviados {idx}/{total} ({rate:.0f} msg/s)")

            time.sleep(1.0 / max(self._tasa_zt(elapsed), 1))

        total_time = time.time() - inicio
        entregados = idx - fallos
        print(f"  Entregados: {entregados}/{total} (fallos: {fallos})")
        print(f"  Tiempo: {total_time:.2f}s")
        print(f"  Throughput: {entregados/total_time:.1f} msg/s")
        self.connection.close()


def main(archivo):
    experiment_id = str(uuid.uuid4())[:8]
    print(f"Cargando benchmark: {archivo} (experiment_id={experiment_id})...")
    payloads = []

    with open(archivo, 'r') as f:
        for line in f:
            line = line.strip()
            if not line.startswith("BUY"):
                continue
            parts = line.split()
            sent_ts = datetime.now(timezone.utc).isoformat()
            if len(parts) == 3:
                payloads.append({
                    "client_id": parts[1],
                    "request_id": f"{experiment_id}_{parts[2]}",
                    "experiment_id": experiment_id,
                    "sent_timestamp": sent_ts
                })
            elif len(parts) == 4:
                payloads.append({
                    "client_id": parts[1],
                    "seat_id": parts[2],
                    "request_id": f"{experiment_id}_{parts[3]}",
                    "experiment_id": experiment_id,
                    "sent_timestamp": sent_ts
                })

    if not payloads:
        print("Error: no se encontraron peticiones BUY en el archivo")
        sys.exit(1)

    print(f"Preparados {len(payloads)} payloads ({'numerada' if 'seat_id' in payloads[0] else 'no numerada'})")

    cliente = BenchmarkClient()
    cliente.enviar(payloads)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 indirect_client.py <benchmark_file>")
        sys.exit(1)
    main(sys.argv[1])
