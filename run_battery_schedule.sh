#!/bin/bash

# Define paths
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_DIR=~/Library/Logs/BatterySchedule

# Create log directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Function to run the script with specified mode
run_schedule() {
    MODE=$1
    
    echo "Running battery schedule in $MODE mode..."
    echo "Output will be saved to $LOG_DIR/${MODE,,}.log"
    echo "Errors will be saved to $LOG_DIR/${MODE,,}_error.log"
    
    # Run the script in the specified mode
    python3 "$SCRIPT_DIR/set_battery_schedule.py" --mode "$MODE" > "$LOG_DIR/${MODE,,}.log" 2> "$LOG_DIR/${MODE,,}_error.log"
    
    # Check if the script ran successfully
    if [ $? -eq 0 ]; then
        echo "✅ Success! Schedule updated in $MODE mode"
        echo "Latest log entry:"
        echo "--------------------------"
        tail -n 5 "$LOG_DIR/${MODE,,}.log"
        echo "--------------------------"
    else
        echo "❌ Error updating schedule in $MODE mode"
        echo "Error log:"
        echo "--------------------------"
        cat "$LOG_DIR/${MODE,,}_error.log"
        echo "--------------------------"
    fi
}

# Check for command line arguments
if [ "$1" = "regular" ] || [ "$1" = "evening" ]; then
    run_schedule "$1"
else
    echo "Battery Schedule Manual Runner"
    echo ""
    echo "Usage: $0 [regular|evening]"
    echo ""
    echo "Please select a mode:"
    echo "1) Regular mode (normally runs at 14:00)"
    echo "2) Evening mode (normally runs at 17:00)"
    echo "3) Run both modes sequentially"
    echo "4) Start high usage monitor (normally runs continuously)"
    echo "q) Quit"
    echo ""
    read -p "Enter your choice: " CHOICE
    
    case $CHOICE in
        1)
            run_schedule "regular"
            ;;
        2)
            run_schedule "evening"
            ;;
        3)
            run_schedule "regular"
            echo ""
            echo "============================"
            echo ""
            run_schedule "evening"
            ;;
        4)
            echo "Starting high usage monitor in a new terminal window..."
            echo "Press Ctrl+C in that window to stop the monitor."
            osascript -e 'tell application "Terminal" to do script "cd \"'$SCRIPT_DIR'\" && python3 run_high_usage_monitor.py"'
            echo "Monitor started in a new terminal window."
            ;;
        q|Q)
            echo "Exiting..."
            exit 0
            ;;
        *)
            echo "Invalid choice. Exiting."
            exit 1
            ;;
    esac
fi