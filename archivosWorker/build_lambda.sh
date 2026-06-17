#!/bin/bash
# Build Lambda deployment packages with Linux-compatible binaries
# Requires Docker

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Building Lambda deployment packages ==="

# Build worker package
echo "Building worker package..."
docker run --rm \
  -v "$SCRIPT_DIR:/app" \
  -w /app \
  python:3.10-slim bash -c "
    pip install psycopg2-binary -q -t /tmp/pkg
    cp /app/indirect_worker.py /tmp/pkg/
    cd /tmp/pkg
    rm -rf ./*.dist-info/RECORD ./*.dist-info/WHEEL
    zip -r /app/dummy_worker.zip . -x '*.pyc' '__pycache__/*'
    chmod 644 /app/dummy_worker.zip
"

# Build scaling controller package
echo "Building scaling controller package..."
docker run --rm \
  -v "$SCRIPT_DIR:/app" \
  -w /app \
  python:3.10-slim bash -c "
    zip -r /app/scaling_controller.zip scaling_controller.py -x '*.pyc'
"

echo "=== Done ==="
echo "Packages created:"
ls -lh "$SCRIPT_DIR"/dummy_worker.zip "$SCRIPT_DIR"/scaling_controller.zip
