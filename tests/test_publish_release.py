import base64
from contextlib import redirect_stderr
from copy import deepcopy
import hashlib
import json
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from scripts.publish_release import PublishError, finish, guard, inspect_image, is_release_candidate, main, outputs, read_file, reserve, tag_commit
from scripts.sync_upstream_release import canonical, proposed_baseline


REPO = "Example/sub2api-helper"
SHA = "a" * 40
UPSTREAM_SHA = "b" * 40
DIGEST = "sha256:" + "d" * 64
IMAGE = "ghcr.io/example/sub2api-helper:v0.2.23"


class APIError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__("safe GitHub error")


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.files = {"helper/VERSION": "0.2.23\n", "ui/upstream.css": ":root{color:blue}\n", "ui/upstream.tailwind.config.js": "module.exports = {};\n"}
        hashes = {"frontend/src/style.css": hashlib.sha256(self.files["ui/upstream.css"].encode()).hexdigest(), "frontend/tailwind.config.js": hashlib.sha256(self.files["ui/upstream.tailwind.config.js"].encode()).hexdigest()}
        self.manifest = {"version": "0.2.23", "tag": "v0.2.23", "release_id": 12, "published_at": "2026-11-17T00:00:00Z", "release_url": "https://github.com/Wei-Shaw/sub2api/releases/tag/v0.2.23", "target_sha": UPSTREAM_SHA,
            "previous_baseline": {"repository": "https://github.com/Wei-Shaw/sub2api", "reviewed_commit": "c" * 40, "reviewed_version": "0.2.22", "files": hashes},
            "evidence": {"all_changes": [], "relevant_changes": [], "pinned_hashes": hashes, "changed_pinned_files": []}, "requires_review": True}
        self.refresh_evidence()
        self.info = {"id": 42, "full_name": REPO, "default_branch": "main"}
        self.compare = {"status": "ahead", "merge_base_commit": {"sha": SHA}}
        self.runs = [{"id": 100, "event": "push", "status": "completed", "conclusion": "success", "head_sha": SHA, "head_branch": "main", "head_repository": {"id": 42}}]
        self.prs = [{"number": 8, "merged_at": "2026-11-17T00:00:00Z", "merge_commit_sha": SHA, "state": "closed", "head": {"ref": "automation/upstream-v0.2.23", "repo": {"id": 42}}, "base": {"ref": "main", "repo": {"id": 42}}}]
        self.upstream_release = {"id": 12, "tag_name": "v0.2.23", "draft": False, "prerelease": False, "published_at": "2026-11-17T00:00:00Z", "html_url": self.manifest["release_url"]}
        self.upstream_tag = {"type": "commit", "sha": UPSTREAM_SHA}
        self.annotated_tags = {}
        self.own_tag = None
        self.own_release = None
        self.own_releases = []
        self.fail_release_after_creation = False
        self.contents_override = None
        self.run_pages = None

    def refresh_evidence(self):
        self.manifest["fingerprint"] = hashlib.sha256(canonical({k: v for k, v in self.manifest.items() if k != "fingerprint"})).hexdigest()
        self.approval = {"target_sha": UPSTREAM_SHA, "fingerprint": self.manifest["fingerprint"], "status": "approved"}
        self.baseline = proposed_baseline(self.manifest)

    def release(self, tag="v0.2.23", release_id=30):
        return {"id": release_id, "tag_name": tag, "draft": False, "prerelease": False, "published_at": "2026-11-18T00:00:00Z", "html_url": f"https://github.com/{REPO}/releases/tag/{tag}"}

    def request(self, method, raw_path, data=None):
        self.calls.append((method, raw_path, deepcopy(data)))
        split = urlsplit(raw_path)
        path, query = unquote(split.path), parse_qs(split.query)
        root = "/repos/" + REPO
        if method == "GET":
            if path == root:
                return deepcopy(self.info)
            if path.startswith(root + "/compare/"):
                return deepcopy(self.compare)
            if path.startswith(root + "/contents/"):
                if self.contents_override:
                    return deepcopy(self.contents_override)
                self.assert_ref(query)
                relative = path.split("/contents/", 1)[1]
                files = self.files | {"docs/upstream-release.json": json.dumps(self.manifest), "docs/upstream-release-approval.json": json.dumps(self.approval), "docs/upstream-baseline.json": json.dumps(self.baseline)}
                if relative not in files:
                    raise APIError(404)
                return {"type": "file", "encoding": "base64", "content": base64.b64encode(files[relative].encode()).decode()}
            if path == root + "/actions/workflows/ci.yaml/runs":
                page = int(query.get("page", ["1"])[0])
                values = self.run_pages.get(page, []) if self.run_pages is not None else self.runs
                return {"workflow_runs": deepcopy(values)}
            if path == root + f"/commits/{SHA}/pulls":
                return deepcopy(self.prs)
            if path.startswith("/repos/Wei-Shaw/sub2api/releases/tags/"):
                return deepcopy(self.upstream_release)
            if path.startswith("/repos/Wei-Shaw/sub2api/git/ref/tags/"):
                return {"object": deepcopy(self.upstream_tag)}
            if path.startswith("/repos/Wei-Shaw/sub2api/git/tags/"):
                return {"object": deepcopy(self.annotated_tags[path.rsplit("/", 1)[1]])}
            if path.startswith(root + "/git/ref/tags/"):
                if self.own_tag is None:
                    raise APIError(404)
                return {"object": {"type": "commit", "sha": self.own_tag}}
            if path.startswith(root + "/releases/tags/"):
                if self.own_release is None:
                    raise APIError(404)
                return deepcopy(self.own_release)
            if path == root + "/releases":
                return deepcopy(self.own_releases)
        if method == "POST" and path == root + "/git/refs":
            self.own_tag = data["sha"]
            return {"object": {"type": "commit", "sha": data["sha"]}}
        if method == "POST" and path == root + "/releases":
            self.own_release = self.release(data["tag_name"])
            self.own_release["body"] = data["body"]
            self.own_releases.append(deepcopy(self.own_release))
            if self.fail_release_after_creation:
                self.fail_release_after_creation = False
                raise APIError(502)
            return deepcopy(self.own_release)
        raise AssertionError("Unexpected fake API operation: " + method + " " + path)

    def assert_ref(self, query):
        if query.get("ref") != [SHA]:
            raise AssertionError("Guard must read exact release commit, never a moving branch")


class PublishGuardTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()

    def guard(self):
        return guard(self.api, REPO, SHA, "main")

    def test_complete_guard_is_read_only_and_returns_canonical_metadata(self):
        metadata = self.guard()
        self.assertEqual(metadata["version"], "0.2.23")
        self.assertEqual(metadata["tag"], "v0.2.23")
        self.assertEqual(metadata["image"], "ghcr.io/example/sub2api-helper")
        self.assertEqual(metadata["sha"], SHA)
        self.assertEqual(metadata["upstream_sha"], UPSTREAM_SHA)
        self.assertEqual(metadata["pr_number"], 8)
        self.assertTrue(all(call[0] == "GET" for call in self.api.calls))

    def test_invalid_sha_is_rejected_before_api_use(self):
        for sha in ("main", "a" * 39, "A" * 40, "a" * 40 + "\n"):
            with self.subTest(sha=sha), self.assertRaises(PublishError):
                guard(self.api, REPO, sha, "main")
        self.assertEqual(self.api.calls, [])

    def test_unreachable_or_unconfirmed_default_branch_ancestry_is_rejected(self):
        for compare in ({"status": "behind", "merge_base_commit": {"sha": SHA}}, {"status": "diverged", "merge_base_commit": {"sha": SHA}}, {"status": "ahead", "merge_base_commit": {"sha": "f" * 40}}):
            self.api.compare = compare
            with self.assertRaises(PublishError):
                self.guard()

    def test_ci_must_be_successful_push_for_same_repository_branch_and_exact_sha(self):
        valid = deepcopy(self.api.runs[0])
        for change in ({"event": "workflow_dispatch"}, {"event": "pull_request"}, {"conclusion": "failure"}, {"head_sha": "f" * 40}, {"head_branch": "automation/upstream-v0.2.23"}, {"head_repository": {"id": 99}}):
            with self.subTest(change=change):
                self.api.runs = [valid | change]
                with self.assertRaises(PublishError):
                    self.guard()

    def test_ci_pagination_does_not_drop_a_later_matching_run(self):
        invalid = self.api.runs[0] | {"event": "pull_request"}
        self.api.run_pages = {1: [invalid] * 100, 2: self.api.runs}
        self.assertEqual(self.guard()["ci_run_id"], 100)

    def test_only_merged_same_repository_sync_pull_request_is_eligible(self):
        valid = deepcopy(self.api.prs[0])
        for change in ({"merged_at": None}, {"merge_commit_sha": "f" * 40}, {"state": "open"}, {"head": {"ref": "feature/arbitrary", "repo": {"id": 42}}}, {"head": {"ref": "automation/upstream-v0.2.23", "repo": {"id": 999}}}, {"base": {"ref": "other", "repo": {"id": 42}}}):
            with self.subTest(change=change):
                self.api.prs = [valid | change]
                with self.assertRaises(PublishError):
                    self.guard()

    def test_review_approval_is_bound_to_both_target_sha_and_fingerprint(self):
        for change in ({"target_sha": "f" * 40}, {"fingerprint": "0" * 64}, {"status": "not-required"}):
            self.api.approval = {"target_sha": UPSTREAM_SHA, "fingerprint": self.api.manifest["fingerprint"], "status": "approved"} | change
            with self.assertRaises(PublishError):
                self.guard()

    def test_metadata_only_proposal_can_use_explicit_not_required_approval(self):
        self.api.manifest["requires_review"] = False
        self.api.refresh_evidence()
        self.api.approval["status"] = "not-required"
        self.assertEqual(self.guard()["version"], "0.2.23")

    def test_mutated_manifest_baseline_version_and_ui_mirror_each_block_release(self):
        for mutation in (lambda api: api.manifest.update(release_id=999), lambda api: api.baseline.update(reviewed_commit="f" * 40), lambda api: api.files.update({"helper/VERSION": "0.2.24"}), lambda api: api.files.update({"ui/upstream.css": "changed"})):
            self.api = FakeAPI()
            mutation(self.api)
            with self.assertRaises(PublishError):
                self.guard()

    def test_official_release_identity_stability_and_peeled_tag_are_required(self):
        for change in ({"draft": True}, {"prerelease": True}, {"id": 999}, {"published_at": None}, {"published_at": "2026-11-18T00:00:00Z"}, {"html_url": "https://unexpected.example.test"}):
            self.api = FakeAPI()
            self.api.upstream_release.update(change)
            with self.assertRaises(PublishError):
                self.guard()
        self.api = FakeAPI()
        self.api.upstream_tag["sha"] = "f" * 40
        with self.assertRaises(PublishError):
            self.guard()

    def test_annotated_upstream_tags_are_peeled_and_cycles_fail(self):
        annotation = "e" * 40
        self.api.upstream_tag = {"type": "tag", "sha": annotation}
        self.api.annotated_tags[annotation] = {"type": "commit", "sha": UPSTREAM_SHA}
        self.assertEqual(self.guard()["upstream_sha"], UPSTREAM_SHA)
        self.api.annotated_tags[annotation] = {"type": "tag", "sha": annotation}
        with self.assertRaises(PublishError):
            tag_commit(self.api, "Wei-Shaw/sub2api", "v0.2.23")

    def test_exact_official_tag_without_v_prefix_is_preserved(self):
        self.api.manifest.update(tag="0.2.23", release_url="https://github.com/Wei-Shaw/sub2api/releases/tag/0.2.23")
        self.api.refresh_evidence()
        self.api.upstream_release.update(tag_name="0.2.23", html_url=self.api.manifest["release_url"])
        self.assertEqual(self.guard()["tag"], "0.2.23")

    def test_existing_tag_is_immutable(self):
        self.api.own_tag = "f" * 40
        with self.assertRaises(PublishError):
            self.guard()

    def test_older_version_requires_matching_existing_tag_or_release_intent(self):
        self.api.own_releases = [self.api.release("v0.2.24", 31)]
        with self.assertRaises(PublishError):
            self.guard()
        self.api.own_tag = SHA
        self.assertTrue(self.guard()["older_recovery"])

    def test_existing_draft_or_wrong_tag_release_is_not_overwritten(self):
        self.api.own_tag = SHA
        self.api.own_release = self.api.release()
        self.api.own_release["draft"] = True
        with self.assertRaises(PublishError):
            self.guard()

    def test_contents_require_valid_base64_file_responses(self):
        for response in ({"type": "file", "encoding": "base64", "content": "%%%bad"}, {"type": "dir", "encoding": "base64", "content": ""}, {"type": "file", "encoding": "none", "content": ""}):
            self.api.contents_override = response
            with self.assertRaises(PublishError):
                read_file(self.api, REPO, SHA, "helper/VERSION")

    def test_finish_creates_tag_then_release_with_digest_and_platforms(self):
        metadata = self.guard()
        result = finish(self.api, metadata, DIGEST)
        posts = [call for call in self.api.calls if call[0] == "POST"]
        self.assertEqual([call[1] for call in posts], [f"/repos/{REPO}/git/refs", f"/repos/{REPO}/releases"])
        self.assertEqual(posts[0][2], {"ref": "refs/tags/v0.2.23", "sha": SHA})
        self.assertEqual(posts[1][2]["target_commitish"], SHA)
        self.assertEqual(posts[1][2]["make_latest"], "false")
        self.assertIn(DIGEST, result["body"])
        self.assertIn("linux/amd64", result["body"])
        self.assertIn("linux/arm64", result["body"])

    def test_finish_retry_preserves_existing_release_without_any_write(self):
        metadata = self.guard()
        finish(self.api, metadata, DIGEST)
        self.api.calls.clear()
        finish(self.api, metadata, DIGEST)
        self.assertFalse(any(call[0] == "POST" for call in self.api.calls))

    def test_partial_tag_and_ambiguous_release_creation_can_be_recovered(self):
        metadata = self.guard()
        self.api.own_tag = SHA
        self.api.fail_release_after_creation = True
        with self.assertRaises(APIError):
            finish(self.api, metadata, DIGEST)
        self.api.calls.clear()
        recovered = finish(self.api, metadata, DIGEST)
        self.assertEqual(recovered["tag_name"], "v0.2.23")
        self.assertFalse(any(call[0] == "POST" for call in self.api.calls))

    def test_older_recovery_never_moves_github_latest_backwards(self):
        self.api.own_tag = SHA
        self.api.own_releases = [self.api.release("v0.2.24", 31)]
        finish(self.api, self.guard(), DIGEST)
        request = next(call[2] for call in self.api.calls if call[0] == "POST" and call[1].endswith("/releases"))
        self.assertEqual(request["make_latest"], "false")

    def test_tampered_verified_metadata_and_invalid_digest_do_not_publish(self):
        metadata = self.guard()
        with self.assertRaises(PublishError):
            finish(self.api, metadata | {"image": "ghcr.io/attacker/image"}, DIGEST)
        with self.assertRaises(PublishError):
            finish(self.api, metadata, "invalid-digest")
        self.assertFalse(any(call[0] == "POST" for call in self.api.calls))

    def test_racing_tag_creation_never_overwrites_a_different_target(self):
        metadata = self.guard()
        original = self.api.request

        def request(method, path, data=None):
            if method == "POST" and path.endswith("/git/refs"):
                self.api.own_tag = "f" * 40
                raise APIError(422)
            return original(method, path, data)

        self.api.request = request
        with self.assertRaises(PublishError):
            finish(self.api, metadata, DIGEST)
        self.assertEqual(self.api.own_tag, "f" * 40)
        self.assertIsNone(self.api.own_release)

    def test_racing_release_creation_reuses_only_the_matching_release(self):
        metadata = self.guard()
        original = self.api.request

        def request(method, path, data=None):
            if method == "POST" and path.endswith("/releases"):
                self.api.own_release = self.api.release()
                self.api.own_release["body"] = data["body"]
                raise APIError(422)
            return original(method, path, data)

        self.api.request = request
        self.assertEqual(finish(self.api, metadata, DIGEST)["tag_name"], "v0.2.23")

    def test_reserve_records_immutable_intent_before_any_registry_write(self):
        metadata = self.guard()
        reserve(self.api, metadata)
        self.assertEqual(self.api.own_tag, SHA)
        self.assertIsNone(self.api.own_release)
        self.assertEqual([call[1] for call in self.api.calls if call[0] == "POST"], [f"/repos/{REPO}/git/refs"])
        self.api.calls.clear()
        reserve(self.api, metadata)
        self.assertFalse(any(call[0] == "POST" for call in self.api.calls))

    def test_reserved_version_can_recover_after_a_newer_release_is_published(self):
        metadata = self.guard()
        reserve(self.api, metadata)
        self.api.own_releases = [self.api.release("v0.2.24", 31)]
        finish(self.api, metadata, DIGEST)
        payload = next(call[2] for call in self.api.calls if call[0] == "POST" and call[1].endswith("/releases"))
        self.assertEqual(payload["make_latest"], "false")

    def test_existing_release_different_digest_is_not_silently_accepted(self):
        metadata = self.guard()
        finish(self.api, metadata, DIGEST)
        self.api.calls.clear()
        with self.assertRaises(PublishError):
            finish(self.api, metadata, "sha256:" + "e" * 64)
        self.assertFalse(any(call[0] == "POST" for call in self.api.calls))

    def test_matching_existing_release_preserves_additional_human_notes(self):
        metadata = self.guard()
        finish(self.api, metadata, DIGEST)
        self.api.own_release["body"] += "\nAdditional maintainer notes.\n"
        existing_body = self.api.own_release["body"]
        self.assertEqual(finish(self.api, metadata, DIGEST)["body"], existing_body)

    def test_guard_cli_writes_verified_metadata_and_safe_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.json"
            output = Path(directory) / "outputs"
            arguments = ["publish_release.py", "guard", "--repository", REPO, "--sha", SHA, "--default-branch", "main", "--metadata-path", str(metadata), "--outputs", str(output)]
            with patch("sys.argv", arguments), patch("scripts.github_api.GitHub", return_value=self.api):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(metadata.read_text())["sha"], SHA)
            self.assertIn("version=0.2.23\n", output.read_text())
            self.assertIn("image=ghcr.io/example/sub2api-helper\n", output.read_text())

    def test_release_preflight_rejects_fork_spoof_and_ordinary_push(self):
        self.assertTrue(is_release_candidate(self.api, REPO, SHA, "main"))
        self.api.prs[0]["head"]["repo"]["id"] = 999
        self.assertFalse(is_release_candidate(self.api, REPO, SHA, "main"))
        self.api.prs = []
        self.assertFalse(is_release_candidate(self.api, REPO, SHA, "main"))

    def test_skip_unrelated_cli_has_no_metadata_and_returns_success(self):
        self.api.prs = []
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.json"
            output = Path(directory) / "outputs"
            arguments = ["publish_release.py", "guard", "--skip-unrelated", "--repository", REPO, "--sha", SHA, "--default-branch", "main", "--metadata-path", str(metadata), "--outputs", str(output)]
            with patch("sys.argv", arguments), patch("scripts.github_api.GitHub", return_value=self.api):
                self.assertEqual(main(), 0)
            self.assertFalse(metadata.exists())
            self.assertEqual(output.read_text(), "eligible=false\n")
            self.assertTrue(all("/contents/" not in call[1] for call in self.api.calls))

    def test_skip_unrelated_flag_does_not_hide_a_broken_release_candidate(self):
        self.api.approval["status"] = "not-required"
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "metadata.json"
            output = Path(directory) / "outputs"
            arguments = ["publish_release.py", "guard", "--skip-unrelated", "--repository", REPO, "--sha", SHA, "--default-branch", "main", "--metadata-path", str(metadata), "--outputs", str(output)]
            with patch("sys.argv", arguments), patch("scripts.github_api.GitHub", return_value=self.api), redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 1)
            self.assertFalse(metadata.exists())

    def test_cli_does_not_echo_unexpected_exception_credentials(self):
        self.api.request = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("sensitive-workflow-token"))
        with tempfile.TemporaryDirectory() as directory:
            arguments = ["publish_release.py", "guard", "--repository", REPO, "--sha", SHA, "--default-branch", "main", "--metadata-path", str(Path(directory) / "metadata.json"), "--outputs", str(Path(directory) / "outputs")]
            error = io.StringIO()
            with patch("sys.argv", arguments), patch("scripts.github_api.GitHub", return_value=self.api), redirect_stderr(error):
                self.assertEqual(main(), 1)
            self.assertNotIn("sensitive-workflow-token", error.getvalue())
            self.assertFalse((Path(directory) / "metadata.json").exists())


