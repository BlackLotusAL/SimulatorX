"""Keep config-only invocation scoped to demo tests from any directory."""
from pathlib import Path


def pytest_configure(config):
    # pytest otherwise falls back to the invocation directory when -c points
    # outside it. Explicit case paths from the demo runner remain untouched.
    if config.args_source == config.ArgsSource.INVOCATION_DIR:
        config.args = [str(Path(config.inipath).parent / "tests")]
