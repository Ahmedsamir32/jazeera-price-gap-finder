#!/bin/bash
cd "$(dirname "$0")"

export SERPAPI_API_KEY=$(grep -oE 'SERPAPI_API_KEY="[^"]*"' ~/.zshrc | tail -1 | cut -d'"' -f2)

source .venv/bin/activate
python3 price_gap_finder_serpapi.py

echo ""
echo "Done. Press any key to close this window."
read -n 1 -s
