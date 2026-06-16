import psycopg2
import time
import sys

DB_HOST = "localhost"
DB_USER = "admin"
DB_PASS = "admin123"
DB_NAME = "ticketdb"

def init_db():
    # 1. Esperar a que PostgreSQL esté listo
    conn = None
    for i in range(15):
        try:
            conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port="5432")
            print("✅ Conectado a PostgreSQL.")
            break
        except Exception as e:
            print(f"⏳ Esperando a PostgreSQL... ({i+1}/15)")
            time.sleep(3)
            
    if not conn:
        print("❌ Error: No se pudo conectar a PostgreSQL.")
        sys.exit(1)

    cur = conn.cursor()

    # 2. BORRAR TABLAS EXISTENTES (El "CASCADE" elimina también las relaciones si las hubiera)
    print("🧹 Limpiando base de datos anterior...")
    cur.execute("DROP TABLE IF EXISTS unnumbered_tickets CASCADE;")
    cur.execute("DROP TABLE IF EXISTS numbered_seats CASCADE;")
    cur.execute("DROP TABLE IF EXISTS transactions CASCADE;")

    # 3. CREAR TABLAS DESDE CERO
    print("🏗️ Creando esquema limpio...")
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

    # 4. INSERTAR DATOS INICIALES (Como acabamos de borrar la tabla, insertamos sin preguntar)
    cur.execute("INSERT INTO unnumbered_tickets (available_tickets) VALUES (100000);")
    print("✅ Se han restaurado las 100.000 entradas no numeradas.")

    conn.commit()
    cur.close()
    conn.close()
    print("🚀 Base de datos reiniciada con éxito y lista para un nuevo test.")

if __name__ == "__main__":
    init_db()