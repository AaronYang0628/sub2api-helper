#!/usr/bin/env python3
"""Create a real release-sync PR through GitHub's Git Data API.

Only eight release-owned metadata paths can change. Existing branch work is
retained unless every unmerged change is known bot-authored sync metadata.
Credentials stay inside the shared API client, never Git arguments or files.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, urlencode

try:
    from .github_api import GitHub, GitHubError
except ImportError:
    from github_api import GitHub, GitHubError


ALLOWED = frozenset(("helper/VERSION", "compose.yaml", "ui/package.json", "ui/package-lock.json", "README.md",
                     "docs/upstream-release.json", "docs/upstream-release-approval.json", "docs/upstream-baseline.json"))
OPTIONAL = frozenset(("docs/upstream-release-approval.json",))
SHA = re.compile(r"[0-9a-f]{40}\Z")
BRANCH = re.compile(r"automation/upstream-v((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_sha(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "GitHub returned an invalid commit/tree/blob SHA")
    return value


def optional_get(api, path):
    try:
        return api.request("GET", path)
    except GitHubError as error:
        if error.status == 404:
            return None
        raise


def git_head(root):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"], capture_output=True, text=True, check=False)
    require(result.returncode == 0, "Prepared helper directory must retain its original checkout HEAD")
    return checked_sha(result.stdout.strip())


def ref_head(api, prefix, branch, optional=False):
    path = prefix + "/git/ref/heads/" + quote(branch, safe="")
    value = optional_get(api, path) if optional else api.request("GET", path)
    if value is None:
        return None
    require(isinstance(value, dict) and value.get("object", {}).get("type") == "commit", "Branch does not point directly to a commit")
    return checked_sha(value["object"].get("sha"))


def existing_pr(api, prefix, repository_id, repository, base, branch):
    query = urlencode({"state": "all", "head": repository.split("/")[0] + ":" + branch, "base": base})
    history = api.paginate(prefix + "/pulls?" + query)
    matches = []
    for pr in history:
        require(isinstance(pr, dict), "Invalid pull-request history")
        head, target = pr.get("head") or {}, pr.get("base") or {}
        if head.get("ref") != branch or target.get("ref") != base:
            continue
        require((head.get("repo") or {}).get("id") == repository_id and (target.get("repo") or {}).get("id") == repository_id,
                "Reserved sync branch is associated with a foreign pull request")
        matches.append(pr)
    opened = [pr for pr in matches if pr.get("state") == "open"]
    require(len(opened) <= 1, "Multiple open PRs use the reserved sync branch")
    if opened:
        require(type(opened[0].get("number")) is int and opened[0]["number"] > 0, "Invalid existing PR number")
        return opened[0]
    require(not matches, "A sync PR for this branch was already closed; reopen it instead of creating a duplicate")
    return None


def branch_parents(api, prefix, base_sha, head_sha, version):
    if head_sha is None or head_sha == base_sha:
        return [base_sha]
    comparison = api.request("GET", prefix + f"/compare/{base_sha}...{head_sha}")
    require(isinstance(comparison, dict) and comparison.get("status") in {"ahead", "behind", "identical", "diverged"}, "Cannot compare the existing sync branch")
    common = checked_sha((comparison.get("merge_base_commit") or {}).get("sha"))
    if common == head_sha and comparison["status"] in {"behind", "identical"}:
        return [base_sha]  # The old sync head is already included in default.
    own = api.request("GET", prefix + f"/compare/{common}...{head_sha}?per_page=100")
    require(isinstance(own, dict) and own.get("status") == "ahead"
            and (own.get("merge_base_commit") or {}).get("sha") == common,
            "Existing sync branch ancestry could not be confirmed")
    files, commits = own.get("files"), own.get("commits")
    require(isinstance(files, list) and len(files) < 300 and isinstance(commits, list)
            and type(own.get("total_commits")) is int and 0 < own["total_commits"] == len(commits) <= 100,
            "Existing branch comparison is incomplete; preserving all branch work")
    for item in files:
        require(isinstance(item, dict) and item.get("filename") in ALLOWED
                and ("previous_filename" not in item or item["previous_filename"] in ALLOWED),
                "Existing sync branch contains non-metadata changes; preserving user work")
    for commit in commits:
        require(isinstance(commit, dict), "Invalid existing branch commit")
        # The previous create-pull-request action used the triggering user's
        # author, but the authenticated committer was github-actions[bot].
        committer, details = commit.get("committer") or {}, commit.get("commit") or {}
        require(committer.get("type") == "Bot" and committer.get("login") == "github-actions[bot]"
                and isinstance(details.get("message"), str)
                and details["message"].partition("\n")[0] in {f"chore: sync sub2api v{version}", f"chore: sync sub2api {version}"},
                "Existing sync branch contains a non-bot commit; preserving user work")
    return [base_sha, head_sha]


def open_sync_pr(api, repository, base, branch, title, body, root, draft=False, checkout_sha=None):
    require(isinstance(repository, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "Invalid repository")
    match = BRANCH.fullmatch(branch) if isinstance(branch, str) else None
    require(match is not None, "Only canonical automation/upstream-vVERSION branches are supported")
    version = match[1]
    require(isinstance(base, str) and base and not any(character in base for character in "\r\n"), "Invalid base branch")
    require(title in {f"chore: sync sub2api v{version}", f"chore: sync sub2api {version}"}, "PR title must describe this exact upstream version")
    require(isinstance(body, str) and 0 < len(body.encode()) <= 60000 and type(draft) is bool, "Invalid PR body or draft flag")
    root = Path(root).resolve()
    prepared = {}
    for path in sorted(ALLOWED):
        local = root / path
        require(local.resolve().is_relative_to(root) and not local.is_symlink(), "Release metadata cannot be symlinked outside the checkout")
        if not local.exists():
            require(path in OPTIONAL, "Prepared release metadata is missing a required file")
            prepared[path] = None
            continue
        require(local.is_file() and local.stat().st_size <= 5_000_000, "Release metadata must be a bounded regular file")
        prepared[path] = local.read_bytes()
    require(prepared["helper/VERSION"].decode("ascii").strip() == version, "Canonical helper version differs from the sync branch")
    manifest = json.loads(prepared["docs/upstream-release.json"])
    require(isinstance(manifest, dict) and manifest.get("version") == version, "Prepared release manifest differs from the sync branch")

    prefix = "/repos/" + repository
    info = api.request("GET", prefix)
    require(isinstance(info, dict) and str(info.get("full_name", "")).lower() == repository.lower()
            and info.get("default_branch") == base and type(info.get("id")) is int,
            "Repository/default-branch identity could not be confirmed")
    base_sha = ref_head(api, prefix, base)
    require((checkout_sha or git_head(root)) == base_sha, "Default branch advanced after preparation; retry from its current checkout")
    commit = api.request("GET", prefix + "/git/commits/" + base_sha)
    require(isinstance(commit, dict) and commit.get("sha") == base_sha, "Default branch commit response is invalid")
    tree_sha = checked_sha((commit.get("tree") or {}).get("sha"))
    tree = api.request("GET", prefix + "/git/trees/" + tree_sha + "?recursive=1")
    require(isinstance(tree, dict) and tree.get("truncated") is False and isinstance(tree.get("tree"), list), "Default tree is incomplete")
    original = {}
    for item in tree["tree"]:
        require(isinstance(item, dict), "Invalid Git tree entry")
        if item.get("path") in ALLOWED:
            require(item.get("type") == "blob" and item.get("mode") == "100644", "Release metadata in default branch is not a regular source file")
            original[item["path"]] = checked_sha(item.get("sha"))
    pr = existing_pr(api, prefix, info["id"], repository, base, branch)
    head_sha = ref_head(api, prefix, branch, optional=True)
    parents = branch_parents(api, prefix, base_sha, head_sha, version)
    entries = []
    for path, raw in prepared.items():
        if raw is None:
            if path in original:
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        blob_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if original.get(path) == blob_sha:
            continue
        blob = api.request("POST", prefix + "/git/blobs", {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"})
        require(isinstance(blob, dict) and blob.get("sha") == blob_sha, "Created blob does not match prepared release metadata")
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob_sha})
    require(entries, "No release metadata changes remain to propose")
    new_tree = api.request("POST", prefix + "/git/trees", {"base_tree": tree_sha, "tree": entries})
    new_tree_sha = checked_sha(new_tree.get("sha") if isinstance(new_tree, dict) else None)
    new_commit = api.request("POST", prefix + "/git/commits", {"message": title, "tree": new_tree_sha, "parents": parents})
    new_sha = checked_sha(new_commit.get("sha") if isinstance(new_commit, dict) else None)
    # Do not move a branch if its head changed during the API operations, even
    # if a racing commit happens to be an ancestor of our proposed merge.
    require(ref_head(api, prefix, branch, optional=True) == head_sha, "Reserved branch changed during preparation; no ref was overwritten")
    if head_sha is None:
        api.request("POST", prefix + "/git/refs", {"ref": "refs/heads/" + branch, "sha": new_sha})
    else:
        api.request("PATCH", prefix + "/git/refs/heads/" + quote(branch, safe=""), {"sha": new_sha, "force": False})
    require(ref_head(api, prefix, branch) == new_sha, "New sync branch head could not be confirmed")
    if pr is None:
        # A failed API call can leave the branch behind. Retrying will inspect
        # real PR history and cannot create a synthetic or duplicate PR.
        pr = api.request("POST", prefix + "/pulls", {"title": title, "body": body, "head": branch, "base": base, "draft": draft})
    else:
        pr = api.request("PATCH", prefix + "/pulls/" + str(pr["number"]), {"title": title, "body": body})
    require(isinstance(pr, dict) and type(pr.get("number")) is int and pr["number"] > 0 and pr.get("state") == "open",
            "GitHub did not confirm an open sync pull request")
    return {"pull_request_number": pr["number"], "head_sha": new_sha, "branch": branch,
            "pull_request_url": f"https://github.com/{repository}/pull/{pr['number']}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("repository", "base", "branch", "title"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--draft", choices=("true", "false"), required=True)
    parser.add_argument("--outputs", type=Path)
    args = parser.parse_args()
    try:
        result = open_sync_pr(GitHub(), args.repository, args.base, args.branch, args.title,
                              args.body_file.read_text(encoding="utf-8"), args.root, args.draft == "true")
        if args.outputs:
            with args.outputs.open("a", encoding="utf-8") as output:
                for key, value in result.items():
                    output.write(f"{key}={value}\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (GitHubError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"Sync PR creation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
