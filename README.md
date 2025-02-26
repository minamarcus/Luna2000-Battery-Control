# Battery Schedule Automation

This package provides tools to automatically run your battery management system using macOS's built-in scheduling system (launchd).

## What This System Does

### Battery Schedule Optimization (`set_battery_schedule.py`)

This program optimizes your home battery usage based on electricity prices:

- **Regular Mode (14:00)**: Creates the optimal charging/discharging schedule for the next day by:
  - Finding cheapest nighttime hours for charging
  - Finding most expensive daytime hours for discharging
  - Comparing prices to determine if current schedule should be kept or replaced
  - Writing the optimized schedule to the battery

- **Evening Mode (17:00)**: Optimizes today's evening schedule by:
  - Checking current battery State of Charge (SOC)
  - Analyzing which evening hours (18:00-22:00) are already covered
  - Comparing evening prices with next day prices
  - Adding additional discharge periods for high-price evening hours when beneficial

### High Usage Monitor (`run_high_usage_monitor.py`)

This service runs continuously to:

- Monitor real-time power consumption via Tibber integration
- Detect periods of high power usage (above configured threshold)
- Automatically switch the battery to "Max Self-Consumption" mode during high usage
- Help avoid power usage spikes and reduce grid dependency during peak consumption
- Automatically return to Time-of-Use (TOU) mode after the high usage period

## Features

- Automatically schedules both components:
  - Battery schedule optimization runs daily at 14:00 and 17:00
  - High usage monitor runs continuously when enabled
- Easy to enable/disable when needed
- Captures all output to log files
- Includes status checking and manual run options

## Installation

1. **Place all files in the same directory as your `set_battery_schedule.py` script**

2. **Make all scripts executable:**
   ```bash
   chmod +x *.sh
   ```

3. **Configure your system parameters:**
   
   Run the configuration helper script:
   ```bash
   ./create_dotenv.sh
   ```
   This will prompt you for:
   - Your battery's IP address (e.g., 192.168.1.100)
   - Your Tibber API token

   These settings are saved in a `.env` file that your battery management system will use.

   Without these parameters configured correctly:
   - The system won't be able to connect to your battery
   - The high usage monitor won't be able to connect to Tibber

4. **Install required Python dependencies:**
   ```bash
   ./install_dependencies.sh
   ```
   This will install all the Python packages needed by your battery management system.

5. **Run the installation script:**
   ```bash
   ./install_battery_scheduler.sh
   ```
   This will:
   - Copy the launch agent files to your LaunchAgents directory
   - Set up log directories
   - Update file paths to match your environment

6. **Enable the scheduler:**
   ```bash
   ./enable_battery_scheduler.sh
   ```

## Usage

### Check scheduler status:
```bash
./status_battery_scheduler.sh
```

### Enable the scheduler:
```bash
./enable_battery_scheduler.sh
```

### Disable the scheduler:
```bash
./disable_battery_scheduler.sh
```

### Run manually:
```bash
./run_battery_schedule.sh
```
Or specify a mode:
```bash
./run_battery_schedule.sh regular
./run_battery_schedule.sh evening
```

## Log Files

Logs are saved to `~/Library/Logs/BatterySchedule/`:
- `regular.log` - Output from regular mode
- `regular_error.log` - Errors from regular mode
- `evening.log` - Output from evening mode
- `evening_error.log` - Errors from evening mode

## How It Works

This setup uses macOS's native `launchd` system to run your script at specific times. The launch agent files define when the scripts run, and the shell scripts make it easy to control the scheduling.

When enabled, the scripts will run automatically at the scheduled times. If your Mac is asleep at the scheduled time, macOS will run the script when the computer wakes up.

## Troubleshooting

If the scripts aren't running as expected:

1. Check the status:
   ```bash
   ./status_battery_scheduler.sh
   ```

2. Ensure the scheduler is enabled:
   ```bash
   ./enable_battery_scheduler.sh
   ```

3. Check the log files for errors:
   ```bash
   cat ~/Library/Logs/BatterySchedule/regular_error.log
   cat ~/Library/Logs/BatterySchedule/evening_error.log
   cat ~/Library/Logs/BatterySchedule/high_usage_monitor_error.log
   ```

4. Check that your configuration is correct:
   - Verify your battery IP address is correct in the .env file
   - Check your Tibber API token is valid
   - Make sure your battery is accessible on the network

5. Try running the script manually to verify it works:
   ```bash
   ./run_battery_schedule.sh
   ```

6. Make sure your Mac isn't in sleep mode during the scheduled times