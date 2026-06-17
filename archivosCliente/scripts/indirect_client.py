import pika
import time
import uuid
import os
import sys
import json
import psycopg2
from datetime import datetime, timezone

RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
QUEUE_NAME = 'booking_queue'
DB_HOST = os.getenv('DB_HOST', 'localhost')
EXPERIMENT_ID = str(uuid.uuid4())[:8]

def generate_zt_workload(duration=180):
    payloads = []
    start_time = time.time()
    msg_index = 0

    print(f"Generating Z(t) workload for {duration}s (experiment_id={EXPERIMENT_ID})...")

    while True:
        elapsed = time.time() - start_time
        if elapsed > duration:
            break

        if elapsed < 30:
            rate = 50
        elif elapsed < 60:
            ramp_progress = (elapsed - 30) / 30
            rate = int(50 + ramp_progress * 450)
        elif elapsed < 90:
            rate = 1000
        elif elapsed < 150:
            rate = 500
        else:
            cool_progress = (elapsed - 150) / 30
            rate = int(500 - cool_progress * 450)

        msg_index += 1
        payloads.append({
            "client_id": f"user{msg_index:06d}",
            "request_id": f"{EXPERIMENT_ID}_{msg_index:06d}",
            "sent_timestamp": datetime.now(timezone.utc).isoformat(),
            "experiment_id": EXPERIMENT_ID
        })

        sleep_time = 1.0 / max(rate, 1)
        time.sleep(sleep_time)

    return payloads

def generate_numbered_workload(duration=180, hotspot=False):
    payloads = []
    start_time = time.time()
    msg_index = 0
    total_seats = 100000

    if hotspot:
        hotspot_pool = list(range(1, 5001))
        normal_pool = list(range(5001, total_seats + 1))

    print(f"Generating Z(t) numbered workload for {duration}s (hotspot={hotspot})...")

    while True:
        elapsed = time.time() - start_time
        if elapsed > duration:
            break

        if elapsed < 30:
            rate = 50
        elif elapsed < 60:
            ramp_progress = (elapsed - 30) / 30
            rate = int(50 + ramp_progress * 450)
        elif elapsed < 90:
            rate = 1000
        elif elapsed < 150:
            rate = 500
        else:
            cool_progress = (elapsed - 150) / 30
            rate = int(500 - cool_progress * 450)

        msg_index += 1

        if hotspot:
            if msg_index <= int(msg_index * 0.8 / 1.0) and hotspot_pool:
                seat_id = hotspot_pool[(msg_index - 1) % len(hotspot_pool)]
            else:
                seat_id = normal_pool[(msg_index - 1) % len(normal_pool)]
        else:
            seat_id = ((msg_index - 1) % total_seats) + 1

        payloads.append({
            "client_id": f"user{msg_index:06d}",
            "seat_id": seat_id,
            "request_id": f"{EXPERIMENT_ID}_{msg_index:06d}",
            "sent_timestamp": datetime.now(timezone.utc).isoformat(),
            "experiment_id": EXPERIMENT_ID
        })

        sleep_time = 1.0 / max(rate, 1)
        time.sleep(sleep_time)

    return payloads

def read_benchmark_file(filepath):
    payloads = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith("BUY"):
                parts = line.split()
                if len(parts) == 3:
                    payloads.append({
                        "client_id": parts[1],
                        "request_id": parts[2],
                        "sent_timestamp": datetime.now(timezone.utc).isoformat(),
                        "experiment_id": EXPERIMENT_ID
                    })
                elif len(parts) == 4:
                    payloads.append({
                        "client_id": parts[1],
                        "seat_id": parts[2],
                        "request_id": parts[3],
                        "sent_timestamp": datetime.now(timezone.utc).isoformat(),
                        "experiment_id": EXPERIMENT_ID
                    })
    return payloads

def send_payloads(payloads):
    print(f"Connecting to RabbitMQ at {RABBITMQ_HOST}...")
    credentials = pika.PlainCredentials('admin', 'admin123')
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST, port=5672, credentials=credentials
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    print(f"Sending {len(payloads)} messages...")
    experiment_start = time.time()

    for i, payload in enumerate(payloads):
        channel.basic_publish(
            exchange='',
            routing_key=QUEUE_NAME,
            properties=pika.BasicProperties(delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE),
            body=json.dumps(payload)
        )

        if (i + 1) % 1000 == 0:
            elapsed = time.time() - experiment_start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(f"  Sent {i+1}/{len(payloads)} ({rate:.0f} msg/s)")

    experiment_end = time.time()
    total_send_time = experiment_end - experiment_start
    connection.close()

    print(f"\nAll messages sent in {total_send_time:.2f}s")
    return experiment_start, experiment_end

