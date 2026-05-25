#!/bin/bash
# Quick setup script for Zaptec Home Assistant Bridge

echo "=== Zaptec Home Assistant Bridge Setup ==="
echo ""

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install it first."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt

# Create config file if it doesn't exist
if [ ! -f config.yaml ]; then
    echo ""
    echo "Creating config.yaml from template..."
    cp config.example.yaml config.yaml
    echo "⚠️  Please edit config.yaml with your credentials!"
else
    echo ""
    echo "✅ config.yaml already exists"
fi

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Next steps:"
echo "1. Edit config.yaml with your Zaptec and MQTT credentials"
echo "2. Run the bridge:"
echo "   source venv/bin/activate"
echo "   python zaptec_bridge.py"
echo ""
echo "Or install as a systemd service:"
echo "   sudo cp zaptec-bridge.service /etc/systemd/system/"
echo "   sudo systemctl enable zaptec-bridge"
echo "   sudo systemctl start zaptec-bridge"
