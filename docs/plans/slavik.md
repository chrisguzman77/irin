# Slavik — Hardware plan (docs/plans/slavik.md)

Read this at the start of any session working hardware/. CLAUDE.md has
the repo rules. Division of labor: all PHYSICAL work is yours by hand
following the spec's build phases; Claude's role is diagnosis and
scripts (paste it the error or the symptom, it reasons, you touch).
Test scripts run by hand on the Pi over SSH, never from a Claude session
on the Pi. You also own the Meta demo VIDEO (a work item, not code; see
the last section).

## Lane

Owns hardware/: hal.py (the boundary file Chris's backend imports;
change its interface only with contracts-level care and a journal
Interface-changes note), leds.py, sound.py, presence.py, mock.py,
scripts/, docs/wiring.md. On a laptop everything runs as mocks
(IRIN_HW=mock); the real requirements.txt installs on the Pi only.
Rounds and Night Buddy ask nothing mandatory of hardware; the optional
items are marked below.

## The circuit (authoritative pin map)

- LED data: Pi GPIO10 / SPI MOSI, physical pin 19 → level shifter
  SN74AHCT125N pin 2 (1A); shifter pin 3 (1Y) → 330 ohm → frame DIN.
- Shifter pin 14 → 5V rail, pins 1 and 7 → ground rail.
- Common ground: Pi physical pin 20 → ground rail.
- LED power: ALITOVE 5V 5A → screw terminal; terminal + → 5V rail and
  frame 5V; terminal − → ground rail and frame GND. Rubycon 1000 uF cap
  across + and −, stripe leg to minus.
- INVARIANT: the strip's 5V comes ONLY from the ALITOVE, never the Pi.
- Radar (LD2410B-P): the adapter's 1.25mm connector presses onto the
  board's five 1.27mm-pitch header pins (keyed, one way);
  VCC → physical pin 4 (5V), GND → physical pin 9, OUT → GPIO17
  (physical pin 11). OUT is 3.3V logic, no shifter. Tape off the two
  unused UART wires; photograph wire colors into wiring.md (adapter
  colors are not standardized).
- Unused shifter pins: ground 2OE, 2A, 3A, 3OE, 4A, 4OE (pins 4, 5, 9,
  10, 12, 13); leave outputs 6, 8, 11 open. Floating CMOS inputs
  oscillate and heat the chip. On the mini breadboard (chip over cols
  6-12, notch left): pins 4 and 5 are cols 9 and 10 bottom, one jumper
  each to the ground node (col 16 bottom, or col 12 bottom which pin 7
  already grounds); pins 9, 10, 12, 13 are cols 11, 10, 8, 7 top, and col
  16 top is the 5V node, so bridge ground up first (col 16 bottom → col
  15 top) and jumper col 15 top to each of those four columns. Seven
  jumpers. Never touch col 6 top (pin 14, 5V).
- Strip leads: 5V and GND go into the screw terminal as bare stranded
  wire, twisted and tug-tested. The data lead is the only wire that
  lives in a breadboard hole: a factory-tinned pigtail end goes straight
  in and tug-tests; a bare or frayed end is joined to a solid-core
  jumper with a Wago 221 lever connector (or a tight twist under tape),
  never soldered. No soldering iron is needed anywhere in the build.
- Barrel fit: the ALITOVE plug is 5.5 x 2.5 mm, most screw-terminal jacks
  are 5.5 x 2.1 mm. If the plug wobbles, cut it off and screw the bare
  leads in (multimeter first: center conductor is +).
- Polarity: before first power, with only the ALITOVE on the terminal,
  the multimeter reads about +5.0 V on the screw marked +. Negative means
  wrong; nothing else gets power until it reads right. Borrow a
  multimeter (it is also the only way to see 5V at the far end of the
  strip or find a bad corner); the no-meter fallback for polarity is the
  kit's LED with a 330 ohm resistor in series across the screws, long
  leg on +, which lights only if + is really +.
- Radar VCC: pin 4 only if the display's GPIO power plug housing (pins 2
  and 6) leaves it reachable; otherwise the breadboard's 5V node (col 16
  top). Grounds are common either way.
- GPIO17: internal pull-down configured in presence.py, so an unplugged
  or dead radar reads low, never floats. Read it with gpiozero (lgpio
  backend); RPi.GPIO does NOT work on Pi 5.
