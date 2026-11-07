#!/bin/sh
# Start Rocky's dashboard (server/launcher.py) at login, so http://localhost:8766/ is always there and the brain
# server is one click away. macOS: a LaunchAgent for your user. Nothing runs as root.
#
#   server/install_launcher.sh             install (or update) and start it now
#   server/install_launcher.sh --start-brain   same, and also start the brain server whenever the launcher starts
#   server/install_launcher.sh --uninstall     stop it and remove it
#
# Linux: run `python3 server/launcher.py` from your desktop's autostart, or a systemd user service with
#   ExecStart=<repo>/server/.venv/bin/python <repo>/server/launcher.py   and   Restart=on-failure
set -e

LABEL="com.desk-robot.launcher"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(uname)" != "Darwin" ]; then
  echo "This script sets up macOS. On Linux, see the note at the top of $0."
  exit 1
fi

if [ "$1" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed. The dashboard no longer starts at login."
  exit 0
fi

PY="$HERE/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "No server/.venv yet. Set up the brain server first (see the README), then run this again."
  exit 1
fi

START_BRAIN=0
[ "$1" = "--start-brain" ] && START_BRAIN=1
mkdir -p "$HOME/Library/LaunchAgents" "$HERE/debug"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>-u</string><string>$HERE/launcher.py</string></array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict><key>ROCKY_START_BRAIN</key><string>$START_BRAIN</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HERE/debug/launcher.log</string>
  <key>StandardErrorPath</key><string>$HERE/debug/launcher.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart "gui/$(id -u)/$LABEL"   # RunAtLoad doesn't always start it straight away
echo "Installed. The dashboard starts at login: http://localhost:8766/"
[ "$START_BRAIN" = 1 ] && echo "The brain server starts with it."
echo "The first time the brain uses the microphone, macOS asks for permission for Python: allow it."
