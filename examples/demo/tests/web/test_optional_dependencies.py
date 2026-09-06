"""Core imports and CLI help must work without any web dependencies."""
import subprocess
import sys

from framework.source import SOURCE_ROOT, hidden_process_options, source_environment


def test_core_entrypoint_does_not_import_optional_demo(tmp_path):
    script = '''
import importlib.abc
import sys

class BlockWeb(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'flask', 'werkzeug', 'demo'}:
            raise AssertionError('Core loaded optional module: ' + fullname)

sys.meta_path.insert(0, BlockWeb())
import pytest_plugin
import main
try:
    main.main(['run', '--help'])
except SystemExit as exc:
    assert exc.code == 0
else:
    raise AssertionError('Help did not exit')
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path,
                            env=source_environment(), capture_output=True, text=True,
                            timeout=15, **hidden_process_options())
    assert result.returncode == 0, result.stdout + result.stderr
