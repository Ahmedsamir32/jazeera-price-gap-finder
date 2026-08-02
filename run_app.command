#!/bin/bash
cd "$(dirname "$0")"

export SERPAPI_API_KEY=$(grep -oE 'SERPAPI_API_KEY="[^"]*"' ~/.zshrc | tail -1 | cut -d'"' -f2)

source .venv/bin/activate
streamlit run app.py
