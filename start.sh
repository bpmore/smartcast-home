#!/bin/bash
# Launch the Vizio web remote. Open the printed URL on any device on your Wi-Fi.
cd "$(dirname "$0")"
source .venv/bin/activate
exec python server.py
