#!/bin/bash
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "No venv found, using global python3..."
fi

PARAM=${1:-benchmarks/benchmark_numbered_hotspot_60000.txt}
python3 scripts/indirect_client.py "$PARAM"