def get_metrics_from_db():
    print("\nWaiting for workers to finish processing...")
    time.sleep(5)

    try:
        conn = psycopg2.connect(
            dbname="ticketdb", user="admin", password="admin123",
            host=DB_HOST, port="5432"
        )
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*), 
                   COALESCE(AVG(latency_ms), 0),
                   COALESCE(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms), 0),
                   COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms), 0),
                   COALESCE(PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY latency_ms), 0),
                   SUM(CASE WHEN status = '200' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status = '409' THEN 1 ELSE 0 END)
            FROM metric_log 
            WHERE experiment_id = %s;
        """, (EXPERIMENT_ID,))

        row = cur.fetchone()
        total = row[0] or 0
        avg_lat = row[1] or 0
        p50 = row[2] or 0
        p95 = row[3] or 0
        p99 = row[4] or 0
        success = row[5] or 0
        conflicts = row[6] or 0

        cur.execute("""
            SELECT MIN(processing_start), MAX(processing_end)
            FROM metric_log WHERE experiment_id = %s;
        """, (EXPERIMENT_ID,))
        time_row = cur.fetchone()
        first_start = time_row[0]
        last_end = time_row[1]

        cur.close()
        conn.close()

        return {
            'total': total, 'success': success, 'conflicts': conflicts,
            'avg_latency': avg_lat, 'p50': p50, 'p95': p95, 'p99': p99,
            'first_start': first_start, 'last_end': last_end
        }
    except Exception as e:
        print(f"Could not query metrics: {e}")
        return None

def print_results(experiment_start, experiment_end, total_sent, metrics):
    total_time = experiment_end - experiment_start

    if metrics and metrics['total'] > 0:
        e2e_start = metrics['first_start']
        e2e_end = metrics['last_end']
        if e2e_start and e2e_end:
            e2e_time = (e2e_end - e2e_start).total_seconds()
        else:
            e2e_time = total_time
        throughput = metrics['total'] / e2e_time
    else:
        e2e_time = total_time
        throughput = total_sent / total_time

    print("\n" + "=" * 55)
    print("  RESULTS - ELASTIC TICKET SYSTEM")
    print("=" * 55)
    print(f"  Experiment ID:     {EXPERIMENT_ID}")
    print(f"  Total sent:        {total_sent}")
    print(f"  Experiment time:   {e2e_time:.2f}s")
    print(f"  Throughput:        {throughput:.2f} req/s")
    print("-" * 55)

    if metrics:
        print(f"  Messages processed: {metrics['total']}")
        print(f"  Success (200):     {metrics['success']}")
        print(f"  Conflicts (409):   {metrics['conflicts']}")
        print(f"  Avg latency:       {metrics['avg_latency']:.1f}ms")
        print(f"  P50 latency:       {metrics['p50']:.1f}ms")
        print(f"  P95 latency:       {metrics['p95']:.1f}ms")
        print(f"  P99 latency:       {metrics['p99']:.1f}ms")
    else:
        print("  (metrics not available)")

    print("=" * 55)

def main(mode, param=None):
    if mode == 'zt_unnumbered':
        duration = int(param) if param else 180
        payloads = generate_zt_workload(duration)
    elif mode == 'zt_numbered':
        duration = int(param) if param else 180
        payloads = generate_numbered_workload(duration, hotspot=False)
    elif mode == 'zt_hotspot':
        duration = int(param) if param else 180
        payloads = generate_numbered_workload(duration, hotspot=True)
    elif mode == 'file':
        payloads = read_benchmark_file(param)
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python3 indirect_client.py <mode> [param]")
        print("  Modes:")
        print("    zt_unnumbered [duration]  - Z(t) unnumbered workload")
        print("    zt_numbered  [duration]   - Z(t) numbered uniform workload")
        print("    zt_hotspot   [duration]   - Z(t) numbered hotspot workload")
        print("    file         <path>       - Read from benchmark file")
        sys.exit(1)

    print(f"Prepared {len(payloads)} payloads.")
    experiment_start, experiment_end = send_payloads(payloads)
    metrics = get_metrics_from_db()
    print_results(experiment_start, experiment_end, len(payloads), metrics)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 indirect_client.py <mode> [param]")
        sys.exit(1)
    mode = sys.argv[1]
    param = sys.argv[2] if len(sys.argv) > 2 else None
    main(mode, param)
