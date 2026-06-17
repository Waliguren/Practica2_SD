import os, sys, json, time, psycopg2, traceback
from datetime import datetime, timezone

DB_HOST = os.getenv('DB_HOST', 'localhost')
conn = None

def conectar_postgresql():
    global conn
    try:
        if conn is not None and conn.closed == 0:
            return conn
    except:
        pass
    conn = psycopg2.connect(
        dbname="ticketdb", user="admin", password="admin123",
        host=DB_HOST, port="5432"
    )
    conn.autocommit = False
    return conn

def lambda_handler(event, context):
    global conn
    db_conn = conectar_postgresql()
    cur = db_conn.cursor()
    failed_ids = []

    for record in event.get('Records', []):
        processing_start = datetime.now(timezone.utc)
        try:
            body = json.loads(record['body'])
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
            cur.execute("""
                INSERT INTO transactions (request_id, client_id, status, completed_at)
                VALUES (%s, %s, 'processing', CURRENT_TIMESTAMP)
                ON CONFLICT (request_id) DO NOTHING
                RETURNING request_id;
            """, (request_id, client_id))
            if cur.fetchone() is None:
                db_conn.commit()
                continue

            time.sleep(0.1)

            if not es_numerada:
                cur.execute("""
                    UPDATE unnumbered_tickets
                    SET available_tickets = available_tickets - 1
                    WHERE available_tickets > 0
                    RETURNING available_tickets;
                """)
                status = "200" if cur.fetchone() else "409"
            else:
                cur.execute("""
                    INSERT INTO numbered_seats (seat_id, status)
                    VALUES (%s, 'ocupado')
                    ON CONFLICT (seat_id) DO NOTHING
                    RETURNING seat_id;
                """, (str(seat_id),))
                status = "200" if cur.fetchone() else "409"

            cur.execute("UPDATE transactions SET status = %s WHERE request_id = %s;", (status, request_id))

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
            traceback.print_exc()
            try:
                db_conn.rollback()
            except:
                pass
            try:
                db_conn.close()
            except:
                pass
            conn = None
            db_conn = conectar_postgresql()
            cur = db_conn.cursor()
            failed_ids.append({'itemIdentifier': record.get('messageId', '')})

    cur.close()

    if failed_ids:
        return {'statusCode': 200, 'batchItemFailures': failed_ids}
    return {'statusCode': 200, 'body': 'Batch processed'}
