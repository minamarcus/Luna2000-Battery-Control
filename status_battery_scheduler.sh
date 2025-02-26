#!/bin/bash

# Define labels
REGULAR_LABEL="com.battery.schedule.regular"
EVENING_LABEL="com.battery.schedule.evening"
MONITOR_LABEL="com.battery.high-usage-monitor"

# Function to check status of a scheduler
check_status() {
    LABEL=$1
    SCHEDULE_TYPE=$2
    
    # Check if service is loaded
    launchctl list | grep -q "$LABEL"
    if [ $? -eq 0 ]; then
        echo "✅ $SCHEDULE_TYPE scheduler is ENABLED"
        
        # Get next run time
        NEXT_RUN=$(launchctl list "$LABEL" 2>/dev/null | grep "next exit" | awk '{print $4, $5, $6, $7, $8}')
        if [ -n "$NEXT_RUN" ]; then
            echo "   Next run: $NEXT_RUN"
        else
            echo "   Will run at ${3}:00 daily"
        fi
        
        # Check for recent logs
        LOG_FILE=~/Library/Logs/BatterySchedule/${2,,}.log
        if [ -f "$LOG_FILE" ]; then
            LAST_RUN=$(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$LOG_FILE")
            echo "   Last log update: $LAST_RUN"
            
            # Show last log entry
            echo "   Last log entry:"
            echo "   ------------------------------"
            tail -n 1 "$LOG_FILE" | sed 's/^/   /'
            echo "   ------------------------------"
        else
            echo "   No log file found yet"
        fi
    else
        echo "❌ $SCHEDULE_TYPE scheduler is DISABLED"
    fi
    echo ""
}

echo "=== Battery Management System Status ==="
echo ""
echo "Schedule Optimizer:"
check_status "$REGULAR_LABEL" "Regular" "14"
check_status "$EVENING_LABEL" "Evening" "17"

echo "High Usage Monitor:"
check_status "$MONITOR_LABEL" "Monitor" "continuous"

echo "To enable:  ./enable_battery_scheduler.sh"
echo "To disable: ./disable_battery_scheduler.sh"