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


# 引き継ぎ時点（input/soikaxa）の base.py。input/ が無い環境（clean clone・CI）でも検査できるよう固定する
BASE_PY_SHA256 = "dfa08afea4a3100592c467334c3334ba94e59126e99a8c2b0b9b77d1d5e03a48"


def test_adapter_interface_unchanged_from_handoff():
    # D-09: アダプタ I/F（4 関数）は変更しない・増やさない
    import hashlib

    data = (ROOT / "core" / "soikaxa" / "adapters" / "base.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == BASE_PY_SHA256
