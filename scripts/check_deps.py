"""依存方向と命名のチェック（ARCH §3）。違反があれば一覧を出して終了コード 1。

使い方: uv run python scripts/check_deps.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core" / "soikaxa"

# 純粋層（I/O を import しない）
PURE = {"queue", "state", "adapters.base", "touch"}
IO_MODULES = {"subprocess", "socket", "urllib", "http", "requests", "httpx", "os", "shutil"}
# web から直接呼んではいけない内部モジュール
WEB_FORBIDDEN = {"soikaxa.player", "soikaxa.runner"}
FORBIDDEN_WORDS = re.compile(r"youtube|ytmusic", re.IGNORECASE)
NAMING_FILES = ("README.md",)


def module_name(path: Path) -> str:
    rel = path.relative_to(CORE).with_suffix("")
    return ".".join(rel.parts)


def resolve(mod: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    base = ("soikaxa." + mod).split(".")[: -node.level]
    return ".".join([*base, node.module] if node.module else base)


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mod = module_name(path)
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            out.append(resolve(mod, n))
    return out


def code_without_comments(path: Path) -> str:
    """docstring とコメントを除いたコード（語の混入チェック用）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body[0].value.value = ""
    return ast.unparse(tree)


def check() -> list[str]:
    errors: list[str] = []
    for path in sorted(CORE.rglob("*.py")):
        mod = module_name(path)
        imps = imports(path)
        if mod in PURE:
            bad = [i for i in imps if i.split(".")[0] in IO_MODULES]
            errors += [f"{mod}: 純粋層が I/O を import: {b}" for b in bad]
        if any(i.startswith("soikaxa_adapter") for i in imps):
            errors.append(f"{mod}: コアがアダプタパッケージを import")
        if mod.startswith("web."):
            errors += [f"{mod}: web が {i} を直接 import" for i in imps if i in WEB_FORBIDDEN]
        if FORBIDDEN_WORDS.search(code_without_comments(path)):
            errors.append(f"{mod}: コアのコードに音源固有名が含まれる")
    for name in NAMING_FILES:
        p = ROOT / name
        if p.exists() and re.search(r"youtube|\byt\b", p.read_text(encoding="utf-8"), re.IGNORECASE):
            errors.append(f"{name}: 「YouTube」「yt」を含めない（D-10）")
    return errors


def main() -> int:
    errors = check()
    for e in errors:
        print(e)
    print(f"check_deps: {len(errors)} violation(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
