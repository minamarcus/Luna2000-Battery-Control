#!/bin/bash

# Define paths
PLIST_DIR=~/Library/LaunchAgents
REGULAR_PLIST="com.battery.schedule.regular.plist"
EVENING_PLIST="com.battery.schedule.evening.plist"
MONITOR_PLIST="com.battery.high-usage-monitor.plist"

# Function to unload a plist file
unload_plist() {
    if [ -f "$PLIST_DIR/$1" ]; then
        echo "Unloading $1..."
        launchctl unload -w "$PLIST_DIR/$1" 2>/dev/null
        STATUS=$?
        if [ $STATUS -eq 0 ]; then
            echo "✅ Successfully disabled $1"
        else
            echo "ℹ️ $1 was not loaded"
        fi
    else
        echo "ℹ️ $1 not found in $PLIST_DIR"
    fi
}

# Unload all plist files
unload_plist "$REGULAR_PLIST"
unload_plist "$EVENING_PLIST"
unload_plist "$MONITOR_PLIST"

echo ""
echo "Battery management system is now DISABLED."
echo "Use enable_battery_scheduler.sh to turn it back on when needed."