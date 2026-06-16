import pika
import os
import sys
import time
import json
import psycopg2

# Variables inyectadas por Terraform (o puestas a mano para pruebas locales)
DB_HOST = os.getenv('DB_HOST', 'localhost')
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = 'booking_queue'

max_reintentos = 15

def conectar_postgresql():
    # 1. Esperar a que PostgreSQL esté listo
    conn = None
    for i in range(15):
        try:
            conn = psycopg2.connect(dbname="ticketdb", user="admin", password="admin123", host=DB_HOST, port="5432")
            conn.autocommit = False
            print("✅ Conectado a PostgreSQL.")
            break
        except Exception as e:
            print(f"⏳ Esperando a PostgreSQL... ({i+1}/15)")
            time.sleep(3)
            
    if not conn:
        print("❌ Error: No se pudo conectar a PostgreSQL.")
        sys.exit(1)

    return conn

conn = conectar_postgresql()

# 2. Conexión a RabbitMQ con paciencia (Reintentos)
def conectar_rabbitmq():
    for i in range(max_reintentos):
        try:
            credentials = pika.PlainCredentials('admin', 'admin123')
            # Aumentamos el heartbeat a 600s (10 minutos) para evitar cortes de conexión en AWS 
            # cuando la red tiene micro-cortes o cuando el worker está inactivo por un tiempo.
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST, port=5672, virtual_host='/', credentials=credentials,
                heartbeat=600, blocked_connection_timeout=300
            )
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE_NAME, durable=True)
            channel.basic_qos(prefetch_count=100)
            print(f"✅ Conectado a RabbitMQ en {RABBITMQ_HOST}. Esperando trabajo...")
            return connection, channel
        except Exception as e:
            print(f"⚠️ RabbitMQ no responde. Reintentando en 5 segundos... ({i+1}/{max_reintentos})")
            time.sleep(5)
    print("❌ Imposible conectar a RabbitMQ. Apagando worker.")
    sys.exit(1)

connection, channel = conectar_rabbitmq()

def responder_y_confirmar(ch, method, props, respuesta_http):
    if props.reply_to and props.correlation_id:
        try:
            ch.basic_publish(
                exchange='',
                routing_key=props.reply_to,
                properties=pika.BasicProperties(
                    correlation_id=props.correlation_id
                ),
                body=respuesta_http
            )
        except Exception as e:
            print(f"⚠️ Error al responder al cliente (quizás se desconectó): {e}")
            
    # Confirmar a RabbitMQ que hemos procesado el mensaje (ACK)
    ch.basic_ack(delivery_tag=method.delivery_tag)

# 3. La lógica central del Worker
def procesar_mensaje(ch, method, props, body):
    peticion = body.decode()
    time.sleep(0.1)

    # 1. Detectar si es numerada o no numerada
    asiento = None
    try:
        # Intento de parsear JSON
        datos = json.loads(peticion)
        es_numerada = "seat_id" in datos
        asiento = datos.get("seat_id")
        request_id = datos.get("request_id")
        client_id = datos.get("client_id")
    except Exception as e:
        print(f"❌ Error parseando JSON: {peticion}")
        ch.basic_reject(delivery_tag=method.delivery_tag, requeue=False) #Indicar a RabbitMQ que ha habido un error y que no reintente
        return

    cur = conn.cursor()

    try:
        # Comprobamos si este request_id ya se completó antes (por si RabbitMQ reenvía el mensaje tras una caída)
        cur.execute("SELECT status FROM transactions WHERE request_id = %s;", (request_id,))
        row = cur.fetchone()
        
        if row:
            print(f"🔁 Petición duplicada detectada ({request_id}). Devolviendo estado anterior.")
            respuesta_http = row[0]
            conn.rollback() # Limpiamos la transacción actual
            responder_y_confirmar(ch, method, props, respuesta_http)
            return

        if not es_numerada:
            cur.execute("""
                UPDATE unnumbered_tickets 
                SET available_tickets = available_tickets - 1 
                WHERE available_tickets > 0 
                RETURNING available_tickets;
            """)
            if cur.fetchone(): #Operación exitosa, había entradas disponibles
                respuesta_http = "200" # Se pudo restar, hay entrada
            else:
                respuesta_http = "409" # No se pudo restar, sold out
                
        else:
            cur.execute("""
                INSERT INTO numbered_seats (seat_id, status) 
                VALUES (%s, 'ocupado') 
                ON CONFLICT (seat_id) DO NOTHING 
                RETURNING seat_id;
            """, (str(asiento),))
            
            if cur.fetchone(): #Operación exitosa, había entradas disponibles
                respuesta_http = "200" # Fila insertada, asiento reservado
            else:
                respuesta_http = "409" # El asiento ya estaba ocupado

        # Guardamos el resultado con CURRENT_TIMESTAMP.
        cur.execute("""
            INSERT INTO transactions (request_id, client_id, status, completed_at) 
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP);
        """, (request_id, client_id, respuesta_http))
        
        # Commit de la transacción (si todo ha ido bien)
        conn.commit()

        # Respondemos al cliente
        responder_y_confirmar(ch, method, props, respuesta_http)

    except Exception as e:
        print(f"⚠️ Error grave procesando transacción {request_id}: {e}")
        conn.rollback()
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False) 
    finally:
        cur.close()

# Arrancamos el bucle infinito
def iniciar_consumo():
    # AÑADIR conn a las variables globales para poder reconectarla
    global connection, channel, conn
    while True:
        try:
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=procesar_mensaje)
            channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            print(f"⚠️ Conexión con RabbitMQ perdida ({e}). Reconectando en 5 segundos...")
            time.sleep(5)
            connection, channel = conectar_rabbitmq()
            
        # AÑADIR ESTE BLOQUE POR SI FALLA LA BASE DE DATOS
        except psycopg2.InterfaceError:
            print(f"⚠️ Conexión con PostgreSQL perdida. Reconectando en 5 segundos...")
            time.sleep(5)
            conn = conectar_postgresql()
            
        except KeyboardInterrupt:
            print("\nApagando worker de forma segura...")
            connection.close()
            conn.close() # Cerramos también la base de datos
            break
        except Exception as e:
            print(f"⚠️ Error inesperado ({e}). Reconectando en 5 segundos...")
            time.sleep(5)
            connection, channel = conectar_rabbitmq()

iniciar_consumo()