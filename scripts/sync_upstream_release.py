#!/usr/bin/env python3
"""Prepare and verify a release-sync PR without GitHub network access.

Only the explicit ``approve`` command records a compatibility approval for
runtime-affecting changes. ``prepare`` still produces a version-sync proposal
when there are no relevant changes; publishing is a separate post-merge job.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


REPOSITORY = "https://github.com/Wei-Shaw/sub2api"
VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BASELINE_PATH = "docs/upstream-baseline.json"
MANIFEST_PATH = "docs/upstream-release.json"
APPROVAL_PATH = "docs/upstream-release-approval.json"


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Release version must be optional-v MAJOR.MINOR.PATCH without prerelease/build suffixes")
    return tuple(map(int, match.groups()))


def normalize_release(record: dict) -> dict | None:
    if not isinstance(record, dict) or record.get("draft") is not False or record.get("prerelease") is not False:
        return None
    if type(record.get("id")) is not int or record["id"] <= 0:
        return None
    published = record.get("published_at")
    if not isinstance(published, str):
        return None
    try:
        if datetime.fromisoformat(published.replace("Z", "+00:00")).tzinfo is None:
            return None
        numbers = version_tuple(record.get("tag_name"))
    except (ValueError, TypeError):
        return None
    tag = record["tag_name"]
    official_url = REPOSITORY + "/releases/tag/" + tag
    if record.get("html_url", official_url) != official_url:
        return None
    return {"version": ".".join(map(str, numbers)), "tag": tag,
            "release_id": record["id"], "release_url": official_url,
            "published_at": published}


def select_release(records: list[dict], current: str) -> dict | None:
    """Select the oldest newer stable release so intermediate releases get PRs."""
    current_version = version_tuple(current)
    eligible = [(version_tuple(item["version"]), item, record) for record in records
                if (item := normalize_release(record)) is not None and version_tuple(item["version"]) > current_version]
    if not eligible:
        return None
    _, normalized, original = min(eligible, key=lambda item: (item[0], item[1]["tag"]))
    return {**original, **normalized}


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(source: Path, *arguments: str, binary=False):
    result = subprocess.run(["git", "-C", str(source), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ValueError("Cannot read exact upstream tag or reviewed commit; fetch both commits before running this command")
    return result.stdout if binary else result.stdout.decode().strip()


def file_hash(source: Path, relative: str) -> str | None:
    pure = PurePosixPath(relative)
    path = source / relative
    if pure.is_absolute() or ".." in pure.parts or not path.resolve().is_relative_to(source):
        raise ValueError("Unsafe upstream path")
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("Upstream evidence paths must be regular files")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_baseline(value: dict) -> None:
    if not isinstance(value, dict) or not SHA.fullmatch(str(value.get("reviewed_commit", ""))):
        raise ValueError("Invalid reviewed baseline commit")
    version_tuple(value.get("reviewed_version"))
    hashes = value.get("files")
    if not isinstance(hashes, dict) or not hashes or any(not isinstance(path, str) or not SHA256.fullmatch(str(digest)) for path, digest in hashes.items()):
        raise ValueError("Invalid reviewed baseline hashes")


def change_scope(path: str) -> str:
    if path == "backend/cmd/server/VERSION":
        return "version-metadata"
    # Only positively identified documentation is classified as non-runtime.
    # Unknown root paths, build workflows, dependencies and new code all require
    # review rather than being silently ignored by the pinned-file list.
    if (path.startswith("docs/") and PurePosixPath(path).suffix.lower() in {".md", ".rst", ".txt", ".adoc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}) or re.fullmatch(r"README(?:_[A-Za-z]+)?\.md", path):
        return "documentation"
    if path.startswith(("backend/", "frontend/", "deploy/")):
        return "runtime-or-interface"
    return "unclassified-review-required"


def build_manifest(source: Path, release: dict, previous: dict) -> dict:
    validate_baseline(previous)
    if not source.is_dir() or Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("--source must be the upstream checkout root")
    target = git(source, "rev-parse", "--verify", "HEAD^{commit}")
    tag_target = git(source, "rev-parse", "--verify", "refs/tags/" + release["tag"] + "^{commit}")
    if not SHA.fullmatch(target) or tag_target != target:
        raise ValueError("Upstream HEAD must equal the exact official release tag")
    if git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Upstream checkout contains local changes; release evidence must come from the clean exact tag")
    old = previous["reviewed_commit"]
    git(source, "cat-file", "-e", old + "^{commit}")
    for path, expected in previous["files"].items():
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError("Unsafe reviewed baseline path")
        recorded = git(source, "show", old + ":" + path, binary=True)
        if hashlib.sha256(recorded).hexdigest() != expected:
            raise ValueError("Previous baseline hashes do not match its recorded upstream commit")
    # Two-tree comparison deliberately works with separately fetched depth-1
    # commits and does not require a merge base or ancestor relationship.
    fields = git(source, "diff", "--name-status", "--no-renames", "-z", old, target, "--", binary=True).split(b"\0")
    if fields[-1:] == [b""]:
        fields.pop()
    if len(fields) % 2:
        raise ValueError("Invalid upstream diff output")
    changes = []
    for offset in range(0, len(fields), 2):
        status, path = fields[offset].decode(), fields[offset + 1].decode()
        if status not in {"A", "D", "M", "T"}:
            raise ValueError("Unsupported upstream diff status")
        changes.append({"path": path, "status": status, "scope": change_scope(path), "sha256": file_hash(source, path)})
    hashes = {path: file_hash(source, path) for path in sorted(previous["files"])}
    pinned = [path for path, digest in hashes.items() if digest != previous["files"][path]]
    relevant = [change for change in changes if change["scope"] not in {"documentation", "version-metadata"}]
    # Pinned files may intentionally cover documents; their changes still need
    # review unless they are the release's own version metadata.
    pinned_relevant = [path for path in pinned if path != "backend/cmd/server/VERSION"]
    embedded_path = source / "backend/cmd/server/VERSION"
    embedded_version = embedded_path.read_text(encoding="utf-8").strip() if embedded_path.is_file() else None
    evidence = {"all_changes": sorted(changes, key=lambda change: change["path"]),
                "relevant_changes": sorted(relevant, key=lambda change: change["path"]),
                "pinned_hashes": hashes, "changed_pinned_files": pinned,
                "embedded_version": embedded_version,
                "embedded_version_matches_release": embedded_version == release["version"]}
    manifest = {**release, "target_sha": target, "previous_baseline": previous, "evidence": evidence,
                "requires_review": bool(relevant or pinned_relevant)}
    manifest["fingerprint"] = hashlib.sha256(canonical(manifest)).hexdigest()
    return manifest


def proposed_baseline(manifest: dict) -> dict:
    baseline = dict(manifest["previous_baseline"])
    hashes = {path: digest for path, digest in manifest["evidence"]["pinned_hashes"].items() if digest is not None}
    for item in manifest["evidence"]["relevant_changes"]:
        if item["sha256"] is not None:
            hashes[item["path"]] = item["sha256"]
    baseline.update(reviewed_commit=manifest["target_sha"], reviewed_version=manifest["version"], files=dict(sorted(hashes.items())))
    return baseline


def approval_valid(approval, manifest: dict) -> bool:
    return (isinstance(approval, dict) and approval.get("target_sha") == manifest["target_sha"]
            and approval.get("fingerprint") == manifest["fingerprint"]
            and approval.get("status") in ({"approved"} if manifest["requires_review"] else {"approved", "not-required"}))


def version_tools():
    path = Path(__file__).with_name("version.py")
    specification = importlib.util.spec_from_file_location("helper_release_version", path)
    if specification is None or specification.loader is None:
        raise ValueError("Version tooling is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def validate_vendor_assets(source: Path, root: Path) -> None:
    for target, vendored in (("frontend/src/style.css", "ui/upstream.css"),
                             ("frontend/tailwind.config.js", "ui/upstream.tailwind.config.js")):
        expected = file_hash(source, target)
        if expected is None or file_hash(root, vendored) != expected:
            raise ValueError(f"Vendored UI source {vendored} differs from release {target}; adapt and rebuild the UI before approval")


def release_for_command(records, command: str, current: str, existing) -> dict:
    if isinstance(records, dict):
        release = normalize_release(records)
    elif isinstance(records, list):
        selected = select_release(records, current) if command == "prepare" else None
        release = normalize_release(selected) if selected is not None else None
        if release is None and isinstance(existing, dict):
            release = next((item for record in records if (item := normalize_release(record)) is not None and item["tag"] == existing.get("tag")), None)
    else:
        raise ValueError("Release metadata must be one GitHub release object or an array")
    if release is None:
        raise ValueError("No eligible published stable release found")
    if version_tuple(release["version"]) < version_tuple(current):
        raise ValueError("Refusing to downgrade the helper version")
    return release


def run(command: str, release_json: Path, source: Path, root: Path, outputs: Path | None = None) -> dict:
    tools = version_tools()
    current = tools.read_version(root)
    existing = read_json(root / MANIFEST_PATH) if (root / MANIFEST_PATH).exists() else None
    records = read_json(release_json)
    selection_command = command
    if command == "prepare" and isinstance(records, list) and isinstance(existing, dict) and source.is_dir():
        if git(source, "rev-parse", "--verify", "HEAD^{commit}") == existing.get("target_sha"):
            selection_command = "check"  # same pinned source is an idempotent re-prepare
    release = release_for_command(records, selection_command, current, existing)
    baseline = read_json(root / BASELINE_PATH)
    if isinstance(existing, dict) and existing.get("tag") == release["tag"]:
        previous = existing.get("previous_baseline")
    else:
        previous = baseline
    manifest = build_manifest(source.resolve(), release, previous)
    approval = read_json(root / APPROVAL_PATH) if (root / APPROVAL_PATH).exists() else None
    valid = approval_valid(approval, manifest)
    expected_baseline = proposed_baseline(manifest)
    if command == "prepare":
        if manifest["requires_review"] and not valid and baseline != previous:
            raise ValueError("Reviewed baseline was advanced without a matching approval; restore the previous baseline or explicitly approve")
        tools.set_version(manifest["version"], root)
        write_json(root / MANIFEST_PATH, manifest)
        if not manifest["requires_review"]:
            if not valid:
                approval = {"target_sha": manifest["target_sha"], "fingerprint": manifest["fingerprint"], "status": "not-required"}
            write_json(root / APPROVAL_PATH, approval)
            write_json(root / BASELINE_PATH, expected_baseline)
            valid = True
        elif valid:
            write_json(root / BASELINE_PATH, expected_baseline)
        elif (root / APPROVAL_PATH).exists():
            (root / APPROVAL_PATH).unlink()  # stale approval cannot follow a new target
    else:
        if existing != manifest:
            raise ValueError("Saved release proposal does not match freshly computed exact-tag evidence; run prepare again")
        errors = tools.check_versions(root)
        if errors:
            raise ValueError("Version references disagree: " + "; ".join(errors))
        if current != manifest["version"]:
            raise ValueError("Canonical helper version differs from target release")
        validate_vendor_assets(source.resolve(), root.resolve())
        if command == "approve":
            approval = {"target_sha": manifest["target_sha"], "fingerprint": manifest["fingerprint"], "status": "approved"}
            write_json(root / BASELINE_PATH, expected_baseline)
            write_json(root / APPROVAL_PATH, approval)
            valid = True
        if not valid:
            raise ValueError("Runtime or unclassified changes require explicit compatibility approval for this target SHA and evidence fingerprint")
        if read_json(root / BASELINE_PATH) != expected_baseline:
            raise ValueError("Reviewed baseline does not match the approved release target and hashes")
    result = {"version": manifest["version"], "tag": manifest["tag"], "target_sha": manifest["target_sha"],
              "requires_review": manifest["requires_review"], "branch": "automation/upstream-v" + manifest["version"],
              "publishable": valid, "fingerprint": manifest["fingerprint"]}
    if outputs is not None:
        with outputs.open("a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "check", "approve"))
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--helper-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--outputs", type=Path)
    args = parser.parse_args()
    try:
        result = run(args.command, args.release_json, args.source, args.helper_root.resolve(), args.outputs)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"Release sync failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
