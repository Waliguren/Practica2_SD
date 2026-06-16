#!/bin/bash

# Aseguramos que estamos en el entorno virtual
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "⚠️ No se encontró el entorno virtual 'venv'. Ejecutando con python3 global..."
fi

# Ejecutamos el cliente pasándole el archivo de prueba
python3 scripts/indirect_client.py benchmarks/benchmark_numbered_60000.txt