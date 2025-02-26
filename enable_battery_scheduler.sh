#!/bin/bash

# Define paths
PLIST_DIR=~/Library/LaunchAgents
REGULAR_PLIST="com.battery.schedule.regular.plist"
EVENING_PLIST="com.battery.schedule.evening.plist"
MONITOR_PLIST="com.battery.high-usage-monitor.plist"

# Function to load a plist file
load_plist() {
    if [ -f "$PLIST_DIR/$1" ]; then
        echo "Loading $1..."
        launchctl load -w "$PLIST_DIR/$1"
        STATUS=$?
        if [ $STATUS -eq 0 ]; then
            echo "✅ Successfully enabled $1"
        else
            echo "❌ Failed to enable $1"
        fi
    else
        echo "❌ Error: $1 not found in $PLIST_DIR"
        echo "   Run install_battery_scheduler.sh first"
    fi
}

# Ask about enabling high usage monitor
echo "Do you want to enable the high usage monitor service?"
echo "This continuously monitors power usage and switches battery modes during high consumption."
echo "1) Yes, enable high usage monitor"
echo "2) No, only enable scheduled tasks"
read -p "Enter choice (1/2): " MONITOR_CHOICE

# Load scheduler plist files
load_plist "$REGULAR_PLIST"
load_plist "$EVENING_PLIST"

# Load monitor if requested
if [ "$MONITOR_CHOICE" = "1" ]; then
    load_plist "$MONITOR_PLIST"
    MONITOR_STATUS="ENABLED"
else
    MONITOR_STATUS="DISABLED"
fi

echo ""
echo "Battery management system is now configured:"
echo "- Schedule optimizer: ENABLED (14:00 and 17:00 daily)"
echo "- High usage monitor: $MONITOR_STATUS"
echo ""
echo "Use disable_battery_scheduler.sh to turn it off when needed."
echo "Use status_battery_scheduler.sh to check current status."