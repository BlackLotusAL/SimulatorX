"""Static dependency boundaries of shared layers and device packages."""
import ast
from importlib.util import resolve_name

from framework.source import SOURCE_ROOT


def test_shared_layers_do_not_import_devices():
    devices = {"subsystems"}
    for directory in ("framework", "protocols"):
        for path in (SOURCE_ROOT / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                assert not any(name.split(".")[0] in devices for name in names), path


def test_device_packages_do_not_depend_on_each_other():
    # Model files identify source packages for this static dependency test only.
    packages = {".".join(path.parent.relative_to(SOURCE_ROOT).parts): path.parent
                for path in (SOURCE_ROOT / "subsystems").glob("*/*/model.py")}
    assert "subsystems.vacuum.chamber_plc" in packages
    for owner, directory in packages.items():
        for path in directory.rglob("*.py"):
            package = ".".join(path.parent.relative_to(SOURCE_ROOT).parts)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = "." * node.level + (node.module or "")
                    base = resolve_name(base, package) if node.level else base
                    names = [base] + [base + "." + alias.name for alias in node.names]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # Also cover literal dynamic imports and module:factory references.
                    names = [node.value.split(":")[0]]
                for other in packages.keys() - {owner}:
                    assert not any(name == other or name.startswith(other + ".")
                                   for name in names), (path, other)
