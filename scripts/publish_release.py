#!/usr/bin/env python3
"""Validate a merged upstream-sync proposal and finish an immutable release.

No privileged step executes pull-request artifacts. Callers must run this file
from a trusted default-branch checkout and use the exact tested commit as the
separate image build context. GitHub and Docker operations are injectable for
offline tests.
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
    from .sync_upstream_release import approval_valid, canonical, normalize_release, proposed_baseline, version_tuple
except ImportError:
    from sync_upstream_release import approval_valid, canonical, normalize_release, proposed_baseline, version_tuple


UPSTREAM = "Wei-Shaw/sub2api"
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class PublishError(RuntimeError):
    """Only fixed, non-credential-bearing messages may be used here."""


def require(condition, message):
    if not condition:
        raise PublishError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PublishError("JSON contains duplicate keys")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise PublishError("Invalid JSON data") from None


def optional_get(api, path):
    try:
        return api.request("GET", path)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            return None
        raise


def pages(api, path, field=None):
    items = []
    for page in range(1, 101):
        separator = "&" if "?" in path else "?"
        value = api.request("GET", path + separator + urlencode({"per_page": 100, "page": page}))
        if field:
            require(isinstance(value, dict), "GitHub pagination response is invalid")
            value = value.get(field)
        require(isinstance(value, list), "GitHub pagination response is invalid")
        require(all(isinstance(item, dict) for item in value), "GitHub pagination item is invalid")
        items.extend(value)
        if len(value) < 100:
            return items
    raise PublishError("GitHub pagination limit reached; refusing an incomplete result")


def read_file(api, repository, sha, path):
    result = api.request("GET", f"/repos/{repository}/contents/{path}?" + urlencode({"ref": sha}))
    require(isinstance(result, dict) and result.get("type") == "file" and result.get("encoding") == "base64", "Exact-commit file response is invalid")
    encoded = result.get("content")
    require(isinstance(encoded, str) and len(encoded) <= 2_000_000, "Exact-commit file content is invalid or too large")
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
        require(len(raw) <= 1_000_000, "Exact-commit file content is too large")
        return raw.decode("utf-8")
    except (ValueError, UnicodeError):
        raise PublishError("Exact-commit file encoding is invalid") from None


def tag_commit(api, repository, tag, optional=False):
    path = f"/repos/{repository}/git/ref/tags/{quote(tag, safe='')}"
    value = optional_get(api, path) if optional else api.request("GET", path)
    if value is None:
        return None
    require(isinstance(value, dict), "Git tag response is invalid")
    obj, seen = value.get("object"), set()
    for _ in range(10):
        require(isinstance(obj, dict) and isinstance(obj.get("sha"), str) and SHA.fullmatch(obj["sha"]), "Git tag target is invalid")
        if obj.get("type") == "commit":
            return obj["sha"]
        require(obj.get("type") == "tag" and obj["sha"] not in seen, "Git tag cannot be resolved to one commit")
        seen.add(obj["sha"])
        nested = api.request("GET", f"/repos/{repository}/git/tags/{obj['sha']}")
        require(isinstance(nested, dict), "Annotated Git tag response is invalid")
        obj = nested.get("object")
    raise PublishError("Annotated Git tag nesting limit reached")


def _release_valid(release, repository, tag):
    return (isinstance(release, dict) and release.get("tag_name") == tag
            and release.get("draft") is False and release.get("prerelease") is False
            and type(release.get("id")) is int and release["id"] > 0
            and release.get("html_url") == f"https://github.com/{repository}/releases/tag/{tag}")


def validate_parameters(repository, sha, default_branch):
    require(isinstance(repository, str) and REPOSITORY.fullmatch(repository), "Invalid repository name")
    require(isinstance(sha, str) and SHA.fullmatch(sha), "An exact lowercase 40-character commit SHA is required")
    require(isinstance(default_branch, str) and default_branch and not any(c in default_branch for c in "\r\n"), "Invalid default branch")


def is_release_candidate(api, repository, sha, default_branch):
    """An ordinary successful push is not a publication failure or candidate."""
    validate_parameters(repository, sha, default_branch)
    root = f"/repos/{repository}"
    info = api.request("GET", root)
    require(isinstance(info, dict) and str(info.get("full_name", "")).lower() == repository.lower()
            and info.get("default_branch") == default_branch and type(info.get("id")) is int,
            "Repository identity or default branch does not match")
    for pr in pages(api, root + f"/commits/{sha}/pulls"):
        head, base = pr.get("head") or {}, pr.get("base") or {}
        branch = head.get("ref")
        if (pr.get("merged_at") and pr.get("merge_commit_sha") == sha and pr.get("state") == "closed"
                and (head.get("repo") or {}).get("id") == info["id"]
                and base.get("ref") == default_branch and (base.get("repo") or {}).get("id") == info["id"]
                and isinstance(branch, str) and re.fullmatch(r"automation/upstream-v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", branch)):
            return True
    return False


def guard(api, repository, sha, default_branch):
    validate_parameters(repository, sha, default_branch)
    root = f"/repos/{repository}"
    info = api.request("GET", root)
    require(isinstance(info, dict) and str(info.get("full_name", "")).lower() == repository.lower()
            and info.get("default_branch") == default_branch and type(info.get("id")) is int,
            "Repository identity or default branch does not match")
    repository_id = info["id"]
    compare = api.request("GET", root + f"/compare/{sha}...{quote(default_branch, safe='')}")
    require(isinstance(compare, dict) and compare.get("status") in {"ahead", "identical"}, "Release commit is not an ancestor of the current default branch")
    require(compare.get("merge_base_commit", {}).get("sha") == sha, "Release commit ancestry could not be confirmed")

    manifest = strict_json(read_file(api, repository, sha, "docs/upstream-release.json"))
    approval = strict_json(read_file(api, repository, sha, "docs/upstream-release-approval.json"))
    baseline = strict_json(read_file(api, repository, sha, "docs/upstream-baseline.json"))
    version = read_file(api, repository, sha, "helper/VERSION").strip()
    require(isinstance(manifest, dict), "Release manifest is invalid")
    try:
        require(version == ".".join(map(str, version_tuple(version))), "Helper version must be canonical MAJOR.MINOR.PATCH")
        require(manifest.get("version") == version and version_tuple(manifest.get("tag")) == version_tuple(version), "Manifest, tag and helper version disagree")
        require(isinstance(manifest.get("target_sha"), str) and SHA.fullmatch(manifest["target_sha"]), "Upstream target SHA is invalid")
        require(type(manifest.get("requires_review")) is bool, "Compatibility review requirement is missing")
        fingerprint = manifest.get("fingerprint")
        expected = hashlib.sha256(canonical({key: value for key, value in manifest.items() if key != "fingerprint"})).hexdigest()
        require(isinstance(fingerprint, str) and SHA256.fullmatch(fingerprint) and fingerprint == expected, "Release evidence fingerprint does not match")
        require(approval_valid(approval, manifest), "This exact upstream commit and evidence fingerprint require compatibility approval")
        require(baseline == proposed_baseline(manifest), "Reviewed baseline does not match the approved target and evidence")
        for source, mirror in (("frontend/src/style.css", "ui/upstream.css"), ("frontend/tailwind.config.js", "ui/upstream.tailwind.config.js")):
            expected_hash = manifest["evidence"]["pinned_hashes"].get(source)
            require(isinstance(expected_hash, str) and SHA256.fullmatch(expected_hash), "Upstream UI mirror fingerprint is missing")
            actual_hash = hashlib.sha256(read_file(api, repository, sha, mirror).encode("utf-8")).hexdigest()
            require(actual_hash == expected_hash, "Helper UI mirror differs from the approved upstream source")
    except (KeyError, TypeError, ValueError):
        raise PublishError("Release manifest or compatibility evidence is invalid") from None

    query = urlencode({"head_sha": sha, "branch": default_branch, "event": "push", "status": "success"})
    runs = pages(api, root + "/actions/workflows/ci.yaml/runs?" + query, "workflow_runs")
    valid_runs = [run for run in runs if run.get("head_sha") == sha and run.get("head_branch") == default_branch
                  and run.get("event") == "push" and run.get("status") == "completed" and run.get("conclusion") == "success"
                  and (run.get("head_repository") or {}).get("id") == repository_id
                  and type(run.get("id")) is int and run["id"] > 0]
    require(valid_runs, "No successful default-branch push CI was found for this exact commit")
    prs = pages(api, root + f"/commits/{sha}/pulls")
    valid_prs = []
    for pr in prs:
        head, base = pr.get("head") or {}, pr.get("base") or {}
        if (pr.get("merged_at") and pr.get("merge_commit_sha") == sha and pr.get("state") == "closed"
                and type(pr.get("number")) is int and pr["number"] > 0
                and head.get("ref") == "automation/upstream-v" + version
                and (head.get("repo") or {}).get("id") == repository_id
                and base.get("ref") == default_branch and (base.get("repo") or {}).get("id") == repository_id):
            valid_prs.append(pr)
    require(valid_prs, "Commit is not the merge of a same-repository upstream-version sync PR")

    tag = manifest["tag"]
    upstream_release = api.request("GET", f"/repos/{UPSTREAM}/releases/tags/{quote(tag, safe='')}")
    normalized = normalize_release(upstream_release)
    require(normalized is not None and all(normalized.get(key) == manifest.get(key) for key in ("version", "tag", "release_id", "release_url", "published_at")), "Official upstream Release identity changed or is not a published stable release")
    require(tag_commit(api, UPSTREAM, tag) == manifest["target_sha"], "Official upstream tag no longer points to the reviewed commit")
    existing_tag = tag_commit(api, repository, tag, optional=True)
    require(existing_tag is None or existing_tag == sha, "Existing helper tag points to a different commit; refusing to move it")
    existing_release = optional_get(api, root + f"/releases/tags/{quote(tag, safe='')}")
    if existing_release is not None:
        require(_release_valid(existing_release, repository, tag) and existing_tag == sha, "Existing helper Release is incompatible with this publication")
    newer = False
    for release in pages(api, root + "/releases"):
        if release.get("draft") is not False or release.get("prerelease") is not False:
            continue
        try:
            own_version = version_tuple(release.get("tag_name"))
        except (TypeError, ValueError):
            continue  # Old non-version releases have no ordering relationship.
        require(isinstance(release.get("published_at"), str) and bool(release["published_at"]), "Existing stable helper release metadata is incomplete")
        if own_version > version_tuple(version):
            newer = True
    require(not newer or existing_tag == sha or existing_release is not None,
            "A newer helper release already exists; default-branch backports are unsupported without an existing publication intent")
    return {"schema_version": 1, "repository": repository, "default_branch": default_branch, "version": version,
            "tag": tag, "sha": sha, "image": "ghcr.io/" + repository.lower(), "release_url": manifest["release_url"],
            "upstream_sha": manifest["target_sha"], "release_id": manifest["release_id"], "published_at": manifest["published_at"], "fingerprint": fingerprint,
            "pr_number": valid_prs[0]["number"], "ci_run_id": max(run["id"] for run in valid_runs), "older_recovery": newer}


def inspect_image(image, version, sha, runner=None):
    require(isinstance(image, str) and re.fullmatch(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+:v?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", image), "Image must be an explicit GHCR semantic-version tag")
    require(version_tuple(image.rsplit(":", 1)[1]) == version_tuple(version) and SHA.fullmatch(sha), "Image version or commit is invalid")
    runner = runner or subprocess.run
    try:
        result = runner(["docker", "buildx", "imagetools", "inspect", image, "--format", "{{.Manifest.Digest}}"], capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise PublishError("Cannot inspect registry manifest") from None
    if result.returncode:
        error = str(result.stderr).lower()
        denied = any(word in error for word in ("unauthorized", "forbidden", "denied", "401", "403", "timeout", "certificate", "tls", "connection", "429", "insufficient_scope", "authentication")) or bool(re.search(r"\b5[0-9]{2}\b", error))
        missing = "manifest unknown" in error or "manifest_unknown" in error or "name unknown" in error or "name_unknown" in error or bool(re.search(r"(?:error:\s*)?" + re.escape(image.lower()) + r": not found\s*$", error))
        require(missing and not denied, "Registry lookup failed; absence was not confirmed")
        return {"exists": False}
    digest = result.stdout.strip()
    require(DIGEST.fullmatch(digest), "Existing image digest was not confirmed")
    # Tags can move between requests. Validate the immutable resolved digest,
    # never one tag response followed by a separately resolved tag digest.
    immutable = image.rsplit(":", 1)[0] + "@" + digest
    try:
        result = runner(["docker", "buildx", "imagetools", "inspect", immutable, "--raw"], capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise PublishError("Cannot inspect the immutable registry manifest") from None
    require(result.returncode == 0, "Resolved image manifest could not be verified")
    require(len(result.stdout) <= 4_000_000, "Registry manifest is too large")
    value = strict_json(result.stdout)
    require(isinstance(value, dict) and value.get("mediaType") in {"application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json"}, "Existing version is not a multi-platform image index")
    annotations = value.get("annotations") or {}
    require(isinstance(annotations, dict) and annotations.get("org.opencontainers.image.revision") == sha and annotations.get("org.opencontainers.image.version") == version,
            "Existing image version or revision differs; refusing to overwrite the tag")
    manifests = value.get("manifests")
    require(isinstance(manifests, list) and all(isinstance(item, dict) for item in manifests), "Image platform descriptors are invalid")
    require(all(isinstance(item.get("platform"), dict) for item in manifests), "Image platform fields are invalid")
    platforms = {(item["platform"].get("os"), item["platform"].get("architecture")) for item in manifests}
    require({("linux", "amd64"), ("linux", "arm64")} <= platforms, "Existing image does not contain both required Linux platforms")
    return {"exists": True, "digest": digest}


def reserve(api, metadata):
    """Reserve the exact helper tag as durable intent before registry writes."""
    require(isinstance(metadata, dict), "Verified publication metadata is required")
    try:
        fresh = guard(api, metadata["repository"], metadata["sha"], metadata["default_branch"])
    except KeyError:
        raise PublishError("Verified metadata is incomplete") from None
    for key in ("schema_version", "repository", "default_branch", "sha", "version", "tag", "image", "release_url", "upstream_sha", "release_id", "published_at", "fingerprint", "pr_number"):
        require(metadata.get(key) == fresh[key], "Saved publication metadata does not match refreshed provenance")
    repository, tag, sha = fresh["repository"], fresh["tag"], fresh["sha"]
    root = f"/repos/{repository}"
    current = tag_commit(api, repository, tag, optional=True)
    if current is None:
        try:
            api.request("POST", root + "/git/refs", {"ref": "refs/tags/" + tag, "sha": sha})
        except Exception as exc:
            if getattr(exc, "status", None) != 422:
                raise
        current = tag_commit(api, repository, tag)
    require(current == sha, "Helper tag points to a different commit; refusing to overwrite it")
    return fresh


def publication_evidence(metadata, digest):
    return {"schema_version": 1, **{key: metadata[key] for key in ("version", "tag", "sha", "upstream_sha", "fingerprint", "image")},
            "digest": digest, "platforms": ["linux/amd64", "linux/arm64"]}


def verify_release_evidence(release, expected):
    body = release.get("body")
    require(isinstance(body, str), "Existing GitHub Release has no verifiable publication evidence")
    matches = re.findall(r"<!-- sub2api-helper-publication (\{.*?\}) -->", body, re.DOTALL)
    require(len(matches) == 1 and strict_json(matches[0]) == expected,
            "Existing GitHub Release image digest or publication evidence differs; refusing to overwrite it")


def finish(api, metadata, digest):
    require(isinstance(digest, str) and DIGEST.fullmatch(digest), "A sha256 image digest is required")
    fresh = reserve(api, metadata)
    repository, tag, sha = fresh["repository"], fresh["tag"], fresh["sha"]
    root = f"/repos/{repository}"
    evidence = publication_evidence(fresh, digest)
    existing = optional_get(api, root + f"/releases/tags/{quote(tag, safe='')}")
    if existing is not None:
        require(_release_valid(existing, repository, tag), "Existing GitHub Release differs from the verified publication")
        verify_release_evidence(existing, evidence)
        return existing
    body = (f"Synced with [Wei-Shaw/sub2api {tag}]({fresh['release_url']}).\n\n"
            f"- Upstream commit: `{fresh['upstream_sha']}`\n- Helper commit: `{sha}`\n"
            f"- Compatibility evidence: `{fresh['fingerprint']}` (PR #{fresh['pr_number']})\n"
            f"- Image: `{fresh['image']}:{tag}`\n- Image digest: `{digest}`\n"
            "- Platforms: `linux/amd64`, `linux/arm64`\n\n"
            "<!-- sub2api-helper-publication " + canonical(evidence).decode("ascii") + " -->\n")
    try:
        release = api.request("POST", root + "/releases", {"tag_name": tag, "target_commitish": sha,
            "name": "sub2api-helper " + tag, "body": body, "draft": False, "prerelease": False,
            # Per-version workflows may finish out of order. Never mutate a
            # floating GitHub Latest alias (the registry also has no latest tag).
            "make_latest": "false"})
    except Exception as exc:
        if getattr(exc, "status", None) != 422:
            raise
        release = api.request("GET", root + f"/releases/tags/{quote(tag, safe='')}")
    require(_release_valid(release, repository, tag), "GitHub did not confirm the published Release")
    verify_release_evidence(release, evidence)
    return release


def outputs(path, values):
    if path is None:
        return
    with Path(path).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            value = str(value).lower() if isinstance(value, bool) else str(value)
            require("\n" not in value and "\r" not in value, "Unsafe workflow output")
            output.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("guard")
    for name in ("repository", "sha", "default-branch", "metadata-path", "outputs"):
        check.add_argument("--" + name, required=True)
    check.add_argument("--skip-unrelated", action="store_true")
    inspect = commands.add_parser("inspect-image")
    for name in ("image", "version", "sha", "outputs"):
        inspect.add_argument("--" + name, required=True)
    complete = commands.add_parser("finish")
    complete.add_argument("--metadata-path", required=True)
    complete.add_argument("--digest", required=True)
    reservation = commands.add_parser("reserve")
    reservation.add_argument("--metadata-path", required=True)
    args = parser.parse_args()
    try:
        if args.command == "inspect-image":
            outputs(args.outputs, inspect_image(args.image, args.version, args.sha))
            return 0
        try:
            from .github_api import GitHub
        except ImportError:
            from github_api import GitHub
        api = GitHub()
        if args.command == "guard":
            if args.skip_unrelated and not is_release_candidate(api, args.repository, args.sha, args.default_branch):
                outputs(args.outputs, {"eligible": False})
                print("No merged upstream-version sync PR at this commit; publication skipped")
                return 0
            metadata = guard(api, args.repository, args.sha, args.default_branch)
            path = Path(args.metadata_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            outputs(args.outputs, {"eligible": True, **{key: metadata[key] for key in ("version", "tag", "sha", "image", "release_url", "upstream_sha")}})
        elif args.command == "reserve":
            fresh = reserve(api, strict_json(Path(args.metadata_path).read_text(encoding="utf-8")))
            print("Reserved Git tag: " + fresh["tag"])
        else:
            release = finish(api, strict_json(Path(args.metadata_path).read_text(encoding="utf-8")), args.digest)
            print("Published Release: " + release["html_url"])
    except Exception as exc:
        message = str(exc) if isinstance(exc, PublishError) else (f"GitHub operation failed (HTTP {exc.status})" if type(getattr(exc, "status", None)) is int else type(exc).__name__)
        print("Publication failed: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
