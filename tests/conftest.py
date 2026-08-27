import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fake_primesim_path() -> Path:
    path = Path(__file__).parent / "fake_primesim.py"
    os.chmod(path, 0o755)
    assert os.access(path, os.X_OK)
    assert shutil.which("python3") is not None
    return path
