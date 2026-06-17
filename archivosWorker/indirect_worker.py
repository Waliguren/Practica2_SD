import os
import sys
import json
import time
import psycopg2
from datetime import datetime, timezone

DB_HOST = os.getenv('DB_HOST', 'localhost')

conn = None

def conectar_postgresql():
    global conn
    if conn is None or conn.closed != 0:
        conn = psycopg2.connect(
            dbname="ticketdb", user="admin", password="admin123",
            host=DB_HOST, port="5432"
        )
        conn.autocommit = False
    return conn

def lambda_handler(event, context):
    db_conn = conectar_postgresql()
    cur = db_conn.cursor()
    failed_ids = []

    for record in event.get('Records', []):
        processing_start = datetime.now(timezone.utc)

        try:
            body = json.loads(record['body'])
            message_id = record.get('messageId', 'unknown')
        except Exception as e:
            print(f"Error parsing message: {e}")
            failed_ids.append({'itemIdentifier': record.get('messageId', '')})
            continue

        request_id = body.get('request_id')
        client_id = body.get('client_id')
        seat_id = body.get('seat_id')
        sent_timestamp = body.get('sent_timestamp')
        experiment_id = body.get('experiment_id', 'unknown')
        es_numerada = seat_id is not None

        try:
            cur.execute("SELECT status FROM transactions WHERE request_id = %s;", (request_id,))
            existing = cur.fetchone()
            if existing:
                db_conn.rollback()
                continue

            time.sleep(0.1)

            if not es_numerada:
                cur.execute("""
                    UPDATE unnumbered_tickets
                    SET available_tickets = available_tickets - 1
                    WHERE available_tickets > 0
                    RETURNING available_tickets;
                """)
                if cur.fetchone():
                    status = "200"
                else:
                    status = "409"
            else:
                cur.execute("""
                    INSERT INTO numbered_seats (seat_id, status)
                    VALUES (%s, 'ocupado')
                    ON CONFLICT (seat_id) DO NOTHING
                    RETURNING seat_id;
                """, (str(seat_id),))
                if cur.fetchone():
                    status = "200"
                else:
                    status = "409"

            cur.execute("""
                INSERT INTO transactions (request_id, client_id, status, completed_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP);
            """, (request_id, client_id, status))

            processing_end = datetime.now(timezone.utc)
            latency_ms = int((processing_end - processing_start).total_seconds() * 1000)

            cur.execute("""
                INSERT INTO metric_log
                    (experiment_id, request_id, client_id, status, seat_type,
                     sent_timestamp, processing_start, processing_end, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (request_id) DO NOTHING;
            """, (
                experiment_id, request_id, client_id, status,
                'numbered' if es_numerada else 'unnumbered',
                sent_timestamp, processing_start, processing_end, latency_ms
            ))

            db_conn.commit()

        except Exception as e:
            print(f"Error processing {request_id}: {e}")
            db_conn.rollback()
            failed_ids.append({'itemIdentifier': record.get('messageId', '')})

    cur.close()

    if failed_ids:
        return {
            'statusCode': 200,
            'batchItemFailures': failed_ids
        }

    return {'statusCode': 200, 'body': 'Batch processed'}
