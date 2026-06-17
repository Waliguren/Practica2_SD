import os
import sys
import json
import base64
import pika
import psycopg2

DB_HOST = os.getenv('DB_HOST', 'localhost')
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')

# Reutilizamos la conexión a la base de datos entre ejecuciones (Warm Start)
conn = None

def conectar_postgresql():
    global conn
    if conn is None or conn.closed != 0:
        try:
            conn = psycopg2.connect(dbname="ticketdb", user="admin", password="admin123", host=DB_HOST, port="5432")
            conn.autocommit = False
            print("✅ Conectado a PostgreSQL.")
        except Exception as e:
            print(f"❌ Error al conectar a PostgreSQL: {e}")
            raise e
    return conn

def responder_cliente(respuesta_http, reply_to, correlation_id):
    """Abre una conexión efímera a RabbitMQ para devolver el código al cliente"""
    try:
        credentials = pika.PlainCredentials('admin', 'admin123')
        parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, port=5672, credentials=credentials)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        
        channel.basic_publish(
            exchange='',
            routing_key=reply_to,
            properties=pika.BasicProperties(correlation_id=correlation_id),
            body=respuesta_http
        )
        connection.close()
    except Exception as e:
        print(f"⚠️ No se pudo responder al cliente: {e}")

def lambda_handler(event, context):
    db_conn = conectar_postgresql()
    cur = db_conn.cursor()
    
    # AWS Lambda agrupa los mensajes por cola en el evento
    messages_by_queue = event.get('rmqMessagesByQueue', {})
    
    for queue_name, messages in messages_by_queue.items():
        for msg in messages:
            # 1. Decodificar el mensaje que viene en Base64 desde AWS
            payload_b64 = msg.get('data', '')
            peticion = base64.b64decode(payload_b64).decode('utf-8')
            
            # Recuperar propiedades RPC necesarias para responder
            props = msg.get('redelivered', False) # Truco para ignorar si no vienen vacías
            # Nota: Lambda mapea las propiedades AMQP dentro del objeto de cada mensaje
            reply_to = msg.get('properties', {}).get('replyTo')
            correlation_id = msg.get('properties', {}).get('correlationId')

            asiento = None
            try:
                datos = json.loads(peticion)
                es_numerada = "seat_id" in datos
                asiento = datos.get("seat_id")
                request_id = datos.get("request_id")
                client_id = datos.get("client_id")
            except Exception as e:
                print(f"❌ Error parseando JSON: {peticion}")
                continue

            try:
                # Control de Idempotencia
                cur.execute("SELECT status FROM transactions WHERE request_id = %s;", (request_id,))
                row = cur.fetchone()
                
                if row:
                    respuesta_http = row[0]
                    db_conn.rollback()
                    if reply_to and correlation_id:
                        responder_cliente(respuesta_http, reply_to, correlation_id)
                    continue

                # Lógica de negocio
                if not es_numerada:
                    cur.execute("""
                        UPDATE unnumbered_tickets 
                        SET available_tickets = available_tickets - 1 
                        WHERE available_tickets > 0 
                        RETURNING available_tickets;
                    """)
                    if cur.fetchone():
                        respuesta_http = "200"
                    else:
                        respuesta_http = "409"
                else:
                    cur.execute("""
                        INSERT INTO numbered_seats (seat_id, status) 
                        VALUES (%s, 'ocupado') 
                        ON CONFLICT (seat_id) DO NOTHING 
                        RETURNING seat_id;
                    """, (str(asiento),))
                    
                    if cur.fetchone():
                        respuesta_http = "200"
                    else:
                        respuesta_http = "409"

                # Guardar Transacción
                cur.execute("""
                    INSERT INTO transactions (request_id, client_id, status, completed_at) 
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP);
                """, (request_id, client_id, respuesta_http))
                
                db_conn.commit()

                # Responder si el cliente lo requiere
                if reply_to and correlation_id:
                    responder_cliente(respuesta_http, reply_to, correlation_id)

            except Exception as e:
                print(f"⚠️ Error en transacción {request_id}: {e}")
                db_conn.rollback()
                
    cur.close()
    return {"statusCode": 200, "body": "Lote procesado"}