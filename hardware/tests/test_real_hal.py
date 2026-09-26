"""RealHAL on a laptop: each device starts on its own, so a dead LED frame
never takes the sound (and so the low alarm) down with it."""

from hardware import real


def test_dead_devices_degrade_to_noops(monkeypatch):
    def boom():
        raise RuntimeError("no SPI here")

    monkeypatch.setattr(real.leds, "LedFrame", boom)
    monkeypatch.setattr(real.presence, "Radar", boom)
    played = []
    monkeypatch.setattr(real.sound, "play", lambda name, volume: played.append((name, volume)))

    h = real.RealHAL()
    h.set_leds("full")  # no frame: no crash
    h.play_sound("alarm_urgent", 1.0)
    assert played == [("alarm_urgent", 1.0)]
    assert h.get_presence() is None  # no radar = no value, never a guess


def test_sound_failure_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr(real.leds, "LedFrame", lambda: None)
    monkeypatch.setattr(real.presence, "Radar", lambda: None)

    def fail(name, volume):
        raise OSError("pw-play missing")

    monkeypatch.setattr(real.sound, "play", fail)
    real.RealHAL().play_sound("chirp", 0.5)


def make_panel(tmp_path, max_brightness=255, current=255):
    panel = tmp_path / "backlight" / "10-0045"
    panel.mkdir(parents=True)
    (panel / "max_brightness").write_text(f"{max_brightness}\n")
    (panel / "brightness").write_text(f"{current}\n")
    return tmp_path / "backlight", panel / "brightness"


def test_backlight_scales_to_the_panel_max(tmp_path):
    root, bright = make_panel(tmp_path, max_brightness=31)
    bl = real.Backlight(root)
    bl.set(0.5)
    assert bright.read_text().strip() == "16"
    bl.set(7.0)
    assert bright.read_text().strip() == "31"
    bl.set(0.0)
    assert bright.read_text().strip() == "0"


def test_no_backlight_means_no_op_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(real, "BACKLIGHT_ROOT", tmp_path / "nothing")
    monkeypatch.setattr(real.Backlight.__init__, "__defaults__", (tmp_path / "nothing",))
    monkeypatch.setattr(real.leds, "LedFrame", lambda: None)
    monkeypatch.setattr(real.presence, "Radar", lambda: None)
    h = real.RealHAL()
    assert h.backlight is None
    h.set_display_brightness(0.2)


def test_backlight_permission_error_is_logged(tmp_path, monkeypatch):
    root, bright = make_panel(tmp_path)
    bl = real.Backlight(root)

    def denied(*a, **k):
        raise PermissionError("udev rule missing")

    monkeypatch.setattr(type(bl.path), "write_text", denied)
    bl.set(0.3)  # logged, not raised
