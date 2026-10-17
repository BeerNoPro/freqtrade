import os
import shutil
import subprocess  # noqa: S404, RUF100
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import is_mac


# Parallel pytest-xdist workers compete for the CPU and slow down process startup.
# Slow imports at startup cost seconds, so a doubled budget still catches regressions.
PARALLEL_WORKERS = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1")) > 1
MAXIMUM_STARTUP_TIME = (0.7 if is_mac() else 0.5) * (2 if PARALLEL_WORKERS else 1)


def freqtrade_executable() -> str | None:
    """
    Locate the freqtrade console script of the interpreter running the tests.
    The virtual environment is not necessarily on PATH (e.g. when the venv is not activated).
    """
    scripts_dir = Path(sys.executable).parent
    for name in ("freqtrade.exe", "freqtrade"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which("freqtrade")


def test_startup_time():
    executable = freqtrade_executable()
    if executable is None:
        pytest.skip("freqtrade console script is not installed")

    # warm up to generate pyc
    subprocess.run([executable, "-h"], check=True, capture_output=True)

    # Best of several runs: other test workers running in parallel add noise to a single run.
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        subprocess.run([executable, "-h"], check=True, capture_output=True)
        timings.append(time.perf_counter() - start)
    elapsed = min(timings)
    assert elapsed < MAXIMUM_STARTUP_TIME, (
        "The startup time is too long, try to use lazy import in the command entry function"
        f" (maximum {MAXIMUM_STARTUP_TIME}s, got {elapsed}s)"
    )