- Orientation: USB ports pointing right, pin 1 = bottom-left square pad.
- Speaker: LIELONGREN USB into a Pi USB-A port. It must be a true USB
  audio device (aplay -l lists it); if it is USB-powered with a 3.5 mm
  input only, the Pi 5 has no jack and a USB sound dongle is needed.
- SPI driver: Pi5Neo or Blinka SPI NeoPixel. rpi_ws281x does NOT work
  on Pi 5.

## Build order (each step names its check)

1. **mock.py + hal.py interface** (day one, so Chris develops against
   mocks immediately): set_leds(state/color/brightness), play_sound,
   stop_sound, get_presence() -> bool | None, set_display_brightness,
   plus a mock presence you can drive from tests (set_presence_for_test).
   The mock's get_presence() returns None until driven, meaning "no
   value": the backend reads that as unknown, never as present or
   absent; tests and the demo panel drive it explicitly. The real reader
   returns True/False and None only on a read error. Rounds samples this
   RAW value every 30 s during alarms (presence_during), so the reader
   must always report the truth the radar sees, never the backend's
   Home/Away state. Check: backend boots with IRIN_HW=mock on a laptop.
2. **leds.py:** ambient color at low brightness, off at night, amber
   ramp (predicted-low warning), red full pulse (actual low), strobe
   (escalation). Colors configurable but warning and full must stay
   visually distinct. Optional, later tiers, never before core works:
   message_waiting (a soft daytime color for a pending doctor message;
   never at night, never during any alarm) and buddy_alert (a distinct
   pattern meaning "not your low, your buddy's", for the both-devices
   tier and the video). Both must be impossible to confuse with the
   warning and full tiers. Check: scripts/first_light.py lights 3 LEDs at
   10 percent; scripts/full_frame_test.py runs a color cycle at 30
   percent while you wiggle both corner connectors, no flicker.
3. **sound.py:** tone playback with software volume ramp; low alarms
   must be able to reach full volume regardless of quiet-hour settings
   (the engine decides, the driver must not cap). Sound names are a
   small fixed set the backend refers to by name: alarm_soft (warning),
   alarm_urgent (full), chirp (high / stale), and from the Night Buddy
   tier buddy_chime (the brokered-call chime, distinct from every alarm
   tone); the files come from backend/sounds/make_tones.py (Chris). The
   driver plays through the kiosk user's PipeWire session, which only
   works because the backend runs as that user (see Pi setup notes); do
   not open ALSA hw: devices directly, Chromium holds them. Check:
   scripts/speaker_test.sh audible end to end, then the same tone from
   the running service.
4. **presence.py:** read GPIO17 with gpiozero (lgpio backend; RPi.GPIO
   does not work on Pi 5), pull_up=False so the pin has the internal
   pull-down, with a short bounce_time as the debounce; return None on a
   read error, never a guess. Report raw
   presence/no-presence with timestamps; the Away decision lives in the
   backend (about 15 min sustained absence, outside the night window
   only, manual toggle always wins). Mounting: antenna face (the side
   with the flat patch-antenna pattern) toward the bed, nothing metal in
   front (not the aluminum channels, not the display backplate).
   Preferred: the SmartiPi camera mount beneath the display (open bottom
   of the U-frame) if the 7 x 35mm board fits the cradle; otherwise a
   3D-printed LD2410B housing (plain PLA/PETG, no carbon-fiber or
   metal-filled filament) taped to the front below the screen. Datasheet
   rule: open window over the antenna, or 12.4-18.6mm between antenna
   and any cover. Rigidly fixed (a wobbling radar reads its own motion),
   a few cm from the cooler fan. Identify wires by the board's printed
   labels, never by color; extend short wires with M-F Dupont jumpers,
   joints taped. Retest at the display tilt you'll actually use. Calibrate distance in the HLK Bluetooth
   app. Range is set in gates 0.75m (~2.5 ft) deep: max gate n reaches
   (n + 1) x 0.75m, so gate 1 = 1.5m (~5 ft), gate 2 = 2.25m (~7.4 ft),
   gate 3 = 3.0m (~9.8 ft). The radar stores the setting itself.
   Two profiles:
   - HOME: smallest gate covering the sleeper's chest from the
     nightstand (usually 2 or 3), so the hallway doesn't count.
   - DEMO: gate 1 (~5 ft), set at table setup so a crowded room
     doesn't hold presence on; walk the boundary with the real crowd
     before the first judge. Switch back to HOME after the event. The
     same profile must read a judge standing at the table as present
     during the Rounds pairing beat and the Night Buddy beat (both rely
     on presence = present); confirm it with a person at arm's length
     from the device.
   Set the no-one duration to a few seconds; the backend applies its
   own longer Away rule. Check: `watch -n 0.5 pinctrl get 17` holds
   `hi` with a motionless person present and drops to `lo` shortly
   after the room (or the demo zone) is empty.
