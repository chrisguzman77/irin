#!/usr/bin/env bash
# Slavik step 3 check. Run BY HAND on the Pi, as the kiosk user (the user
# whose desktop session owns PipeWire), from anywhere:
#   bash hardware/scripts/speaker_test.sh
# Pass = you hear 1) the raw soft tone, 2) the driver's urgent tone ramping up
# and looping for ~6 s then stopping, 3) one chirp. Then the second half of the
# plan's check, by hand: the same tone from the running service (see the end).
set -u
cd "$(dirname "$0")/../.."
PY=.venv/bin/python

echo "== 1. is the USB speaker a real audio device?"
if aplay -l | grep -i usb; then echo "ok: USB audio card listed"; else
  echo "FAIL: no USB card in 'aplay -l' (a 3.5 mm-only speaker needs a USB sound dongle on the Pi 5)"; exit 1; fi

echo "== 2. PipeWire sees it?"
if command -v wpctl >/dev/null; then wpctl status | sed -n '/Sinks:/,/Sources:/p'; fi
command -v pw-play >/dev/null || echo "note: pw-play missing, the driver falls back to aplay"

echo "== 3. raw file through PipeWire (alarm_soft, ~2 s)"
if command -v pw-play >/dev/null; then pw-play backend/sounds/alarm_soft.wav; else aplay -q backend/sounds/alarm_soft.wav; fi

echo "== 4. through the driver: alarm_urgent at full volume, ramped, looping ~6 s"
IRIN_HW=real $PY - <<'EOF'
import sys, time
sys.path.insert(0, ".")
from hardware import sound
sound.play("alarm_urgent", 1.0)
time.sleep(6)
sound.stop()
time.sleep(0.3)
sound.play("chirp", 0.6)
time.sleep(1)
print("driver ok")
EOF

cat <<'EOF'
== 5. by hand: the same tone from the running service
   Start the backend as a systemd --user service (Pi setup note 3), then in
   replay let The Save reach its low, or trigger any alarm from the demo panel.
   Pass = the tone plays from the service, not only from this terminal.
   Silent from the service but fine here = the service is not in the kiosk
   user's session (loginctl enable-linger <user>; run as that user, not root).
EOF
