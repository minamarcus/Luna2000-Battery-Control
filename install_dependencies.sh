#!/bin/bash

echo "Installing Python dependencies for Battery Management System..."

# Define required packages based on imports in the provided code
PACKAGES=(
    "pymodbus"
    "pandas"
    "pytz"
    "python-dotenv"
    "requests"
    "aiohttp"
    "pytibber"
)

# Check if pip3 is available
if ! command -v pip3 &> /dev/null; then
    echo "❌ Error: pip3 not found. Please install Python 3 and pip3."
    exit 1
fi

# Install each package
for package in "${PACKAGES[@]}"; do
    echo "Installing $package..."
    pip3 install $package
    
    # Check if installation was successful
    if [ $? -eq 0 ]; then
        echo "✅ Successfully installed $package"
    else
        echo "❌ Failed to install $package"
        echo "Please try manually: pip3 install $package"
    fi
done

echo ""
echo "Installation complete!"
echo "You can now proceed with setting up the scheduler:"
echo "1. Run ./install_battery_scheduler.sh"
echo "2. Then ./enable_battery_scheduler.sh"