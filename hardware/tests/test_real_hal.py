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
