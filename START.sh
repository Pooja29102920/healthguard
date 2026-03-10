#!/bin/bash
echo "HealthGuard v10 - Starting..."
cd "$(dirname "$0")/backend"
echo "Server starting at http://localhost:8000"
echo "Open your browser to http://localhost:8000"
echo "Press Ctrl+C to stop"
python3 server.py
