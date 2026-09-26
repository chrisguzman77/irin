# Wiring (the authoritative pin map; copied from docs/plans/slavik.md)

Figures: `docs/figures/fig01_pi5_landmarks.png` … `fig10_screw_terminal.png`
(the ten spec diagrams). Slavik adds `radar_wiring_*.jpg` photos of which
adapter wire color went to which pin. Any deviation from this map is
written back here the same day.

- **LED data:** Pi GPIO10 / SPI MOSI, physical pin 19 → level shifter SN74AHCT125N pin 2 (1A); shifter pin 3 (1Y) → 330 Ω → frame DIN. (fig07, fig08, fig09)
- **Shifter power:** pin 14 → 5V rail; pins 1 and 7 → ground rail.
- **Common ground:** Pi physical pin 20 → ground rail.
- **LED power:** ALITOVE 5V 5A → screw terminal; terminal + → 5V rail and frame 5V; terminal − → ground rail and frame GND. Rubycon 1000 µF cap across + and −, stripe leg to minus. (fig10)
- **INVARIANT:** the strip's 5V comes ONLY from the ALITOVE, never the Pi.
- **Radar (LD2410B-P):** adapter 1.25 mm connector onto the board's five 1.27 mm header pins (keyed); VCC → physical pin 4 (5V), GND → physical pin 9, OUT → GPIO17 (physical pin 11), 3.3 V logic, no shifter. Tape off the two unused UART wires. Radar VCC falls back to the breadboard's 5V node (col 16 top) if the display's power plug housing blocks pin 4.
- **Unused shifter pins:** ground 2OE, 2A, 3A, 3OE, 4A, 4OE (pins 4, 5, 9, 10, 12, 13); leave outputs 6, 8, 11 open. Floating CMOS inputs oscillate. On the mini breadboard (chip over cols 6-12, notch left): pins 4 and 5 are cols 9 and 10 bottom → ground node (col 16 bottom, or col 12 bottom); pins 9, 10, 12, 13 are cols 11, 10, 8, 7 top; col 16 top is the 5V node, so bridge ground up first (col 16 bottom → col 15 top) and jumper col 15 top to those four columns. Seven jumpers. Never touch col 6 top (pin 14, 5V). (fig09)
- **Strip leads:** 5V and GND into the screw terminal as bare stranded wire, twisted and tug-tested. The data lead is the only wire in a breadboard hole: a tinned pigtail straight in, or a bare end joined to a solid-core jumper with a Wago 221; never soldered. (fig05, fig06)
- **Barrel fit:** ALITOVE plug is 5.5 × 2.5 mm; most jacks are 5.5 × 2.1 mm. If it wobbles, cut it off and screw the bare leads in (multimeter first: center conductor is +).
- **Polarity check:** with only the ALITOVE on the terminal the multimeter reads about +5.0 V on the + screw. Negative means wrong; nothing else gets power until it reads right.
- **GPIO17:** internal pull-down set in presence.py (gpiozero, lgpio backend; RPi.GPIO does not work on Pi 5).
- **Orientation:** USB ports pointing right, pin 1 = bottom-left square pad. (fig01, fig07)
- **Speaker:** LIELONGREN USB into a Pi USB-A port; must be a true USB audio device (`aplay -l` lists it).
- **SPI driver:** Pi5Neo or Blinka SPI NeoPixel; rpi_ws281x does NOT work on Pi 5.
- **Display:** Pi on the display standoffs, DSI ribbon seated (fig02, fig03); display power from the GPIO header power kit or the alternative (fig04).

## As built (2026-09-26)

What is actually on the bench. Where this differs from the map above, this
section is the truth. Mini breadboard: columns numbered left to right, rows
A-E top half and F-J bottom half; chip over cols 6-12, notch left, so pin 1
= col 6 bottom, pin 7 = col 12 bottom, pin 8 = col 12 top, pin 14 = col 6
top. Col 16 top (A-E) is the 5V node, col 16 bottom (F-J) the ground node.

| Wire | From | To | Carries |
|---|---|---|---|
| red jumper | C6 (chip pin 14) | C16 | chip 5V |
| black jumper | G12 (chip pin 7) | G16 | chip ground |
| black jumper | I6 (chip pin 1, 1OE) | I12 | enable, grounded via pin 7 |
| green F-M | Pi pin 19 (GPIO10) | G7 (chip pin 2, 1A) | LED data 3.3V |
| 330 Ω resistor | H8 (chip pin 3, 1Y) | H13 | LED data 5V |
| black F-M | Pi pin 20 | J16 | common ground |
| 1000 µF capacitor | long leg D16 | stripe leg F16 | smoothing |
| ALITOVE + (bare, taped) | supply | E16 | LED 5V in |
| ALITOVE − | supply | H16 | LED ground in |
| strip 5V (red) | strip input | B16 | strip power |
| strip GND (white) | strip input | I16 | strip ground |
| strip DIN (green) | strip input | J13 | LED data 5V |
| radar VCC | radar | A16 | radar 5V |
| radar GND | radar | Pi pin 9 | ground |
| radar OUT | radar | Pi pin 11 (GPIO17) | presence 3.3V |
| display power | Pi pins 2 + 6 | display J1 | display 5V (plug covers pin 4) |

