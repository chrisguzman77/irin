"""slavik.md step 3: gain + ramp rendering, loop vs one-shot, stop, and the
player command, on a laptop (a fake Popen stands in for pw-play; no sleeps)."""

import array
import io
import wave

import pytest

from hardware import sound


def peak(wav_bytes, start_s=0.0, end_s=None):
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate = w.getframerate()
        s = array.array("h", w.readframes(w.getnframes()))
    a, b = int(start_s * rate), (int(end_s * rate) if end_s else len(s))
    return max(abs(x) for x in s[a:b])


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sound, "_CACHE_DIR", tmp_path)


@pytest.mark.parametrize("name", ["alarm_soft", "alarm_urgent", "chirp", "buddy_chime"])
def test_every_named_file_exists_and_renders(name):
    assert (sound.SOUNDS_DIR / f"{name}.wav").exists()
    assert sound.render_wav(sound.SOUNDS_DIR / f"{name}.wav", 0.5)


def test_full_volume_is_the_file_uncapped():
    src = sound.SOUNDS_DIR / "alarm_urgent.wav"
    with wave.open(str(src)) as w:
        original = max(abs(x) for x in array.array("h", w.readframes(w.getnframes())))
    assert peak(sound.render_wav(src, 1.0)) == original


def test_volume_scales_samples():
    src = sound.SOUNDS_DIR / "alarm_urgent.wav"
    full, half = peak(sound.render_wav(src, 1.0)), peak(sound.render_wav(src, 0.5))
    assert abs(half - full / 2) <= 2


def test_ramp_starts_quiet_and_reaches_target():
    src = sound.SOUNDS_DIR / "alarm_urgent.wav"
    ramped = sound.render_wav(src, 1.0, ramp_s=1.5, ramp_from=0.5)
    steady = sound.render_wav(src, 1.0)
    assert peak(ramped, 0.0, 0.3) < 0.7 * peak(steady, 0.0, 0.3)
    assert peak(ramped, 1.6, 2.0) == peak(steady, 1.6, 2.0)


class FakeProc:
    def __init__(self, cmd, on_wait=None, **kw):
        self.cmd = cmd
        self.terminated = False
        self._on_wait = on_wait

    def wait(self):
        if self._on_wait:
            self._on_wait()  # a stop() arriving from another thread while the tone plays
        return 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


def fake_popen(log, stop_after=None, player_ref=None):
    def popen(cmd, **kw):
        log.append(cmd)
        on_wait = player_ref[0].stop if stop_after is not None and len(log) >= stop_after else None
        return FakeProc(cmd, on_wait=on_wait)

    return popen


@pytest.fixture
def pwplay(monkeypatch):
    monkeypatch.setattr(sound.shutil, "which", lambda exe: "/usr/bin/pw-play" if exe == "pw-play" else None)


def test_alarms_loop_until_stopped_ramp_first_pass_only(pwplay):
    log, ref = [], [None]
    p = sound.Player("alarm_urgent", 1.0, popen=fake_popen(log, stop_after=3, player_ref=ref))
    ref[0] = p
    p.run()
    assert len(log) == 3
    assert log[0][0] == "pw-play"
    assert log[0][1].endswith("-r.wav") and all(c[1].endswith("-s.wav") for c in log[1:])


@pytest.mark.parametrize("name", ["chirp", "buddy_chime"])
def test_one_shots_play_once(pwplay, name):
    log = []
    sound.Player(name, 0.4, popen=fake_popen(log)).run()
    assert len(log) == 1


def test_stop_terminates_the_running_process():
    p = sound.Player("alarm_soft", 0.5)
    p._proc = FakeProc(["pw-play"])
    p.stop()
    assert p._proc.terminated


def test_unknown_sound_rejected():
    with pytest.raises(ValueError):
        sound.Player("alarm_urgnet", 1.0)


def test_aplay_fallback_uses_default_device(monkeypatch, tmp_path):
    monkeypatch.setattr(sound.shutil, "which", lambda exe: "/usr/bin/aplay" if exe == "aplay" else None)
    cmd = sound.player_command(tmp_path / "x.wav")
    assert cmd[0] == "aplay" and not any("hw:" in c for c in cmd)


def test_no_player_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(sound.shutil, "which", lambda exe: None)
    with pytest.raises(RuntimeError):
        sound.player_command(tmp_path / "x.wav")
