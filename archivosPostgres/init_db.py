import psycopg2
import time
import sys

DB_HOST = "localhost"
DB_USER = "admin"
DB_PASS = "admin123"
DB_NAME = "ticketdb"

def init_db():
    conn = None
    for i in range(15):
        try:
            conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port="5432")
            print("Connected to PostgreSQL.")
            break
        except Exception as e:
            print(f"Waiting for PostgreSQL... ({i+1}/15)")
            time.sleep(3)

    if not conn:
        print("Error: Could not connect to PostgreSQL.")
        sys.exit(1)

    cur = conn.cursor()

    print("Cleaning database...")
    cur.execute("DROP TABLE IF EXISTS unnumbered_tickets CASCADE;")
    cur.execute("DROP TABLE IF EXISTS numbered_seats CASCADE;")
    cur.execute("DROP TABLE IF EXISTS transactions CASCADE;")
    cur.execute("DROP TABLE IF EXISTS metric_log CASCADE;")

    print("Creating fresh schema...")
    cur.execute("""
        CREATE TABLE unnumbered_tickets (
            id SERIAL PRIMARY KEY,
            available_tickets INTEGER NOT NULL CHECK (available_tickets >= 0)
        );
    """)

    cur.execute("""
        CREATE TABLE numbered_seats (
            seat_id VARCHAR(50) PRIMARY KEY,
            status VARCHAR(20) NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE transactions (
            request_id VARCHAR(100) PRIMARY KEY,
            client_id VARCHAR(100),
            status VARCHAR(10),
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE metric_log (
            id SERIAL PRIMARY KEY,
            experiment_id VARCHAR(100),
            request_id VARCHAR(100) UNIQUE,
            client_id VARCHAR(100),
            status VARCHAR(10),
            seat_type VARCHAR(20),
            sent_timestamp VARCHAR(50),
            processing_start TIMESTAMP,
            processing_end TIMESTAMP,
            latency_ms INTEGER,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("INSERT INTO unnumbered_tickets (available_tickets) VALUES (100000);")
    print("Restored 100,000 unnumbered tickets.")

    conn.commit()
    cur.close()
    conn.close()
    print("Database initialized and ready.")

if __name__ == "__main__":
    init_db()