class ImageInspectionTests(unittest.TestCase):
    def setUp(self):
        self.index = {"mediaType": "application/vnd.oci.image.index.v1+json", "annotations": {"org.opencontainers.image.revision": SHA, "org.opencontainers.image.version": "0.2.23"},
            "manifests": [{"platform": {"os": "linux", "architecture": arch}} for arch in ("amd64", "arm64")]}
        self.calls = []

    def runner(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, json.dumps(self.index) if "--raw" in args else DIGEST + "\n", "")

    def test_existing_image_requires_both_platforms_exact_revision_and_digest(self):
        result = inspect_image(IMAGE, "0.2.23", SHA, self.runner)
        self.assertEqual(result, {"exists": True, "digest": DIGEST})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0], ["docker", "buildx", "imagetools", "inspect", IMAGE, "--format", "{{.Manifest.Digest}}"])
        self.assertEqual(self.calls[1][0], ["docker", "buildx", "imagetools", "inspect", "ghcr.io/example/sub2api-helper@" + DIGEST, "--raw"])
        self.assertNotIn("shell", self.calls[0][1])

    def test_not_found_is_distinct_from_permission_network_and_registry_errors(self):
        for message in ("ERROR: " + IMAGE + ": not found", "MANIFEST_UNKNOWN: missing manifest", "manifest unknown"):
            runner = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", message)
            self.assertEqual(inspect_image(IMAGE, "0.2.23", SHA, runner), {"exists": False})
        for message in ("unauthorized: manifest unknown", "403 forbidden: not found", "connection timeout", "429 too many requests", "500 internal server error: manifest unknown", "unexpected failure"):
            runner = lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", message)
            with self.assertRaises(PublishError):
                inspect_image(IMAGE, "0.2.23", SHA, runner)

    def test_single_platform_wrong_version_or_wrong_revision_cannot_be_overwritten(self):
        original = deepcopy(self.index)
        for change in ({"manifests": self.index["manifests"][:1]}, {"annotations": {"org.opencontainers.image.revision": "f" * 40, "org.opencontainers.image.version": "0.2.23"}}, {"annotations": {"org.opencontainers.image.revision": SHA, "org.opencontainers.image.version": "0.2.24"}}, {"mediaType": "application/vnd.oci.image.manifest.v1+json"}):
            self.index = original | change
            with self.assertRaises(PublishError):
                inspect_image(IMAGE, "0.2.23", SHA, self.runner)

    def test_digest_query_failures_are_not_treated_as_missing_image(self):
        def runner(args, **kwargs):
            return self.runner(args, **kwargs) if "--raw" in args else subprocess.CompletedProcess(args, 1, "", "unauthorized")
        with self.assertRaises(PublishError):
            inspect_image(IMAGE, "0.2.23", SHA, runner)

    def test_resolved_digest_is_verified_even_if_the_tag_moves(self):
        newer_digest = "sha256:" + "e" * 64

        def runner(args, **kwargs):
            if "--format" in args:
                return subprocess.CompletedProcess(args, 0, DIGEST, "")
            self.assertIn("ghcr.io/example/sub2api-helper@" + DIGEST, args)
            self.assertNotIn(IMAGE, args)
            # A later tag lookup would return a different revision/digest. There
            # must be no second tag lookup for that unrelated mutable state.
            self.assertNotIn(newer_digest, args)
            return subprocess.CompletedProcess(args, 0, json.dumps(self.index), "")

        self.assertEqual(inspect_image(IMAGE, "0.2.23", SHA, runner)["digest"], DIGEST)

    def test_unsafe_image_reference_is_rejected_before_subprocess(self):
        for reference in (IMAGE + "\nignored", "ghcr.io/example/repo:latest", "https://ghcr.io/example/repo:0.2.23", IMAGE + "; echo unsafe"):
            with self.assertRaises(PublishError):
                inspect_image(reference, "0.2.23", SHA, self.runner)
        self.assertEqual(self.calls, [])

    def test_workflow_outputs_are_single_line_and_boolean_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outputs"
            outputs(path, {"exists": True, "digest": DIGEST})
            self.assertEqual(path.read_text(), "exists=true\ndigest=" + DIGEST + "\n")
            with self.assertRaises(PublishError):
                outputs(path, {"version": "0.2.23\nunsafe=value"})


if __name__ == "__main__":
    unittest.main()
