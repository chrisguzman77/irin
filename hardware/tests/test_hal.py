"""slavik.md step 1: the HAL interface and the mock the backend develops against."""

import pytest

from hardware import hal
from hardware.mock import MockHAL


def test_get_hal_is_mock_under_mock(mock_hal):
    assert isinstance(mock_hal, MockHAL)
    assert hal.get_hal() is mock_hal  # cached


def test_get_hal_defaults_to_mock_when_unset(monkeypatch):
    monkeypatch.delenv("IRIN_HW", raising=False)
    hal.reset_hal_for_test()
    try:
        assert isinstance(hal.get_hal(), MockHAL)
    finally:
        hal.reset_hal_for_test()


def test_presence_is_none_until_driven(mock_hal):
    assert mock_hal.get_presence() is None
    mock_hal.set_presence_for_test(True)
    assert mock_hal.get_presence() is True
    mock_hal.set_presence_for_test(False)
    assert mock_hal.get_presence() is False
    mock_hal.set_presence_for_test(None)
    assert mock_hal.get_presence() is None


def test_presence_rejects_non_bool(mock_hal):
    with pytest.raises(ValueError):
        mock_hal.set_presence_for_test(1)


@pytest.mark.parametrize("state", sorted(hal.LED_STATES))
def test_every_led_state_accepted(mock_hal, state):
    mock_hal.set_leds(state)
    assert mock_hal.led_state == state


def test_led_color_and_brightness(mock_hal):
    mock_hal.set_leds("ambient", color=(10, 20, 30), brightness=0.1)
    assert (mock_hal.led_color, mock_hal.led_brightness) == ((10, 20, 30), 0.1)
    mock_hal.set_leds("full")
    assert (mock_hal.led_color, mock_hal.led_brightness) == (None, None)
    mock_hal.set_leds("ambient", brightness=3.0)
    assert mock_hal.led_brightness == 1.0


@pytest.mark.parametrize("color", [(256, 0, 0), (-1, 0, 0), (1, 2), (1.5, 0, 0)])
def test_bad_color_rejected(mock_hal, color):
    with pytest.raises(ValueError):
        mock_hal.set_leds("ambient", color=color)


def test_unknown_names_rejected(mock_hal):
    with pytest.raises(ValueError):
        mock_hal.set_leds("rainbow")
    with pytest.raises(ValueError):
        mock_hal.play_sound("alarm_urgnet", 1.0)
    assert mock_hal.led_state == "off" and mock_hal.playing is None


@pytest.mark.parametrize("name", sorted(hal.SOUND_NAMES))
def test_full_volume_reaches_speaker_uncapped(mock_hal, name):
    mock_hal.play_sound(name, 1.0)
    assert (mock_hal.playing, mock_hal.volume) == (name, 1.0)
    mock_hal.stop_sound()
    assert mock_hal.playing is None


def test_volume_and_brightness_clamped(mock_hal):
    mock_hal.play_sound("chirp", 7.0)
    assert mock_hal.volume == 1.0
    mock_hal.play_sound("chirp", -1.0)
    assert mock_hal.volume == 0.0
    mock_hal.set_display_brightness(-0.5)
    assert mock_hal.brightness == 0.0
    mock_hal.set_display_brightness(0.4)
    assert mock_hal.brightness == 0.4


def test_calls_are_logged(mock_hal):
    mock_hal.set_leds("warning")
    mock_hal.play_sound("alarm_soft", 0.5)
    mock_hal.stop_sound()
    assert mock_hal.calls == [("set_leds", "warning"), ("play_sound", "alarm_soft", 0.5), ("stop_sound",)]


def test_overrides_extend_the_call_record(mock_hal):
    mock_hal.set_leds("ambient", color=(1, 2, 3), brightness=0.2)
    assert mock_hal.calls[-1] == ("set_leds", "ambient", (1, 2, 3), 0.2)
