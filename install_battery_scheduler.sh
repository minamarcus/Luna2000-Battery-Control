#!/bin/bash

# Create log directory
mkdir -p ~/Library/Logs/BatterySchedule

# Define paths
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PLIST_DIR=~/Library/LaunchAgents
REGULAR_PLIST="com.battery.schedule.regular.plist"
EVENING_PLIST="com.battery.schedule.evening.plist"
MONITOR_PLIST="com.battery.high-usage-monitor.plist"

# Copy and update plist files
if [ -f "$SCRIPT_DIR/$REGULAR_PLIST" ]; then
    cp "$SCRIPT_DIR/$REGULAR_PLIST" "$PLIST_DIR/"
    # Update path to script
    sed -i '' "s|/full/path/to/set_battery_schedule.py|$SCRIPT_DIR/set_battery_schedule.py|g" "$PLIST_DIR/$REGULAR_PLIST"
    # Update username
    sed -i '' "s|YOUR_USERNAME|$USER|g" "$PLIST_DIR/$REGULAR_PLIST"
    echo "Installed $REGULAR_PLIST to $PLIST_DIR"
else
    echo "Error: $REGULAR_PLIST not found in $SCRIPT_DIR"
fi

if [ -f "$SCRIPT_DIR/$EVENING_PLIST" ]; then
    cp "$SCRIPT_DIR/$EVENING_PLIST" "$PLIST_DIR/"
    # Update path to script
    sed -i '' "s|/full/path/to/set_battery_schedule.py|$SCRIPT_DIR/set_battery_schedule.py|g" "$PLIST_DIR/$EVENING_PLIST"
    # Update username
    sed -i '' "s|YOUR_USERNAME|$USER|g" "$PLIST_DIR/$EVENING_PLIST"
    echo "Installed $EVENING_PLIST to $PLIST_DIR"
else
    echo "Error: $EVENING_PLIST not found in $SCRIPT_DIR"
fi

if [ -f "$SCRIPT_DIR/$MONITOR_PLIST" ]; then
    cp "$SCRIPT_DIR/$MONITOR_PLIST" "$PLIST_DIR/"
    # Update path to script
    sed -i '' "s|/full/path/to/run_high_usage_monitor.py|$SCRIPT_DIR/run_high_usage_monitor.py|g" "$PLIST_DIR/$MONITOR_PLIST"
    # Update username
    sed -i '' "s|YOUR_USERNAME|$USER|g" "$PLIST_DIR/$MONITOR_PLIST"
    echo "Installed $MONITOR_PLIST to $PLIST_DIR"
else
    echo "Error: $MONITOR_PLIST not found in $SCRIPT_DIR"
fi

echo "Installation complete! Use enable_battery_scheduler.sh to activate the scheduler."