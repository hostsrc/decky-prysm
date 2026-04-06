#!/bin/bash
# Prysm installer for Steam Deck
# Run this directly on your Steam Deck (Desktop Mode → Konsole)
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/hostsrc/decky-prysm/main/install.sh | bash
#   — or —
#   git clone https://github.com/hostsrc/decky-prysm.git && cd decky-prysm && ./install.sh

set -euo pipefail

PLUGIN_NAME="Prysm"
PLUGIN_DIR="$HOME/homebrew/plugins/$PLUGIN_NAME"
REPO_URL="https://github.com/hostsrc/decky-prysm.git"
TMP_DIR="/tmp/prysm-install"

echo ""
echo "  ▲ PRYSM — Split your screen everywhere"
echo "  ─────────────────────────────────────────"
echo ""

# Check if Decky Loader is installed
if [ ! -d "$HOME/homebrew/plugins" ]; then
    echo "  ✗ Decky Loader not found at ~/homebrew/plugins"
    echo "    Install Decky first: https://decky.xyz"
    exit 1
fi
echo "  ✓ Decky Loader found"

# Check if we're running from the repo or need to clone
if [ -f "./main.py" ] && [ -f "./plugin.json" ]; then
    SRC_DIR="."
    echo "  ✓ Running from repo directory"
else
    echo "  → Cloning from GitHub..."
    rm -rf "$TMP_DIR"
    git clone --depth 1 "$REPO_URL" "$TMP_DIR" 2>/dev/null
    SRC_DIR="$TMP_DIR"
    echo "  ✓ Cloned"
fi

# Install plugin files
echo "  → Installing to $PLUGIN_DIR"
mkdir -p "$PLUGIN_DIR"
cp "$SRC_DIR/main.py" "$PLUGIN_DIR/"
cp "$SRC_DIR/plugin.json" "$PLUGIN_DIR/"
cp "$SRC_DIR/package.json" "$PLUGIN_DIR/"
cp -r "$SRC_DIR/dist" "$PLUGIN_DIR/"
cp -r "$SRC_DIR/assets" "$PLUGIN_DIR/"
cp -r "$SRC_DIR/defaults" "$PLUGIN_DIR/"
echo "  ✓ Plugin files installed"

# Clean up temp dir if we cloned
if [ "$SRC_DIR" = "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
fi

# Check for xdotool (needed for Discord Go Live automation)
if command -v xdotool &>/dev/null; then
    echo "  ✓ xdotool found"
else
    echo "  ⚠ xdotool not found (needed for Discord Go Live mode)"
    echo "    Install with: sudo steamos-readonly disable && sudo pacman -Sy --noconfirm xdotool && sudo steamos-readonly enable"
fi

# Check for Vesktop
if flatpak info dev.vencord.Vesktop &>/dev/null 2>&1; then
    echo "  ✓ Vesktop found"
else
    echo "  ⚠ Vesktop not found (needed for Discord Go Live mode)"
    echo "    Install with: flatpak install dev.vencord.Vesktop"
fi

# Restart Decky Loader
echo "  → Restarting Decky Loader..."
if sudo systemctl restart plugin_loader 2>/dev/null; then
    echo "  ✓ Decky Loader restarted"
else
    echo "  ⚠ Could not restart Decky Loader (try manually: sudo systemctl restart plugin_loader)"
fi

echo ""
echo "  ✓ Prysm installed!"
echo ""
echo "  Switch to Game Mode → press ... (QAM) → find Prysm"
echo ""
