#!/usr/bin/env bash
set -e

# Change to script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo " Setting up BookStack CLI Environment"
echo "========================================"

# Check if uv is available
if command -v uv &> /dev/null || [ -x "$HOME/.local/bin/uv" ]; then
    UV_CMD="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
    echo "Found uv at: $UV_CMD"
    
    if [ ! -d ".venv" ]; then
        echo "Creating virtual environment with uv..."
        "$UV_CMD" venv .venv
    else
        echo "Virtual environment .venv already exists."
    fi

    echo "Installing dependencies with uv..."
    "$UV_CMD" pip install --python .venv/bin/python -r requirements.txt
else
    # Fallback to standard python3
    if ! command -v python3 &> /dev/null; then
        echo "Error: python3 is not installed or not found in PATH." >&2
        exit 1
    fi

    if [ ! -d ".venv" ]; then
        echo "Creating virtual environment in .venv..."
        python3 -m venv .venv
    else
        echo "Virtual environment .venv already exists."
    fi

    # Activate virtual environment
    # shellcheck source=/dev/null
    source .venv/bin/activate

    echo "Installing dependencies from requirements.txt..."
    python3 -m pip install --upgrade pip --quiet
    python3 -m pip install -r requirements.txt --quiet
fi

# Ensure scripts are executable
chmod +x chat.py 2>/dev/null || true
chmod +x subscripts/bookstack.py 2>/dev/null || true
chmod +x setup.sh

# Create .env from template if missing
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "Created .env template from .env.example. Please update it with your BookStack credentials."
elif [ -f ".env" ]; then
    echo "Existing .env found. Keeping existing .env untouched."
fi

echo "========================================"
echo " Setup complete!"
echo " To run the Interactive Chat Agent:"
echo "   ./chat.py"
echo " Or directly with python:"
echo "   .venv/bin/python chat.py"
echo " To run the CLI directly:"
echo "   .venv/bin/python subscripts/bookstack.py --help"
echo "========================================"
