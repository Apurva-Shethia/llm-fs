#!/usr/bin/env bash

set -euo pipefail

VENV_DIR="venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $PYTHON_VERSION"

# Create virtual environment (recommended)
if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "Creating virtual environment..."
    python -m venv "$VENV_DIR"
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

echo ""
echo "Installing dependencies into $VENV_DIR..."
"$VENV_PIP" install --upgrade pip >/dev/null
"$VENV_PIP" install -r requirements.txt
echo "✓ Dependencies installed"

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    echo "✓ Loaded environment variables from .env"
else
    echo "⚠️  No .env file found; copy .env.example and set GEMINI_API_KEY"
fi

echo ""
echo "Rebuilding vector database and running job matching..."
"$VENV_PYTHON" resume_rag.py
"$VENV_PYTHON" job_matcher.py

echo ""
echo "✓ Pipeline complete"
