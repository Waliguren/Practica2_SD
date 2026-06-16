import pika
import time
import uuid
import os
import sys
import json

# Variables de entorno inyectadas por Terraform
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = 'booking_queue'

class RpcClient(object):
    def __init__(self):
        print(f"Conectando a RabbitMQ en {RABBITMQ_HOST}...")
        credentials = pika.PlainCredentials('admin', 'admin123')
        parameters = pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            port=5672,
            virtual_host='/',
            credentials=credentials
        )
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        # 1. En la arquitectura mostrada en common.py, el cliente NO declara la cola principal.
        # Asumimos que el worker (o la infraestructura) ya la ha creado.

        # 2. Creamos el buzón privado del cliente (cola exclusiva y temporal)
        result = self.channel.queue_declare(queue='', exclusive=True)
        self.callback_queue = result.method.queue

        # 3. Le decimos a RabbitMQ que nos avise aquí cuando lleguen respuestas
        self.channel.basic_consume(
            queue=self.callback_queue,
            on_message_callback=self.on_response,
            auto_ack=True
        )

        self.respuestas_recibidas = 0
        self.total_peticiones = 0
        self.stats = {200: 0, 409: 0, "errors": 0}

    def on_response(self, ch, method, props, body):
        self.respuestas_recibidas += 1
        codigo = body.decode()

        # Clasificamos la respuesta del worker
        if codigo == "200":
            self.stats[200] += 1
        elif codigo == "409":
            self.stats[409] += 1
        else:
            self.stats["errors"] += 1

        # Si ya hemos recibido la respuesta a todas las peticiones, cerramos el chiringuito
        if self.respuestas_recibidas == self.total_peticiones:
            self.channel.stop_consuming()

    def disparar_y_esperar(self, payloads):
        self.total_peticiones = len(payloads)
        print("¡Fuego! Inyectando mensajes en la cola...")
        start_time = time.time()

        # FASE 1: Inyección masiva
        for payload in payloads:
            self.channel.basic_publish(
                exchange='',
                routing_key=QUEUE_NAME,
                properties=pika.BasicProperties(
                    reply_to=self.callback_queue,           # "Mándame la respuesta a mi buzón"
                    correlation_id=str(uuid.uuid4()),       # ID único
                    delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE
                ),
                body=json.dumps(payload)
            )
        
        inyeccion_time = time.time() - start_time
        print(f"✅ Inyección completada en {inyeccion_time:.2f} segundos.")
        print("⏳ Esperando a que los Workers procesen las transacciones en Redis...")

        # FASE 2: Espera pasiva (Bloquea la terminal hasta que todos respondan)
        self.channel.start_consuming()

        # FASE 3: Resultados
        total_time = time.time() - start_time
        throughput = self.total_peticiones / total_time

        print("\n========================================")
        print("📊 RESULTADOS DEL BENCHMARK (RPC ASÍNCRONO)")
        print("========================================")
        print(f"Tiempo total (Inyección + Procesamiento): {total_time:.2f} segundos")
        print(f"Throughput Global Mantenido: {throughput:.2f} req/s")
        print("----------------------------------------")
        print("Desglose de respuestas (Confirmadas por Workers):")
        print(f"  ✅ [200 OK] Compras exitosas: {self.stats[200]}")
        print(f"  ❌ [409 Conflict] Asiento ocupado / Sold out: {self.stats[409]}")
        print(f"  ⚠️ Otros / Errores: {self.stats['errors']}")
        print("========================================")

def main(archivo_benchmark):
    print(f"Cargando benchmark desde: {archivo_benchmark}...")
    try:
        with open(archivo_benchmark, 'r') as f:
            lines = [line.strip() for line in f if line.startswith("BUY")]
    except FileNotFoundError:
        print(f"❌ Error: No se encontró el archivo {archivo_benchmark}")
        sys.exit(1)

    print(f"Se han cargado {len(lines)} líneas válidas.")
    
    payloads = []
    for line in lines:
        parts = line.split()
        if len(parts) == 3:
            # No numerada
            payload = {"client_id": parts[1], "request_id": parts[2]}
        elif len(parts) == 4:
            # Numerada
            payload = {"client_id": parts[1], "seat_id": parts[2], "request_id": parts[3]}
        else:
            continue
            
        payloads.append(payload)

    print(f"Preparados {len(payloads)} payloads JSON.")
    
    cliente_rpc = RpcClient()
    cliente_rpc.disparar_y_esperar(payloads)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 indirect_client.py <ruta_al_benchmark>")
        sys.exit(1)
    main(sys.argv[1])