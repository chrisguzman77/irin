#!/usr/bin/env bash
# The kiosk launcher. Escape hatch: `sudo touch /boot/firmware/NO_KIOSK` (from a tty, SSH, or an
# SD card reader) and this script exits 0 without starting Chromium. `--now` skips the 20 s delay.
# Started by kiosk.service (a --user unit, NEVER enabled by install.sh or any Claude) or by hand.
#
# At boot the user manager can start this before the desktop has registered its Wayland display;
# Chromium would then die with "Missing X server or $DISPLAY" and, with Restart=no, no kiosk would
# ever appear. So after the delay it waits (up to 90 s) for the Wayland socket and starts Chromium
# on it explicitly.
set -u
if [ -e /boot/firmware/NO_KIOSK ]; then
  echo "NO_KIOSK flag present; not starting the kiosk"
  exit 0
fi
if [ "${1:-}" != "--now" ]; then
  sleep 20
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
sock="${WAYLAND_DISPLAY:-wayland-0}"
for _ in $(seq 1 90); do
  [ -S "$XDG_RUNTIME_DIR/$sock" ] && break
  sleep 1
done
if [ -S "$XDG_RUNTIME_DIR/$sock" ]; then
  export WAYLAND_DISPLAY="$sock" XDG_SESSION_TYPE=wayland
  platform=(--ozone-platform=wayland)
else
  echo "no Wayland socket after 90 s; trying Chromium's default display" >&2
  platform=()
fi
[ -e /boot/firmware/NO_KIOSK ] && { echo "NO_KIOSK flag present; not starting the kiosk"; exit 0; }
# --password-store=basic: without it Chromium waits on the desktop keyring's "Unlock" dialog, which
# sits hidden behind the fullscreen kiosk, and the screen stays blank (seen on the Pi, 2026-09-27)
exec chromium "${platform[@]}" --password-store=basic --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble http://localhost:8000/
