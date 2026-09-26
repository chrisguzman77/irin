#!/usr/bin/env bash
# deploy/install.sh — Pi install. Run as the kiosk user (never root) from the repo:
#     bash deploy/install.sh
# Idempotent: every step checks first and only acts (or asks for sudo) when something
# is missing, so it is safe to re-run after every pull. Steps, in order:
#  1. Abort unless `uname -m` prints aarch64 (a 32-bit card has no XGBoost wheel).
#  2. uv, and `uv python install 3.14`.
#  3. apt: liblgpio-dev swig build-essential ffmpeg chromium.
#  4. `uv venv --python 3.14 .venv` + both requirements files.
#     3.13 ESCAPE HATCH (only if lgpio will not build in fifteen minutes): on the system 3.13
#     of Raspberry Pi OS Trixie, `python3 -m venv --system-site-packages .venv` with apt's
#     python3-gpiozero python3-lgpio python3-spidev; nothing else changes.
#  5. SPI on (raspi-config nonint do_spi 0).
#  6. The kiosk user in spi, gpio, audio, video.
#  7. Screen blanking off (raspi-config nonint do_blanking 1).
#  8. /etc/udev/rules.d/90-backlight.rules (group video, g+w on the brightness file).
#  9. irin.service as a systemd --user unit, linger on, enabled + (re)started.
# 10. kiosk.service installed, its enable state NEVER changed here:
#       "kiosk autostart is off; enable by hand after /audit"
#     This script NEVER enables kiosk.service. No Claude ever does either.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ME="$(id -un)"
UNIT_DIR="$HOME/.config/systemd/user"
export PATH="$HOME/.local/bin:$PATH"

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
die()  { printf '\nSTOP: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "run as the kiosk user, not root (the backend must share that user's PipeWire; sudo is used only where needed)"

say "1. architecture"
[ "$(uname -m)" = "aarch64" ] || die "uname -m is $(uname -m), not aarch64: flash the 64-bit Raspberry Pi OS"
ok "aarch64"

say "2. uv + Python 3.14"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "$(uv --version)"
uv python install 3.14 >/dev/null
ok "python 3.14 available to uv"

say "3. apt packages"
missing=()
for p in liblgpio-dev swig build-essential ffmpeg chromium; do
  dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "   installing: ${missing[*]} (sudo)"
  sudo apt-get update -q && sudo apt-get install -y "${missing[@]}"
fi
ok "liblgpio-dev swig build-essential ffmpeg chromium"

say "4. venv + requirements"
cd "$REPO"
[ -x .venv/bin/python ] || uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -q -r backend/requirements.txt -r hardware/requirements.txt
ok ".venv $(.venv/bin/python --version 2>&1)"

say "5. SPI"
if [ ! -e /dev/spidev0.0 ]; then
  sudo raspi-config nonint do_spi 0
  echo "   SPI enabled: REBOOT before the LEDs work"
fi
ok "SPI"

say "6. groups"
for g in spi gpio audio video; do
  if ! id -nG "$ME" | tr ' ' '\n' | grep -qx "$g"; then
    sudo usermod -aG "$g" "$ME"
    echo "   added $ME to $g (log out and back in, or reboot)"
  fi
done
ok "$ME in spi gpio audio video"

say "7. screen blanking"
if [ "$(raspi-config nonint get_blanking 2>/dev/null || echo 0)" != "1" ]; then
  sudo raspi-config nonint do_blanking 1
fi
ok "blanking off"

say "8. backlight udev rule"
RULE=/etc/udev/rules.d/90-backlight.rules
need_rule=0
for b in /sys/class/backlight/*/brightness; do
  [ -e "$b" ] && [ ! -w "$b" ] && need_rule=1
done
# Only the OUTCOME matters: Raspberry Pi OS Trixie already makes brightness
# group-video writable, so the rule is written only when that is not the case.
if [ "$need_rule" = 1 ] && [ ! -f "$RULE" ]; then
  echo 'SUBSYSTEM=="backlight", RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness", RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness"' | sudo tee "$RULE" >/dev/null
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=backlight
fi
for b in /sys/class/backlight/*/brightness; do
  [ -e "$b" ] || continue
  [ -w "$b" ] && ok "$b writable by $ME" || echo "   WARNING: $b not writable yet (reboot, or check the rule)"
done

say "9. irin.service (systemd --user, linger)"
[ -f "$REPO/.env" ] || die "$REPO/.env missing: copy .env.example and fill in the Pi lines first"
mkdir -p "$UNIT_DIR"
install -m 644 "$REPO/deploy/irin.service" "$UNIT_DIR/irin.service"
if [ "$(loginctl show-user "$ME" -p Linger --value 2>/dev/null)" != "yes" ]; then
  loginctl enable-linger "$ME" 2>/dev/null || sudo loginctl enable-linger "$ME"
fi
ok "linger $(loginctl show-user "$ME" -p Linger --value)"
systemctl --user daemon-reload
systemctl --user enable irin.service >/dev/null 2>&1
systemctl --user restart irin.service
for _ in $(seq 1 30); do
  curl -sf http://localhost:8000/api/health >/dev/null && break
  sleep 1
done
curl -sf http://localhost:8000/api/health >/dev/null \
  && ok "irin.service $(systemctl --user is-active irin) as $(ps -o user= -p "$(systemctl --user show irin -p MainPID --value)"): $(curl -s http://localhost:8000/api/health)" \
  || die "irin.service did not answer on :8000; see: journalctl --user -u irin -n 50"

say "10. kiosk.service (installed, enable state untouched)"
chmod +x "$REPO/deploy/kiosk.sh"
install -m 644 "$REPO/deploy/kiosk.service" "$UNIT_DIR/kiosk.service"
systemctl --user daemon-reload
state="$(systemctl --user is-enabled kiosk.service 2>/dev/null || true)"
if [ "$state" = "enabled" ]; then
  echo "   kiosk autostart is ON (enabled by hand); leaving it as it is"
else
  echo "   kiosk autostart is off; enable by hand after /audit"
fi

say "done"
