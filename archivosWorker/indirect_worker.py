import os, sys, json, time, psycopg2, traceback
import pika
from datetime import datetime, timezone

DB_HOST = os.getenv('DB_HOST', 'localhost')
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = "booking_queue"
conn = None

def conectar_postgresql():
    global conn
    if conn is not None and conn.closed == 0:
        return conn
    conn = psycopg2.connect(dbname="ticketdb", user="admin", password="admin123", host=DB_HOST, port="5432")
    conn.autocommit = False
    return conn

def conectar_rabbitmq():
    credentials = pika.PlainCredentials('admin', 'admin123')
    parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=5672, credentials=credentials)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=QUEUE_NAME, durable=True, arguments={'x-max-priority': 10})
    return connection, channel

def lambda_handler(event, context):
    db_conn = conectar_postgresql()
    cur = db_conn.cursor()
    rabbit_conn, channel = conectar_rabbitmq()
    
    procesados = 0
    print("Lambda despierta y consumiendo directamente de RabbitMQ...")

    while True:
        if context.get_remaining_time_in_millis() < 5000:
            print("Tiempo de AWS agotándose. Saliendo...")
            break

        # basic_get saca el mensaje con más prioridad.
        method_frame, properties, body = channel.basic_get(queue=QUEUE_NAME, auto_ack=False)

        if method_frame:
            msg = json.loads(body)

            # PATRÓN POISON PILL (SUICIDIO)
            if msg.get('comando') == 'KILL':
                print("¡Píldora envenenada recibida! Suicidando Lambda...")
                channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                break

            #PROCESAMIENTO NORMAL DE TICKETS
            request_id = msg.get('request_id')
            client_id = msg.get('client_id')
            seat_id = msg.get('seat_id')
            sent_timestamp = msg.get('sent_timestamp')
            experiment_id = msg.get('experiment_id', 'unknown')
            es_numerada = seat_id is not None
            processing_start = datetime.now(timezone.utc)

            try:
                cur.execute("INSERT INTO transactions (request_id, client_id, status, completed_at) VALUES (%s, %s, 'processing', CURRENT_TIMESTAMP) ON CONFLICT (request_id) DO NOTHING RETURNING request_id;", (request_id, client_id))
                if cur.fetchone() is not None:
                    time.sleep(0.1)
                    if not es_numerada:
                        cur.execute("UPDATE unnumbered_tickets SET available_tickets = available_tickets - 1 WHERE available_tickets > 0 RETURNING available_tickets;")
                        status = "200" if cur.fetchone() else "409"
                    else:
                        cur.execute("INSERT INTO numbered_seats (seat_id, status) VALUES (%s, 'ocupado') ON CONFLICT (seat_id) DO NOTHING RETURNING seat_id;", (str(seat_id),))
                        status = "200" if cur.fetchone() else "409"

                    cur.execute("UPDATE transactions SET status = %s WHERE request_id = %s;", (status, request_id))
                    
                    processing_end = datetime.now(timezone.utc)
                    latency_ms = int((processing_end - processing_start).total_seconds() * 1000)

                    cur.execute("INSERT INTO metric_log (experiment_id, request_id, client_id, status, seat_type, sent_timestamp, processing_start, processing_end, latency_ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (request_id) DO NOTHING;", (experiment_id, request_id, client_id, status, 'numbered' if es_numerada else 'unnumbered', sent_timestamp, processing_start, processing_end, latency_ms))
                    db_conn.commit()

                # Confirmamos a RabbitMQ que ya hemos acabado
                channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                procesados += 1

            except Exception as e:
                print(f"Error procesando {request_id}: {e}")
                db_conn.rollback()
                # Devolvemos el ticket a la cola para no perder la venta
                channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)
        else:
            # Si no hay mensajes, duerme medio segundo para no saturar la CPU
            time.sleep(0.5)

    cur.close()
    rabbit_conn.close()
    return {'statusCode': 200, 'body': f'Muerta limpiamente. Procesados: {procesados}'}