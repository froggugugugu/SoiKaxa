import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("check_deps", ROOT / "scripts" / "check_deps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_dependency_or_naming_violations():
    assert load().check() == []


def test_adapter_interface_unchanged_from_handoff():
    # D-09: base.py は input/soikaxa から無変更で取り込む
    src = ROOT / "input" / "soikaxa" / "core" / "soikaxa" / "adapters" / "base.py"
    if src.exists():
        assert (ROOT / "core" / "soikaxa" / "adapters" / "base.py").read_bytes() == src.read_bytes()
