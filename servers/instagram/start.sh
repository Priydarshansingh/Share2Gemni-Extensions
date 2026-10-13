#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Share2Gemini Instagram Server — Quick Start Script
# ─────────────────────────────────────────────────────────────
set -e

echo "🚀 Share2Gemini Instagram Server Setup"
echo "======================================="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Install from https://python.org"
    exit 1
fi

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt -q

# Check for .env
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  No .env file found. Creating from example..."
    cp .env.example .env
    echo ""
    echo "👉 IMPORTANT: Edit .env and add your Instagram credentials:"
    echo "   IG_USERNAME=your_username"
    echo "   IG_PASSWORD=your_password"
    echo ""
    echo "   Use a SECONDARY Instagram account, not your main one!"
    echo ""
    read -p "Press Enter after editing .env to continue..."
fi

# Load .env
export $(grep -v '^#' .env | xargs)

echo ""
echo "✅ Starting server on port ${PORT:-8765}..."
echo "   Android app will connect to: http://10.0.2.2:${PORT:-8765}/extract (emulator)"
echo "   Or your PC's IP: http://$(hostname -I | awk '{print $1}'):${PORT:-8765}/extract (physical device)"
echo ""

python server.py
