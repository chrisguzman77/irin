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
