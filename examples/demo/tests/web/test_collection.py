"""The single demo config scopes collection without overriding explicit cases."""
from pathlib import Path
import subprocess
import sys

from framework.source import hidden_process_options
from demo.common.environment import demo_environment


def test_collection_from_outside_repository_preserves_explicit_selection(tmp_path):
    config = Path(__file__).resolve().parents[2] / "pytest.ini"
    unrelated = tmp_path / "test_unrelated.py"
    unrelated.write_text("def test_unrelated(): pass\n", encoding="utf-8")
    args = [sys.executable, "-m", "pytest", "-c", str(config), "--collect-only", "-q"]
    options = dict(cwd=tmp_path, env=demo_environment(), capture_output=True,
                   text=True, encoding="utf-8", timeout=20, **hidden_process_options())
    collected = subprocess.run(args, **options)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "test_five_real_cases_repeated" in collected.stdout
    assert "test_core_entrypoint_does_not_import_optional_demo" in collected.stdout
    assert "test_unrelated" not in collected.stdout
    explicit = subprocess.run(args + [str(unrelated)], **options)
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    assert "test_unrelated" in explicit.stdout
    assert "test_five_real_cases_repeated" not in explicit.stdout
