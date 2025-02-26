#!/bin/bash

# Script to create a .env file with required configuration

echo "Battery Management System Configuration"
echo "======================================="
echo "This script will help create a .env file with your configuration."
echo "The .env file is used to store sensitive configuration like your Tibber token."
echo ""

# Check if .env already exists and offer to back it up
if [ -f .env ]; then
    echo "Existing .env file found."
    read -p "Backup existing file before creating a new one? (y/n): " BACKUP
    if [[ $BACKUP =~ ^[Yy]$ ]]; then
        cp .env .env.backup.$(date +%Y%m%d%H%M%S)
        echo "Backup created."
    fi
fi

# Get battery IP address
read -p "Enter your battery IP address: " BATTERY_IP
while [[ ! $BATTERY_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; do
    echo "Invalid IP address format. Please use format xxx.xxx.xxx.xxx"
    read -p "Enter your battery IP address: " BATTERY_IP
done

# Get Tibber token
read -p "Enter your Tibber API token: " TIBBER_TOKEN
while [[ -z $TIBBER_TOKEN ]]; do
    echo "Tibber token cannot be empty."
    read -p "Enter your Tibber API token: " TIBBER_TOKEN
done

# Create .env file
echo "Creating .env file..."
cat > .env << EOF
# Battery Management System Configuration
# Created: $(date)

# Battery connection settings
BATTERY_HOST=$BATTERY_IP

# Tibber API settings
TIBBER_TOKEN=$TIBBER_TOKEN
EOF

echo ""
echo "Configuration file created successfully!"
echo "Your settings have been saved to the .env file."
echo ""
echo "You can now proceed with the installation:"
echo "1. Run ./install_dependencies.sh"
echo "2. Run ./install_battery_scheduler.sh"
echo "3. Run ./enable_battery_scheduler.sh"