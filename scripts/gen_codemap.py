#!/usr/bin/env python3
"""scripts/gen_codemap.py — 半自动生成代码地图骨架（结构层）。

用 ast 静态扫描 dragon_quant/，抽取「自动工具能可靠表达」的结构性事实：
  1) DataCache 键的读(get)/写(set)分布 —— 数据流契约
  2) scorers / scorers_v2 的 score()/evaluate() 函数签名 —— 评分器入口
  3) providers 各 Provider 类的公共方法签名 —— 数据源能力

输出到 CODEMAP.generated.md（**自动生成、勿手改**）；语义层（执行路径/设计意图/
不变式）由手工维护的 CODEMAP.md 承载。

用法：
    python scripts/gen_codemap.py            # 写入 CODEMAP.generated.md
    python scripts/gen_codemap.py --check    # 校验是否与现有文件一致（CI 用，漂移则非0退出）
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "dragon_quant"
OUT = ROOT / "CODEMAP.generated.md"

# cache 键里的 f-string 变量统一抽象为 {var}，便于聚合
_FSTR_VAR = re.compile(r"\{[^}]+\}")


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def _norm_key(s: str) -> str:
    """把 f-string 的 {code}/{primary_sector} 等占位抽象掉，归一化 cache 键。"""
    return _FSTR_VAR.sub("{}", s)


def _literal_of(node: ast.AST) -> str | None:
    """从 ast 节点取 cache 键字符串：支持常量字符串与 JoinedStr(f-string)。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append("{}")  # FormattedValue 占位
        return "".join(parts)
    return None


def scan_cache_keys(py: Path) -> tuple[set[str], set[str]]:
    """返回 (读到的键集合, 写入的键集合)。"""
    reads: set[str] = set()
    writes: set[str] = set()
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return reads, writes
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("get", "set")):
            continue
        # 仅匹配形如 <something cache>.get/set(...)，第一个实参为键
        if not node.args:
            continue
        key = _literal_of(node.args[0])
        if key is None:
            continue
        key = _norm_key(key)
        # 过滤明显非 cache 键（如 dict.get("x", default) 的业务字段），只收带前缀的
        if not any(key.startswith(p) for p in (
                "kline:", "sector:", "quotes:", "pankou:", "__meta__:")):
            continue
        (reads if fn.attr == "get" else writes).add(key)
    return reads, writes


def scan_func_sigs(py: Path, names: set[str]) -> list[str]:
    """抽取指定函数名的签名（顶层函数）。"""
    out: list[str] = []
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return out
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out.append(f"{node.name}{_args(node)}")
    return out


def scan_provider_methods(py: Path) -> dict[str, list[str]]:
    """返回 {ClassName: [public method 签名...]}。"""
    res: dict[str, list[str]] = {}
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return res
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name.startswith("_"):
                    continue
                methods.append(f"{item.name}{_args(item)}")
        if methods:
            res[node.name] = methods
    return res


def _args(node: ast.FunctionDef) -> str:
    a = node.args
    parts = [arg.arg for arg in a.args]
    if a.vararg:
        parts.append("*" + a.vararg.arg)
    if a.kwarg:
        parts.append("**" + a.kwarg.arg)
    # 去掉 self/cls
    parts = [p for p in parts if p not in ("self", "cls")]
    return "(" + ", ".join(parts) + ")"


def build() -> str:
    lines: list[str] = [
        "# CODEMAP.generated.md — 结构骨架（自动生成，勿手改）",
        "",
        "> 由 `scripts/gen_codemap.py` 静态扫描生成。语义层（执行路径/设计意图/",
        "> 不变式）见手工维护的 `CODEMAP.md`。修改代码后运行 `python scripts/gen_codemap.py` 重生成。",
        "",
        "## 一、DataCache 键读写契约",
        "",
        "| cache 键 | 写入方 (set) | 读取方 (get) |",
        "|----------|-------------|-------------|",
    ]
    # 聚合所有文件的读写
    writers: dict[str, set[str]] = {}
    readers: dict[str, set[str]] = {}
    for py in sorted(PKG.rglob("*.py")):
        reads, writes = scan_cache_keys(py)
        mod = _rel(py)
        for k in writes:
            writers.setdefault(k, set()).add(mod)
        for k in reads:
            readers.setdefault(k, set()).add(mod)
    all_keys = sorted(set(writers) | set(readers))
    for k in all_keys:
        w = ", ".join(sorted(writers.get(k, set()))) or "—"
        r = ", ".join(sorted(readers.get(k, set()))) or "—"
        lines.append(f"| `{k}` | {w} | {r} |")

    # 评分器入口
    lines += ["", "## 二、评分器入口签名", ""]
    for sub in ("scorers", "scorers_v2"):
        d = PKG / sub
        if not d.exists():
            continue
        lines.append(f"### `{sub}/`")
        for py in sorted(d.glob("*.py")):
            sigs = scan_func_sigs(py, {"score", "evaluate", "rank_verdicts"})
            for s in sigs:
                lines.append(f"- `{_rel(py)}` → `{s}`")
        lines.append("")

    # provider 能力
    lines += ["## 三、Provider 公共方法", ""]
    for py in sorted((PKG / "providers").glob("*.py")):
        classes = scan_provider_methods(py)
        for cls, methods in classes.items():
            if not cls.endswith("Provider"):
                continue
            lines.append(f"### {cls} (`{_rel(py)}`)")
            for m in methods:
                lines.append(f"- `{m}`")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    content = build()
    if "--check" in sys.argv:
        old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if old != content:
            print("CODEMAP.generated.md 已过期，请运行 `python scripts/gen_codemap.py` 重生成",
                  file=sys.stderr)
            return 1
        print("CODEMAP.generated.md 是最新的。")
        return 0
    OUT.write_text(content, encoding="utf-8")
    print(f"已生成 {_rel(OUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
