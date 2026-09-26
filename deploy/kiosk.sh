#!/usr/bin/env bash
# The kiosk launcher. Escape hatch: `sudo touch /boot/firmware/NO_KIOSK` (from a tty, SSH, or an
# SD card reader) and this script exits 0 without starting Chromium. `--now` skips the 20 s delay.
set -u
if [ -e /boot/firmware/NO_KIOSK ]; then
  echo "NO_KIOSK flag present; not starting the kiosk"
  exit 0
fi
if [ "${1:-}" != "--now" ]; then
  sleep 20
fi
exec chromium --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble http://localhost:8000/
