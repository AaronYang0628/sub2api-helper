"""Offline release synchronization: real Git trees, no GitHub requests."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_upstream_release.py"
SPEC = importlib.util.spec_from_file_location("release_sync", SCRIPT)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def release(version="v1.1.0", release_id=11, **changes):
    return {"id": release_id, "tag_name": version, "draft": False, "prerelease": False,
            "published_at": "2026-11-17T00:00:00Z", "html_url": sync.REPOSITORY + "/releases/tag/" + version, **changes}


class ReleaseSelectionTests(unittest.TestCase):
    def test_code_and_schemas_inside_docs_still_require_review(self):
        self.assertEqual(sync.change_scope("docs/guide.md"), "documentation")
        for path in ("docs/generate.py", "docs/openapi.yaml", "docs/helper.go", "docs/tool.sh"):
            with self.subTest(path=path):
                self.assertEqual(sync.change_scope(path), "unclassified-review-required")

    def test_oldest_stable_newer_release_is_selected_without_skipping(self):
        records = [release("v1.4.0", 14), release("v1.2.0", 12), release("v1.1.0", 11),
                   release("v1.0.9", 10, draft=True), release("v1.0.8", 9, prerelease=True),
                   release("v1.0.7-rc1", 8), release("v1.0.6", 7, published_at=None)]
        selected = sync.select_release(records, "1.0.0")
        self.assertEqual(selected["tag"], "v1.1.0")
        self.assertEqual(selected["id"], 11)
        self.assertFalse(selected["prerelease"])
        self.assertIsNone(sync.select_release(records, "1.4.0"))

    def test_strict_versions_and_official_release_urls(self):
        for tag in ("v01.2.3", "v1.2", "v1.2.3;echo", "refs/heads/main", "v1.2.3+build", "1.2.3-rc.1"):
            self.assertIsNone(sync.normalize_release(release(tag)))
        self.assertEqual(sync.normalize_release(release("1.2.3"))["version"], "1.2.3")
        self.assertIsNone(sync.normalize_release(release(html_url="https://attacker.test/release")))
        self.assertIsNone(sync.normalize_release(release(release_id=True)))
        for timestamp in (None, "not-a-time", "2026-11-17T00:00:00"):
            self.assertIsNone(sync.normalize_release(release(published_at=timestamp)))
        published = "2026-11-17T09:41:53Z"
        self.assertEqual(sync.normalize_release(release(published_at=published))["published_at"], published)


class ReleaseSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "upstream"
        self.root = self.directory / "helper"
        self.source.mkdir()
        self.root.mkdir()
        self.git("init", "--quiet")
        self.source_write("backend/cmd/server/VERSION", "1.0.0\n")
        self.source_write("backend/internal/server/routes/admin.go", "// reviewed admin routes\n")
        self.source_write("frontend/src/utils/embedded-url.ts", "// reviewed iframe contract\n")
        self.source_write("frontend/src/style.css", ".card { border-radius: 16px; }\n")
        self.source_write("frontend/tailwind.config.js", "export default {primary: '#14b8a6'}\n")
        self.source_write("docs/guide.md", "documentation\n")
        self.commit("reviewed source")
        self.previous = {
            "repository": sync.REPOSITORY, "reviewed_commit": self.git("rev-parse", "HEAD"),
            "reviewed_version": "1.0.0", "reviewed_at": "2026-11-01",
            "files": {name: hashlib.sha256((self.source / name).read_bytes()).hexdigest() for name in (
                "backend/cmd/server/VERSION", "backend/internal/server/routes/admin.go", "frontend/src/utils/embedded-url.ts",
                "frontend/src/style.css", "frontend/tailwind.config.js")},
        }
        self.helper_write("helper/VERSION", "1.0.0\n")
        self.helper_write("helper/__init__.py", "from pathlib import Path\n__version__ = Path(__file__).with_name('VERSION').read_text().strip()\n")
        self.helper_write("compose.yaml", "services:\n  helper:\n    image: sub2api-helper:1.0.0\n")
        self.helper_write("ui/package.json", json.dumps({"name": "test-ui", "version": "1.0.0"}))
        self.helper_write("ui/upstream.css", (self.source / "frontend/src/style.css").read_text())
        self.helper_write("ui/upstream.tailwind.config.js", (self.source / "frontend/tailwind.config.js").read_text())
        self.helper_write("ui/package-lock.json", json.dumps({"name": "test-ui", "version": "1.0.0", "packages": {"": {"version": "1.0.0"}}}))
        self.helper_write("README.md", "# Helper\n\n## Docker 部署\n\nRun sub2api-helper:1.0.0\n\n## Historical\nOld sub2api-helper:0.1.0\n")
        sync.write_json(self.root / sync.BASELINE_PATH, self.previous)
        self.release_path = self.directory / "release.json"
        self.metadata = release()
        sync.write_json(self.release_path, self.metadata)

    def helper_write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def source_write(self, name, content):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def git(self, *arguments):
        result = subprocess.run(["git", "-C", str(self.source), *arguments], check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("-c", "user.name=Release Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", message)

    def target(self, runtime=False):
        self.source_write("backend/cmd/server/VERSION", "1.1.0\n")
        self.source_write("docs/guide.md", "updated docs\n")
        if runtime:
            self.source_write("backend/internal/server/routes/admin.go", "// changed authentication route\n")
        self.commit("new stable release")
        self.git("tag", "v1.1.0")

    def run_command(self, command, **kwargs):
        return sync.run(command, self.release_path, self.source, self.root, **kwargs)

    def test_metadata_only_still_prepares_version_sync_proposal_and_valid_gate(self):
        self.target()
        outputs = self.directory / "outputs"
        result = self.run_command("prepare", outputs=outputs)
        self.assertFalse(result["requires_review"])
        self.assertTrue(result["publishable"])
        self.assertEqual(result["branch"], "automation/upstream-v1.1.0")
        self.assertEqual((self.root / "helper/VERSION").read_text(), "1.1.0\n")
        self.assertTrue((self.root / sync.MANIFEST_PATH).exists())
        self.assertEqual(sync.read_json(self.root / sync.MANIFEST_PATH)["published_at"], self.metadata["published_at"])
        self.assertEqual(sync.read_json(self.root / sync.APPROVAL_PATH)["status"], "not-required")
        self.assertEqual(sync.read_json(self.root / sync.BASELINE_PATH)["reviewed_commit"], result["target_sha"])
        self.assertEqual(self.run_command("check")["fingerprint"], result["fingerprint"])
        self.assertIn("requires_review=false\n", outputs.read_text())
        self.assertIn("Historical\nOld sub2api-helper:0.1.0", (self.root / "README.md").read_text())

    def test_relevant_changes_block_until_explicit_approval_then_reprepare_preserves_it(self):
        self.target(runtime=True)
        first = self.run_command("prepare")
        self.assertTrue(first["requires_review"])
        self.assertFalse(first["publishable"])
        self.assertEqual(sync.read_json(self.root / sync.BASELINE_PATH), self.previous)
        self.assertFalse((self.root / sync.APPROVAL_PATH).exists())
        with self.assertRaisesRegex(ValueError, "explicit compatibility approval"):
            self.run_command("check")
        self.run_command("approve")
        approval_before = sync.read_json(self.root / sync.APPROVAL_PATH)
        self.assertEqual(approval_before["status"], "approved")
        self.assertTrue(self.run_command("check")["publishable"])
        self.assertEqual(self.run_command("prepare")["fingerprint"], first["fingerprint"])
        self.assertEqual(sync.read_json(self.root / sync.APPROVAL_PATH), approval_before)

    def test_added_deleted_and_renamed_unpinned_runtime_files_require_review(self):
        self.source_write("backend/cmd/server/VERSION", "1.1.0\n")
        self.git("mv", "frontend/src/utils/embedded-url.ts", "frontend/src/utils/iframe-url.ts")
        self.source_write("backend/internal/new_dependency.go", "// new indirect runtime behavior\n")
        self.commit("runtime files renamed and added")
        self.git("tag", "v1.1.0")
        self.run_command("prepare")
        manifest = sync.read_json(self.root / sync.MANIFEST_PATH)
        self.assertTrue(manifest["requires_review"])
        changes = {item["path"]: item for item in manifest["evidence"]["all_changes"]}
        self.assertEqual(changes["frontend/src/utils/embedded-url.ts"]["status"], "D")
        self.assertIsNone(changes["frontend/src/utils/embedded-url.ts"]["sha256"])
        self.assertEqual(changes["frontend/src/utils/iframe-url.ts"]["status"], "A")
        self.assertEqual(changes["backend/internal/new_dependency.go"]["scope"], "runtime-or-interface")
        self.run_command("approve")
        hashes = sync.read_json(self.root / sync.BASELINE_PATH)["files"]
        self.assertNotIn("frontend/src/utils/embedded-url.ts", hashes)
        self.assertIn("frontend/src/utils/iframe-url.ts", hashes)
        self.assertIn("backend/internal/new_dependency.go", hashes)

    def test_new_unclassified_workflow_change_requires_review(self):
        self.source_write(".github/workflows/release.yaml", "name: changed runtime packaging\n")
        self.target()
        self.assertTrue(self.run_command("prepare")["requires_review"])

    def test_approval_and_check_require_exact_vendored_ui_source(self):
        self.source_write("frontend/src/style.css", ".card { border-radius: 20px; }\n")
        self.target()
        self.run_command("prepare")
        with self.assertRaisesRegex(ValueError, "Vendored UI source"):
            self.run_command("approve")
        self.assertFalse((self.root / sync.APPROVAL_PATH).exists())
        self.helper_write("ui/upstream.css", (self.source / "frontend/src/style.css").read_text())
        self.run_command("approve")
        self.assertTrue(self.run_command("check")["publishable"])
        self.helper_write("ui/upstream.tailwind.config.js", "stale stylesheet config\n")
        with self.assertRaisesRegex(ValueError, "Vendored UI source"):
            self.run_command("check")

    def test_equal_bootstrap_tag_is_authority_even_if_embedded_version_lags(self):
        # The real v0.2.22 upstream tag contains VERSION=0.2.21. Packaging may
        # inject its version, so a valid stable release tag is authoritative.
        self.git("tag", "v1.0.0")
        self.source_write("backend/cmd/server/VERSION", "0.9.9\n")
        self.commit("embedded version trails release tag")
        self.git("tag", "--force", "v1.0.0")
        sync.write_json(self.release_path, release("v1.0.0", 10))
        result = self.run_command("prepare")
        self.assertFalse(result["requires_review"])
        self.assertEqual(result["version"], "1.0.0")
        manifest = sync.read_json(self.root / sync.MANIFEST_PATH)
        self.assertEqual(manifest["evidence"]["embedded_version"], "0.9.9")
        self.assertFalse(manifest["evidence"]["embedded_version_matches_release"])
        self.assertTrue(self.run_command("check")["publishable"])

    def test_stale_approval_is_rejected_and_prepare_removes_it(self):
        self.target(runtime=True)
        proposal = self.run_command("prepare")
        sync.write_json(self.root / sync.APPROVAL_PATH, {"status": "approved", "target_sha": proposal["target_sha"], "fingerprint": "0" * 64})
        with self.assertRaisesRegex(ValueError, "explicit compatibility approval"):
            self.run_command("check")
        self.run_command("prepare")
        self.assertFalse((self.root / sync.APPROVAL_PATH).exists())

    def test_publication_time_is_bound_to_evidence_fingerprint(self):
        self.target(runtime=True)
        proposal = self.run_command("prepare")
        metadata = deepcopy(self.metadata)
        metadata["published_at"] = "2026-11-17T09:41:53Z"
        sync.write_json(self.release_path, metadata)
        with self.assertRaisesRegex(ValueError, "freshly computed"):
            self.run_command("check")
        revised = self.run_command("prepare")
        self.assertNotEqual(revised["fingerprint"], proposal["fingerprint"])
        self.assertEqual(sync.read_json(self.root / sync.MANIFEST_PATH)["published_at"], metadata["published_at"])

    def test_forged_saved_evidence_and_modified_versions_do_not_pass(self):
        self.target()
        self.run_command("prepare")
        manifest = sync.read_json(self.root / sync.MANIFEST_PATH)
        manifest["requires_review"] = True
        sync.write_json(self.root / sync.MANIFEST_PATH, manifest)
        with self.assertRaisesRegex(ValueError, "freshly computed"):
            self.run_command("check")
        self.run_command("prepare")
        self.helper_write("ui/package.json", json.dumps({"version": "0.0.0"}))
        with self.assertRaisesRegex(ValueError, "Version references disagree"):
            self.run_command("check")

    def test_dirty_source_wrong_tag_and_missing_reviewed_commit_fail(self):
        self.target()
        self.source_write("untracked-file", "not part of released tag\n")
        with self.assertRaisesRegex(ValueError, "local changes"):
            self.run_command("prepare")
        (self.source / "untracked-file").unlink()
        self.source_write("docs/guide.md", "after tag\n")
        self.commit("later main commit")
        with self.assertRaisesRegex(ValueError, "exact official release tag"):
            self.run_command("prepare")
        self.git("checkout", "--detach", "v1.1.0")
        forged = deepcopy(self.previous)
        forged["reviewed_commit"] = "0" * 40
        sync.write_json(self.root / sync.BASELINE_PATH, forged)
        with self.assertRaisesRegex(ValueError, "fetch both commits"):
            self.run_command("prepare")

    def test_reviewed_baseline_hashes_must_match_its_real_git_tree(self):
        self.target()
        forged = deepcopy(self.previous)
        forged["files"]["backend/internal/server/routes/admin.go"] = "0" * 64
        sync.write_json(self.root / sync.BASELINE_PATH, forged)
        with self.assertRaisesRegex(ValueError, "baseline hashes do not match"):
            self.run_command("prepare")

    def test_list_metadata_is_repeatable_and_single_lower_release_cannot_downgrade(self):
        self.target()
        sync.write_json(self.release_path, [release("v1.2.0", 12), self.metadata])
        first = self.run_command("prepare")
        self.assertEqual(first["version"], "1.1.0")
        self.assertEqual(self.run_command("prepare"), first)
        sync.write_json(self.release_path, release("v0.9.0", 9))
        with self.assertRaisesRegex(ValueError, "downgrade"):
            self.run_command("prepare")


if __name__ == "__main__":
    unittest.main()
