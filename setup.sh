#!/bin/bash
# Setup script for RAG Resume Matching System

set -e

echo "========================================"
echo "RAG System Setup Script"
echo "========================================"

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $PYTHON_VERSION"

# Create virtual environment (recommended)
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python -m venv venv
    source venv/bin/activate
    echo "✓ Virtual environment created and activated"
else
    echo "✓ Virtual environment already exists"
    source venv/bin/activate
fi

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
echo "✓ Dependencies installed"

# Create data directories
echo ""
echo "Creating data directories..."
mkdir -p data/synthetic_resumes
mkdir -p data/job_descriptions
mkdir -p data/chroma_db
echo "✓ Data directories created"

# Check for API key
echo ""
echo "========================================"
echo "Configuration Check"
echo "========================================"

if [ -z "$GEMINI_API_KEY" ]; then
    echo "⚠️  GEMINI_API_KEY not set"
    echo "   Set it with: export GEMINI_API_KEY='your-key'"
else
    echo "✓ GEMINI_API_KEY is set"
fi

# Generate synthetic data if not already present
echo ""
echo "Checking synthetic data..."
RESUME_COUNT=$(ls -1 data/synthetic_resumes/*.json 2>/dev/null | wc -l)
if [ $RESUME_COUNT -eq 0 ]; then
    echo "  Generating synthetic data..."
    python data_generator.py
else
    echo "  ✓ Synthetic data already exists ($RESUME_COUNT resumes)"
fi

echo ""
echo "========================================"
echo "✓ Setup Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "1. export GEMINI_API_KEY='your-key'"
echo "2. python resume_rag.py          # Initialize RAG system"
echo "3. python job_matcher.py         # Run matching"
echo "4. jupyter notebook analysis.ipynb  # Run analysis"
echo ""
echo "See README.md for full documentation"
