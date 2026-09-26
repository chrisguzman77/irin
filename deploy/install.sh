#!/usr/bin/env bash
# deploy/install.sh — Pi install (Chris owns; Slavik runs it at the Pi). STUB: the steps, in order.
#
#  1. Abort unless `uname -m` prints aarch64 (a 32-bit card has no XGBoost wheel).
#  2. Install uv (curl -LsSf https://astral.sh/uv/install.sh | sh) and `uv python install 3.14`.
#  3. sudo apt install -y liblgpio-dev swig build-essential ffmpeg chromium
#  4. `uv venv --python 3.14 .venv` and
#     `uv pip install -r backend/requirements.txt -r hardware/requirements.txt`.
#     3.13 ESCAPE HATCH (only if lgpio will not build in fifteen minutes): on the system 3.13 of
#     Raspberry Pi OS Trixie, `python3 -m venv --system-site-packages .venv` with apt's
#     python3-gpiozero python3-lgpio python3-spidev; nothing else changes (code is 3.13-compatible).
#  5. Enable SPI (raspi-config nonint do_spi 0).
#  6. Add the kiosk user to spi, gpio, audio, video.
#  7. Screen blanking off: raspi-config nonint do_blanking 1.
#  8. Write /etc/udev/rules.d/90-backlight.rules:
#       SUBSYSTEM=="backlight", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness"
#  9. Install irin.service as a systemd --user unit for the kiosk user
#     (~/.config/systemd/user/irin.service), `loginctl enable-linger <user>`, enable + start it.
# 10. Install kiosk.service DISABLED and print:
#       "kiosk autostart is off; enable by hand after /audit"
#     This script NEVER enables kiosk.service. No Claude ever does either.
set -euo pipefail
echo "install.sh is a stub; see the header for the steps." >&2
exit 1
