#!/bin/bash
export DISPLAY=:99

TARGET_BROKER="$1"
if [ -z "$TARGET_BROKER" ]; then
    read -p "Please enter the broker name to search: " TARGET_BROKER
fi

# Check if target broker is still empty
if [ -z "$TARGET_BROKER" ]; then
    echo "Error: Broker name not provided!"
    exit 1
fi

# Capture Window ID (supports Demo, Trial, Live, Real, Netting, Hedge, Account, or MetaTrader titles)
WINDOW_ID=$(xdotool search --onlyvisible --name "Account|Demo|Trial|Live|Real|Netting|Hedge|MetaTrader" | head -n 1)
if [ -z "$WINDOW_ID" ]; then
    # Fallback: pick the first visible window that has a non-empty title
    for id in $(xdotool search --onlyvisible --name "."); do
        wname=$(xdotool getwindowname "$id" 2>/dev/null)
        if [ -n "$wname" ]; then
            WINDOW_ID="$id"
            break
        fi
    done
fi

if [ -z "$WINDOW_ID" ]; then
    echo "Error: MT5 window not found. Please ensure terminal64.exe is running."
    exit 1
fi

echo "Locked MT5 Window ID: $WINDOW_ID ($(xdotool getwindowname "$WINDOW_ID"))"

# 1. Get window geometry and focus inside window to force X11 input focus (avoid windowactivate errors)
eval $(xdotool getwindowgeometry --shell "$WINDOW_ID")
xdotool windowfocus "$WINDOW_ID"
sleep 1

# 2. Send Alt+F to open the File menu, then press 'a' to open the open-account dialog
xdotool key alt+f
sleep 0.8
xdotool key a
sleep 2

# === Fix focus: Press Tab 3 times consecutively to switch focus to search input ===
echo "Switching focus to search box..."
xdotool key Tab
sleep 0.3
xdotool key Tab
sleep 0.3
xdotool key Tab
sleep 0.5

# Select all and clear existing text
xdotool key ctrl+a
sleep 0.2

# 3. Enter broker name and hit Return to start searching
echo "Searching broker: $TARGET_BROKER"
xdotool type --delay 100 "$TARGET_BROKER"
sleep 0.5
xdotool key Return

# 4. Wait for server communication and node download (8-10 seconds recommended)
echo "Waiting for access points and server information to download..."
sleep 10

# 5. Press Escape twice to close the account window and exit
xdotool key Escape
sleep 0.5
xdotool key Escape

echo "=== Checking servers.dat status ==="
# Locate actual servers.dat inside container and list timestamp
find / -name "servers.dat" 2>/dev/null -exec ls -l {} +