5. **wiring.md + figures:** hardware/docs/figures/ holds the ten spec
   diagrams (fig01_pi5_landmarks through fig10_screw_terminal) and your
   radar_wiring_*.jpg photos of which adapter wire color went to which
   pin. Any deviation from the pin map above gets written back into
   wiring.md the same day, or the doc becomes fiction.

## Pi setup notes (things a fresh Pi OS gets wrong; do these in Phase 4)

These are deploy/install.sh's job (Chris owns deploy/), but you are the
one at the Pi, so run them and tick them off. Each has a check.

1. **Screen blanking off:** `sudo raspi-config nonint do_blanking 1`
   (menu: Display Options → Screen Blanking → No). Night mode dims via
   the backlight, never via blanking. Check: kiosk still lit after 15
   minutes idle.
2. **Backlight writable without root:** hal's set_display_brightness
   writes /sys/class/backlight/<name>/brightness. Udev rule in
   /etc/udev/rules.d/90-backlight.rules: `SUBSYSTEM=="backlight",
   RUN+="/bin/chgrp video /sys/class/backlight/%k/brightness",
   RUN+="/bin/chmod g+w /sys/class/backlight/%k/brightness"`. Check:
   `echo 20 > /sys/class/backlight/*/brightness` as the kiosk user dims
   the panel.
3. **Service user, not root:** the backend runs as the kiosk user as a
   systemd --user unit (~/.config/systemd/user/irin.service) with
   `loginctl enable-linger <user>`, so it shares the session's PipeWire
   with Chromium. The user is in groups spi, gpio, audio, video. Check:
   the alarm tone plays from the service, not just from a terminal.
4. **GPIO libraries:** gpiozero + lgpio for GPIO17, Pi5Neo or Blinka for
   SPI; RPi.GPIO and rpi_ws281x both fail on Pi 5. Enable SPI in
   raspi-config. Check: hardware/scripts/radar_watch.py prints
   transitions as the kiosk user.
5. **Touch rotation:** rotate to landscape in the Screen Configuration
   tool of Raspberry Pi OS Trixie (Debian 13, 64-bit, the release the
   card is flashed with: its system Python is the 3.13 the escape hatch
   needs), which rotates the image and the touch matrix
   together; if touch still lands rotated, set a libinput calibration
   matrix (udev rule, ENV{LIBINPUT_CALIBRATION_MATRIX}) rather than
   rotating inside Chromium. Budget an hour. Check: a tap lands where
   the finger is, in all four corners.
6. **No RTC battery:** the Pi 5 only knows the time after NTP syncs.
   Always boot with the network up (hotspot on before the Pi at the
   venue). Check: `timedatectl` says "System clock synchronized: yes"
   before the demo starts. The backend guards the rest (chris.md step
   10).
7. **Kiosk autostart OFF until the end.** Do not enable kiosk.service
   and do not add Chromium to any autostart file; the Pi boots to the
   normal desktop for the whole build. To look at the display page, open
   it in a normal Chromium window; to see the real kiosk for a test, run
   `deploy/kiosk.sh --now` and close it with Alt+F4. Chris enables
   autostart by hand after the final audit. If you ever find the Pi in
   kiosk mode when you need the desktop: Ctrl+Alt+F2 for a text console,
   `sudo touch /boot/firmware/NO_KIOSK`, reboot; the flag file can also
   be created from any computer with the SD card in a reader, and SSH
   from the laptop works underneath the kiosk at all times. Check:
   `systemctl --user is-enabled kiosk` prints disabled until judging
   eve.
