#!/usr/bin/env python3
"""Select one official release for the PR queue; never executes upstream code."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote

from github_api import GitHub, GitHubError
from sync_upstream_release import normalize_release, select_release
from version import read_version


def validate_repository(repository):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected owner/repository")
    return repository


def own_sync_pr(pr, repository, default_branch):
    head, base = pr.get("head") or {}, pr.get("base") or {}
    return (base.get("ref") == default_branch
            and (head.get("repo") or {}).get("full_name", "").lower() == repository.lower()
            and re.fullmatch(r"automation/upstream-v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", head.get("ref", "")) is not None)


def discover(api, repository, default_branch, root):
    validate_repository(repository)
    open_prs = api.paginate(f"repos/{repository}/pulls?state=open&base={quote(default_branch, safe='')}")
    active = [pr for pr in open_prs if own_sync_pr(pr, repository, default_branch)]
    if active:
        return {"found": False, "reason": f"Sync PR #{active[0]['number']} is still open; later releases remain queued"}, None
    records = api.paginate("repos/Wei-Shaw/sub2api/releases")
    current = read_version(root)
    manifest_path = root / "docs/upstream-release.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("version") != current or manifest.get("tag") not in {current, "v" + current}:
            raise ValueError("Default-branch release metadata does not match the canonical version")
        try:
            published = api.request("GET", f"repos/{repository}/releases/tags/{manifest['tag']}")
        except GitHubError as error:
            if error.status != 404:
                raise
            published = None
        try:
            published_at = datetime.fromisoformat(published["published_at"].replace("Z", "+00:00"))
            confirmed = published_at.tzinfo is not None
        except (TypeError, ValueError, KeyError, AttributeError):
            confirmed = False
        if not (confirmed and isinstance(published, dict) and published.get("tag_name") == manifest["tag"]
                and published.get("draft") is False and published.get("prerelease") is False):
            return {"found": False, "reason": f"Helper {manifest['tag']} is not published yet; retry its release job before advancing the queue"}, None
    # The first deployment still gets a PR for the already-aligned official
    # version, so the release evidence is established through the same process.
    same = [record for record in records if (release := normalize_release(record)) and release["version"] == current]
    chosen = None
    if not manifest_path.exists() and same:
        chosen = {**same[0], **normalize_release(same[0])}
    chosen = chosen or select_release(records, current)
    if chosen is None:
        return {"found": False, "reason": "No newer published stable upstream release"}, None
    branch = "automation/upstream-v" + chosen["version"]
    history = api.paginate(f"repos/{repository}/pulls?state=all&head={quote(repository.split('/')[0] + ':' + branch, safe='')}&base={quote(default_branch, safe='')}")
    previous = [pr for pr in history if own_sync_pr(pr, repository, default_branch) and pr["head"]["ref"] == branch]
    if previous:
        pr = previous[0]
        reason = ("already merged; refresh the default checkout" if pr.get("merged_at")
                  else "was closed without merge; reopen it to resume this release queue")
        return {"found": False, "reason": f"Sync PR #{pr['number']} {reason}"}, None
    return {"found": True, "version": chosen["version"], "tag": chosen["tag"], "branch": branch,
            "reason": "An official stable release needs a version-sync PR"}, chosen


def pull_request_body(manifest):
    review = manifest["requires_review"]
    relevant = manifest["evidence"]["relevant_changes"]
    lines = [f"同步官方 sub2api **{manifest['tag']}**，Helper 版本为 **{manifest['version']}**。", "",
             f"上游 Release：{manifest['release_url']}",
             f"固定提交：`{manifest['target_sha']}`", "",
             "需要调整或审阅接口、依赖、UI 或其他运行时变化。" if review else "未发现相关运行时变化；仍按要求提交 PR 并在合并后发布同版本。",
             "", f"检查到 {len(manifest['evidence']['all_changes'])} 个文件变化，其中 {len(relevant)} 个需要兼容性审阅。", ""]
    for change in relevant:
        safe_path = change["path"].replace("`", "\\`").replace("\n", " ")
        lines.append(f"- `{change['status']} {safe_path}`")
    lines.extend(["", "完整证据见 `docs/upstream-release.json`。", "",
                  "CI 会验证固定 Release 提交、全部差异、版本一致性、接口契约和容器流程；不会跟随后续 main 提交而漂移。", ""])
    if review:
        lines.extend(["适配并验证相关改动后，在精确 tag 的上游 checkout 上执行：", "", "```sh",
                      "python3 scripts/sync_upstream_release.py approve \\",
                      "  --release-json /path/to/official-release.json \\",
                      "  --source /path/to/upstream-exact-tag",
                      "```", "", "将生成的兼容性批准及基线一起提交；批准绑定本次提交和证据指纹，不能沿用旧版本的批准。", ""])
    lines.extend(["PR 合并且默认分支 CI 通过后，同一次发布流程构建 amd64/arm64 镜像、创建同版本标签及 GitHub Release。",
                  "不自动合并；部分发布失败可以安全重跑，不覆盖指向其他提交的既有标签或镜像。", ""])
    return "\n".join(lines)


def outputs(path, values):
    if path:
        with path.open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                if key == "reason":
                    continue
                stream.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discovery = commands.add_parser("discover")
    discovery.add_argument("--repository", required=True)
    discovery.add_argument("--default-branch", required=True)
    discovery.add_argument("--helper-root", type=Path, default=Path(__file__).resolve().parents[1])
    discovery.add_argument("--release-json", type=Path, required=True)
    discovery.add_argument("--outputs", type=Path)
    body = commands.add_parser("body")
    body.add_argument("--manifest", type=Path, required=True)
    body.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "body":
            args.out.write_text(pull_request_body(json.loads(args.manifest.read_text())), encoding="utf-8")
            return 0
        values, selected = discover(GitHub(), args.repository, args.default_branch, args.helper_root.resolve())
        if selected:
            args.release_json.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        outputs(args.outputs, values)
        print(values["reason"])
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
                stream.write("## Upstream release queue\n\n" + values["reason"] + "\n")
        return 0
    except (GitHubError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Release watcher failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
