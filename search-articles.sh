#!/bin/bash
# search-articles.sh — Search Google News for litigation finance articles (past 24 hours)
# Usage: ./search-articles.sh [query]
# Default query: "litigation finance"
# Runs multiple searches and deduplicates results.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

source "$ENV_FILE"

if [ -z "${SERPAPI_KEY:-}" ]; then
    echo "Error: SERPAPI_KEY not set in .env"
    exit 1
fi

python3 "${SCRIPT_DIR}/search_news.py"
