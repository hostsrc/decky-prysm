#!/bin/bash
# Quick deploy from dev machine to Steam Deck over SSH
#
# Usage:
#   ./deploy.sh 192.168.1.50
#   ./deploy.sh steamdeck.local
#   DECK_HOST=192.168.1.50 ./deploy.sh

set -euo pipefail

DECK_HOST="${1:-${DECK_HOST:-}}"
DECK_USER="${DECK_USER:-deck}"
PLUGIN_DIR="~/homebrew/plugins/Prysm"

if [ -z "$DECK_HOST" ]; then
    echo "Usage: ./deploy.sh <steam-deck-ip>"
    echo "   or: DECK_HOST=192.168.1.50 ./deploy.sh"
    exit 1
fi

echo "▲ Deploying Prysm to $DECK_USER@$DECK_HOST"

# Build frontend
echo "→ Building..."
pnpm build 2>&1 | tail -1

# Deploy
echo "→ Deploying files..."
ssh "$DECK_USER@$DECK_HOST" "mkdir -p $PLUGIN_DIR"
scp -q main.py plugin.json package.json "$DECK_USER@$DECK_HOST:$PLUGIN_DIR/"
scp -qr dist assets defaults "$DECK_USER@$DECK_HOST:$PLUGIN_DIR/"

# Restart
echo "→ Restarting Decky Loader..."
ssh "$DECK_USER@$DECK_HOST" "sudo systemctl restart plugin_loader"

echo "✓ Deployed! Open QAM on your Deck."
