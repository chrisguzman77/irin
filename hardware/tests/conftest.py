import sys
from pathlib import Path

import pytest

# repo root on sys.path so `import hardware` works from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hardware import hal  # noqa: E402


@pytest.fixture
def mock_hal(monkeypatch):
    monkeypatch.setenv("IRIN_HW", "mock")
    hal.reset_hal_for_test()
    yield hal.get_hal()
    hal.reset_hal_for_test()
