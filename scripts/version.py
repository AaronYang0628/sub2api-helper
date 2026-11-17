#!/usr/bin/env python3
"""Synchronize current release metadata from helper/VERSION, without changing history.

Public functions accept an explicit checkout root for release tooling and tests.
Only current Docker/development README sections are updated. Upstream review
baselines, changelogs, dependency versions, and historical artifacts are untouched.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
VERSION = re.compile(VERSION_PATTERN)
COMPOSE_IMAGE = re.compile(
    rf"^(?P<prefix>[ \t]+image:[ \t]*)(?P<quote>[\"']?)(?P<image>sub2api-helper:(?P<version>{VERSION_PATTERN}))(?P=quote)(?P<suffix>[ \t]*(?:#[^\r\n]*)?)$",
    re.MULTILINE,
)
README_IMAGE = re.compile(rf"(?<![A-Za-z0-9_./-])sub2api-helper:(?P<version>{VERSION_PATTERN})(?![A-Za-z0-9_.+-])")
README_CURRENT_SECTIONS = frozenset(("Docker 部署", "本地开发与验证"))


def validate_version(value: str) -> str:
    if not isinstance(value, str) or VERSION.fullmatch(value) is None:
        raise ValueError("Version must be numeric MAJOR.MINOR.PATCH, with no v prefix, leading zeros or prerelease suffix")
    return value


def read_version(root: Path = ROOT) -> str:
    value = (Path(root) / "helper/VERSION").read_text(encoding="ascii").rstrip("\r\n")
    return validate_version(value)


def check_tag(tag: str, version: str) -> None:
    tag_version = tag[1:] if tag.startswith("v") else tag
    validate_version(tag_version)
    if tag_version != validate_version(version):
        raise ValueError("Release tag does not match helper/VERSION")


def _json_document(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return document


def _compose_version(text: str) -> str:
    matches = list(COMPOSE_IMAGE.finditer(text))
    if len(matches) != 1:
        raise ValueError("compose.yaml must have exactly one literal sub2api-helper:MAJOR.MINOR.PATCH image")
    return matches[0]["version"]


def _readme_sections(text: str):
    """Yield lines and whether they belong to live usage examples, not history."""
    section = None
    for line in text.splitlines(keepends=True):
        heading = re.match(r"^##[ \t]+(.+?)[ \t]*\r?\n?$", line)
        if heading:
            section = heading[1]
        yield line, section in README_CURRENT_SECTIONS


def _readme_versions(text: str) -> list[str]:
    return [match["version"] for line, current in _readme_sections(text) if current
            for match in README_IMAGE.finditer(line)]


def _readme_with_version(text: str, version: str) -> str:
    if not _readme_versions(text):
        raise ValueError("README.md has no current sub2api-helper image examples in the Docker/development sections")
    return "".join(README_IMAGE.sub(lambda match: "sub2api-helper:" + version, line) if current else line
                   for line, current in _readme_sections(text))


def _runtime_version(root: Path) -> str:
    # A fresh interpreter sees the newly written resource, not a cached import.
    result = subprocess.run(
        [sys.executable, "-c", "from helper import __version__; print(__version__)"],
        cwd=root, capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode:
        raise ValueError("helper runtime cannot read its packaged VERSION resource")
    return validate_version(result.stdout.rstrip("\r\n"))


def check_versions(root: Path = ROOT) -> list[str]:
    """Return discrepancies; an empty list means all current release metadata agrees."""
    root = Path(root)
    errors = []
    try:
        version = read_version(root)
    except (OSError, UnicodeError, ValueError) as error:
        return [f"helper/VERSION: {error}"]
    checks = (
        ("helper runtime", lambda: [_runtime_version(root)]),
        ("compose.yaml", lambda: [_compose_version((root / "compose.yaml").read_text(encoding="utf-8"))]),
        ("ui/package.json", lambda: [_json_document(root / "ui/package.json").get("version")]),
        ("ui/package-lock.json", lambda: [_json_document(root / "ui/package-lock.json").get("version"),
                                         _json_document(root / "ui/package-lock.json")["packages"][""].get("version")]),
        ("README.md current image examples", lambda: _readme_versions((root / "README.md").read_text(encoding="utf-8"))),
    )
    for label, get_values in checks:
        try:
            values = get_values()
            if not values or any(value != version for value in values):
                errors.append(f"{label} does not match helper/VERSION ({version})")
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
            errors.append(f"{label} could not be checked")
    return errors


def _atomic_write(path: Path, value: str, mode: int):
    fd, temporary = tempfile.mkstemp(prefix=".version-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def set_version(version: str, root: Path = ROOT) -> None:
    """Validate all inputs first, then synchronize known release-owned fields.

    JSON is parsed and only package root versions change, never dependencies.
    Existing values may be inconsistent: this operation deliberately repairs them.
    """
    version = validate_version(version)
    root = Path(root)
    read_version(root)
    paths = [root / name for name in ("helper/VERSION", "compose.yaml", "ui/package.json", "ui/package-lock.json", "README.md")]
    originals = {path: path.read_text(encoding="utf-8") for path in paths}
    modes = {path: path.stat().st_mode & 0o777 for path in paths}
    compose = originals[root / "compose.yaml"]
    _compose_version(compose)
    package = _json_document(root / "ui/package.json")
    lock = _json_document(root / "ui/package-lock.json")
    if not isinstance(package.get("version"), str) or not isinstance(lock.get("version"), str):
        raise ValueError("UI package and lockfile root versions must exist")
    try:
        lock_root = lock["packages"][""]
    except (KeyError, TypeError):
        raise ValueError("UI lockfile must include root package metadata") from None
    if not isinstance(lock_root, dict) or not isinstance(lock_root.get("version"), str):
        raise ValueError("UI lockfile root package version must exist")
    package["version"] = version
    lock["version"] = lock_root["version"] = version
    replacements = {
        root / "helper/VERSION": version + "\n",
        root / "compose.yaml": COMPOSE_IMAGE.sub(lambda match: match["prefix"] + match["quote"] + "sub2api-helper:" + version + match["quote"] + match["suffix"], compose),
        root / "ui/package.json": json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        root / "ui/package-lock.json": json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
        root / "README.md": _readme_with_version(originals[root / "README.md"], version),
    }
    written = []
    try:
        for path, content in replacements.items():
            if content != originals[path]:
                _atomic_write(path, content, modes[path])
                written.append(path)
    except OSError:
        for path in reversed(written):
            _atomic_write(path, originals[path], modes[path])
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", dest="new_version", metavar="VERSION", help="Set the canonical version and synchronize current release metadata")
    parser.add_argument("--check", action="store_true", help="Fail if runtime, Compose, UI package or current README examples disagree")
    parser.add_argument("--tag", help="Also verify an exact VERSION or vVERSION release tag")
    args = parser.parse_args(argv)
    try:
        version = validate_version(args.new_version) if args.new_version is not None else read_version(ROOT)
        # Reject a misleading tag before any file can be changed.
        if args.tag is not None:
            check_tag(args.tag, version)
        if args.new_version is not None:
            set_version(version, ROOT)
        if args.check or args.new_version is not None or args.tag is not None:
            errors = check_versions(ROOT)
            if errors:
                print("\n".join(errors), file=sys.stderr)
                return 1
        print(version)
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Version check failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
