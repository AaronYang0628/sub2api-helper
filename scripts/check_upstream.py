#!/usr/bin/env python3
"""Read-only upstream review gate; never accepts a new baseline automatically."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


BASELINE = Path(__file__).resolve().parents[1] / "docs" / "upstream-baseline.json"


def git(source: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError("无法读取上游 Git checkout；请检查 --source 是否为有效、可读取的仓库根目录")
    return result.stdout.strip()


def inspect(source: Path) -> tuple[int, str]:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    reviewed = baseline["reviewed_commit"]
    hashes = baseline["files"]
    if not re.fullmatch(r"[0-9a-f]{40}", reviewed) or not isinstance(hashes, dict) or not hashes:
        raise ValueError("审阅基线无效；不得自动重新生成或接受未知版本")
    if not source.is_dir() or Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("--source 必须是上游 Git checkout 的根目录")
    current = git(source, "rev-parse", "--verify", "HEAD^{commit}")
    if not re.fullmatch(r"[0-9a-f]{40}", current):
        raise ValueError("无法确认上游完整提交 SHA")
    changed = []
    missing = []
    for relative, expected in sorted(hashes.items()):
        path = source / relative
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or not path.resolve().is_relative_to(source):
            raise ValueError("审阅基线含有无效路径或 SHA256")
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            missing.append(relative)
            continue
        if actual != expected:
            changed.append(relative)
    unreviewed = current != reviewed
    failed = unreviewed or bool(changed) or bool(missing)
    lines = [
        "# sub2api 上游兼容性检查",
        "",
        f"- 已审阅版本：`{baseline['reviewed_version']}`",
        f"- 已审阅提交：`{reviewed}`",
        f"- 本次上游提交：`{current}`",
        f"- SHA256 检查文件数：{len(hashes)}",
        f"- 结果：{'需要审阅，检查未通过' if failed else '与已审阅基线一致，检查通过'}",
        "",
    ]
    if unreviewed:
        lines.extend([
            "上游提交尚未审阅。即使关键接口文件哈希未变化，也必须检查本次更新的间接依赖和行为变更。",
            f"比较提交：https://github.com/Wei-Shaw/sub2api/compare/{reviewed}...{current}",
            "",
        ])
    if changed:
        lines.extend(["以下关键文件 SHA256 发生变化：", "", *[f"- `{name}`" for name in changed], ""])
    if missing:
        lines.extend(["以下关键文件缺失或不可读取：", "", *[f"- `{name}`" for name in missing], ""])
    if not changed and not missing:
        lines.extend(["关键文件 SHA256 均与基线一致。", ""])
    lines.append("该检查只发现未审阅的更新及源文件变化；契约测试与真实实例联调仍是兼容性验证的一部分。脚本没有修改上游、助手代码或审阅基线。")
    return int(failed), "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="已 checkout 的 Wei-Shaw/sub2api 仓库根目录；脚本不访问网络")
    args = parser.parse_args()
    try:
        code, report = inspect(args.source.resolve())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        code = 2
        report = f"# sub2api 上游兼容性检查\n\n检查无法完成：{error}\n"
    print(report, end="")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as output:
                output.write(report)
        except OSError:
            print("无法写入 GitHub step summary；检查结果见以上输出", file=sys.stderr)
            return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
