import os

BENCHMARK_DIR = "archivosCliente/benchmarks"

def generar_unnumbered(n, filename):
    path = os.path.join(BENCHMARK_DIR, filename)
    with open(path, 'w') as f:
        f.write(f"# Concert Ticket Benchmark - Unnumbered Seats\n")
        f.write(f"# Total tickets: {n}\n")
        f.write(f"# Format: BUY <client_id> <request_id>\n\n")
        for i in range(1, n + 1):
            f.write(f"BUY user{i:05d} {i:05d}\n")
    print(f"Generated {path} with {n} entries")

def generar_numbered_uniform(n, total_seats, filename):
    path = os.path.join(BENCHMARK_DIR, filename)
    with open(path, 'w') as f:
        f.write(f"# Concert Ticket Benchmark - Numbered Seats (Uniform)\n")
        f.write(f"# Seats: 1..{total_seats}\n")
        f.write(f"# Format: BUY <client_id> <seat_id> <request_id>\n\n")
        for i in range(1, n + 1):
            seat_id = ((i - 1) % total_seats) + 1
            f.write(f"BUY user{i:05d} {seat_id} {i:05d}\n")
    print(f"Generated {path} with {n} entries (uniform over {total_seats} seats)")

def generar_numbered_hotspot(n, total_seats, hotspot_pct, hotspot_seats_pct, filename):
    path = os.path.join(BENCHMARK_DIR, filename)
    hot_count = int(hotspot_pct * n)
    normal_count = n - hot_count
    hot_seats = int(total_seats * hotspot_seats_pct)

    with open(path, 'w') as f:
        f.write(f"# Concert Ticket Benchmark - Numbered Seats (Hotspot)\n")
        f.write(f"# {hotspot_pct*100:.0f}% requests -> {hotspot_seats_pct*100:.0f}% seats\n")
        f.write(f"# Format: BUY <client_id> <seat_id> <request_id>\n\n")
        for i in range(1, hot_count + 1):
            seat_id = ((i - 1) % hot_seats) + 1
            f.write(f"BUY user{i:05d} {seat_id} {i:05d}\n")
        for i in range(1, normal_count + 1):
            seat_id = hot_seats + ((i - 1) % (total_seats - hot_seats)) + 1
            idx = hot_count + i
            f.write(f"BUY user{idx:05d} {seat_id} {idx:05d}\n")
    print(f"Generated {path} with {n} entries ({hotspot_pct*100:.0f}% -> {hot_seats} seats)")

def main():
    os.makedirs(BENCHMARK_DIR, exist_ok=True)

    generar_unnumbered(20000, "benchmark_unnumbered_20000.txt")
    generar_unnumbered(50000, "benchmark_unnumbered_50000.txt")

    generar_numbered_uniform(20000, 100000, "benchmark_numbered_uniform_20000.txt")
    generar_numbered_uniform(60000, 100000, "benchmark_numbered_uniform_60000.txt")

    generar_numbered_hotspot(20000, 100000, 0.8, 0.05, "benchmark_numbered_hotspot_20000.txt")
    generar_numbered_hotspot(60000, 100000, 0.8, 0.05, "benchmark_numbered_hotspot_60000.txt")

if __name__ == "__main__":
    main()