8. **Prove the stack on the Pi on day one.** Right after Phase 2's
   bench boot: `uname -m` must print aarch64 (a 32-bit card has no
   XGBoost wheel and fails hours later, the way last year's local model
   did). The project's Python is 3.14, which Raspberry Pi OS does not
   ship: install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`),
   `uv python install 3.14`, clone the repo, `uv venv --python 3.14
   .venv`, `sudo apt install liblgpio-dev swig build-essential ffmpeg`,
   `uv pip install -r backend/requirements.txt -r
   hardware/requirements.txt`, `pytest` from backend/, and boot
   `IRIN_HW=mock DATASOURCE=replay uvicorn app.main:app --host 0.0.0.0`
   with the display page opened from the laptop. Sound stays
   dependency-free: sound.py shells out to aplay or pw-play rather than
   pulling in pygame. Check: pytest green and the replay number moving
   on a browser pointed at the Pi, before any feature is written.

## The Meta video (yours regardless of which Night Buddy tier ships)

The Meta challenge requires a 2-3 minute demo video and it is the piece
of the Meta entry that always exists, so it is scheduled, not improvised.
The arc, from the Night Buddy spec: 0:00 the problem in first person
(Chris, alone, phone on the nightstand; one line on Dexcom Follow) →
0:30 the promise (matched with a stranger, the AI introduction, a week
of five-second mornings: "All quiet for both of you") → 1:00 the night
it matters, split screen (left: the device escalating in a DARK room,
nobody stirring, the radar dot showing someone present; right: a phone
across the world lighting up in daylight; the claim, the brokered call,
the dark room filling with ringtone and chime, movement, juice) → 1:45
the morning after on both screens ("Sam's okay. Your call at 3:12 woke
him; he treated and recovered.") → 2:00 the hub, labeled CONCEPT →
2:20 the close: "AI does not replace the person who checks on you. It
finds them, and it knows when to wake them." Family Story gets its own
~20 s beat (docs/FAMILY_STORY.md), best placed right after the morning
shot: split screen, a phone showing a raw CGM-follow graph with a
worried face versus Mom's phone showing the story; line: "Same night.
Two ways to find out your son is okay."; then the patient's morning
screen, "Sent to Mom", and the pause button. Trim elsewhere to stay
under three minutes.

- The second "device" is the mock HAL on a laptop (IRIN_HW=mock, display
  page fullscreen). If Night Buddy ships video-only, every screen in the
  film is staged with mocks and labeled concept; if the demo tier ships,
  film the real code paths with DEMO badges visible.
- Film the dark-room scene with the real device and the real LED frame:
  it is the most filmable thing the team owns, and the same footage is
  the backup demo video the packing list wants on the laptop and phone.
- Raw footage stays out of the repo (upload elsewhere; the link goes in
  docs/META_WRITEUP.md). Deliver an MP4 under 3 minutes.
- Split-screen staging is worked out with Justin (his B3 step) so the
  live beat and the film match.
  Check: a rough cut exists before the final evening, so re-shoots are
  still possible.

## Physical rules

Power off before rewiring. Pre-power checks before first light: frame
5V only from the screw terminal, capacitor stripe to minus, chip notch
matches pin numbering, unused shifter pins grounded, the multimeter
reads +5.0 V on the + screw with only the ALITOVE connected. Wiggle test passes BEFORE any adhesive (the 3M
5925 tape is one-shot). Drill only the plastic back cover, never the
case. Presence gating happens in the hal layer and suppresses outputs
only; never put presence checks into alarm logic. The optional LED
states above never fire during any alarm and never at night.

## Added 2026-09-25 evening (the voice, Python 3.14, the video beats)

- Python 3.14 on the Pi comes from uv, not apt (chris.md "Pi deploy"):
  the day-one stack proof installs uv, `uv python install 3.14`, `uv venv
  --python 3.14`, `sudo apt install liblgpio-dev swig build-essential
  ffmpeg`, then the two requirements files. If lgpio will not build in
  fifteen minutes, say so in the journal and use the system 3.13 escape
  hatch; do not spend the evening on it.
- ffmpeg is a Pi dependency now: ElevenLabs clips arrive as mp3 and are
  converted to wav for aplay. Phase 9's audio check gains one item: a
  spoken echo ("30 carbs and 4 units, save?") plays through the soundbar
  from the service, after the tone, never instead of it.
- The video gains two beats: the buddy's phone speaking "Your buddy is in
  trouble" on a loop in the daylight half of the split screen (the same
  text and clip landing there as a WhatsApp message, decision 29), and
  Mom pressing play on a 20-second voice note in the Family Story shot
  (in demo mode that clip is rendered locally on the morning screen,
  never emailed, so the shot repeats on demand). Film the dark-room
  device scene first, as before.