Col 16 is full: A-E and F-J all used.

Deviations from the map, and why:

- **No screw terminal; the strip's power runs through the breadboard.**
  ALITOVE + and − and the strip's 5V and GND all land in col 16. Breadboard
  contacts are good for about 1 A; the full-white 29-LED frame draws about
  1.7 A. Consequence for software: leds.py caps total frame current (step
  2). Preferred fix, no solder: ALITOVE + and strip 5V joined in one Wago
  221 with a single jumper to E16, the same for ground to H16, so the strip
  current never crosses the breadboard. Update this table if that is done.
- **Capacitor on the breadboard (D16 / F16), not the screw terminal.**
  Polarity is correct (long leg on 5V, stripe on ground). Electrically fine.
- **Radar VCC from the breadboard 5V node (A16), not Pi pin 4:** the
  display's power plug covers pin 4. This is the fallback the map allows.
  Radar ground still goes to Pi pin 9; grounds are common via Pi pin 20 → J16.

## Software side (what the code assumes about this wiring)

| Part | Code | Assumes |
|---|---|---|
| LED frame | `leds.py` (Pi5Neo, `/dev/spidev0.0`) | 29 LEDs (`LED_COUNT`); SPI enabled; frame current capped at 1 A (`MAX_CURRENT_A`) because strip power crosses the breadboard. Raise the cap only after strip power moves onto Wagos, and update this page. |
| Speaker | `sound.py` (`pw-play`, fallback `aplay` on the default device) | a true USB audio device (`aplay -l` lists it); the backend runs as the kiosk user so it shares that session's PipeWire. Never an ALSA `hw:` device. |
| Radar | `presence.py` (gpiozero + lgpio) | OUT on GPIO17, internal pull-down, 50 ms debounce; range and no-one duration are set in the radar with the HLK app. |
| Backlight | `real.py` `Backlight` | `/sys/class/backlight/<panel>/brightness` writable by group video (udev rule, Pi setup note 2). |

Each of the four starts independently (`real.py`): if one fails it is logged
and its calls do nothing, and the others keep working, so a dead strip never
silences a low alarm.

Hand checks, run on the Pi over SSH from the repo root:

| Step | Command | Pass |
|---|---|---|
| 2 | `.venv/bin/python hardware/scripts/first_light.py` | LED 0 red, 1 green, 2 blue at 10 % |
| 2 | `.venv/bin/python hardware/scripts/full_frame_test.py` | all 29 in the chase; colour cycle at 30 %; no flicker while wiggling both corners |
| 3 | `bash hardware/scripts/speaker_test.sh` | raw tone, driver's ramped looping urgent tone, chirp; then the tone from the running service |
| 4 | `.venv/bin/python hardware/scripts/radar_watch.py` (or `watch -n 0.5 pinctrl get 17`) | PRESENT holds with a still person; empty a few seconds after they leave |

Open:

- **Unused shifter inputs not yet confirmed grounded** (pins 4, 5, 9, 10,
  12, 13; floating CMOS inputs oscillate and heat the chip). Col 16 is
  full, so the route in the map no longer applies. Suggested route: pins 4
  and 5 (G9, G10) → H12 and J12 (col 12 bottom is ground via pin 7); for
  the top pins, bridge a free bottom ground hole to a free top column (15 or
  17), then jumper that column to A7, A8, A10, A11. Power off first; leave
  outputs 6, 8, 11 open. Record the final route here.
- Radar wire colours: add the `radar_wiring_*.jpg` photos to docs/figures/
  (which adapter wire colour went to VCC, GND, OUT; colours are not
  standardized, so the board's printed labels are the reference).
- Radar settings (HLKRadarTool, stored in the radar): HOME = max gate 2
  (2.25 m), no-one duration 5 s, set 2026-09-26. DEMO = gate 1 (1.5 m), set
  at table setup. Mount (SmartiPi cradle or printed housing) not yet
  recorded.
- Radar never dropped to empty in two 40 s tests at gate 2 (radar_watch.py
  held PRESENT throughout). Not yet known whether the tester left the range,
  or the cooler fan / a loose mount holds it on. Diagnose with the app's live
  view in an empty room: a target at 0-0.3 m = fan or vibration (move it a
  few cm and fix it rigidly), 1-2 m = something real in range, beyond
  2.25 m = the settings did not save.
