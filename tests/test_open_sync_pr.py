"""Real PR creation contracts with an offline GitHub Git Data API fake."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from github_api import GitHubError

SPEC = importlib.util.spec_from_file_location("open_sync_pr", SCRIPTS / "open_sync_pr.py")
pr_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pr_script)

BASE, OLD, COMMON, TREE, NEW_TREE, NEW, RACE = (character * 40 for character in "abc1234")
REPOSITORY = "owner/helper"
BRANCH = "automation/upstream-v0.2.22"
TITLE = "chore: sync sub2api v0.2.22"


def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


class API:
    def __init__(self, root, head=None):
        self.root, self.head, self.calls = root, head, []
        self.prs = []
        self.ancestor = False
        self.race = False
        self.branch_reads = 0
        self.fail_create_pr = False
        self.files = [{"filename": "docs/upstream-release.json", "status": "added"}]
        self.commits = [{"sha": OLD, "author": {"type": "User", "login": "owner"},
                         "committer": {"type": "Bot", "login": "github-actions[bot]"},
                         "commit": {"message": TITLE}}]
        self.original = {name: (root / name).read_bytes() for name in pr_script.ALLOWED
                         if name != "docs/upstream-release.json" and (root / name).exists()}

    def pull(self, number=1, state="open"):
        return {"number": number, "state": state,
                "head": {"ref": BRANCH, "repo": {"id": 17}, "sha": self.head},
                "base": {"ref": "main", "repo": {"id": 17}}}

    def paginate(self, path):
        self.calls.append(("PAGINATE", path, None))
        return self.prs

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        prefix = "/repos/owner/helper"
        if method == "GET" and path == prefix:
            return {"id": 17, "full_name": REPOSITORY, "default_branch": "main"}
        if method == "GET" and path == prefix + "/git/ref/heads/main":
            return {"object": {"type": "commit", "sha": BASE}}
        if method == "GET" and path == prefix + "/git/ref/heads/automation%2Fupstream-v0.2.22":
            self.branch_reads += 1
            if self.race and self.branch_reads == 2:
                self.head = RACE
            if self.head is None:
                raise GitHubError(404)
            return {"object": {"type": "commit", "sha": self.head}}
        if method == "GET" and path == prefix + "/git/commits/" + BASE:
            return {"sha": BASE, "tree": {"sha": TREE}}
        if method == "GET" and path == prefix + "/git/trees/" + TREE + "?recursive=1":
            return {"truncated": False, "tree": [{"path": name, "type": "blob", "mode": "100644", "sha": git_blob(raw)} for name, raw in self.original.items()]}
        if method == "GET" and path == prefix + f"/compare/{BASE}...{OLD}":
            return {"status": "behind" if self.ancestor else "diverged", "merge_base_commit": {"sha": OLD if self.ancestor else COMMON}}
        if method == "GET" and path == prefix + f"/compare/{COMMON}...{OLD}?per_page=100":
            return {"status": "ahead", "merge_base_commit": {"sha": COMMON}, "files": self.files, "commits": self.commits, "total_commits": len(self.commits)}
        if method == "POST" and path == prefix + "/git/blobs":
            return {"sha": git_blob(base64.b64decode(data["content"]))}
        if method == "POST" and path == prefix + "/git/trees":
            return {"sha": NEW_TREE}
        if method == "POST" and path == prefix + "/git/commits":
            return {"sha": NEW}
        if method == "POST" and path == prefix + "/git/refs":
            self.head = data["sha"]
            return {"object": {"sha": self.head}}
        if method == "PATCH" and path == prefix + "/git/refs/heads/automation%2Fupstream-v0.2.22":
            assert data["force"] is False
            self.head = data["sha"]
            return {"object": {"sha": self.head}}
        if method == "POST" and path == prefix + "/pulls":
            if self.fail_create_pr:
                raise GitHubError(403)
            return self.pull()
        if method == "PATCH" and path == prefix + "/pulls/8":
            return self.pull(8)
        raise AssertionError((method, path, data))


class OpenSyncPRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in pr_script.ALLOWED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("release metadata\n")
        (self.root / "helper/VERSION").write_text("0.2.22\n")
        (self.root / "docs/upstream-release.json").write_text(json.dumps({"version": "0.2.22"}))

    def run_open(self, api, **changes):
        values = dict(repository=REPOSITORY, base="main", branch=BRANCH, title=TITLE, body="Actual upstream release evidence", root=self.root, draft=False, checkout_sha=BASE)
        return pr_script.open_sync_pr(api, **(values | changes))

    def mutations(self, api):
        return [call for call in api.calls if call[0] in {"POST", "PATCH"}]

    def test_new_branch_creates_real_pr_with_only_allowlisted_blobs_and_no_git_credentials(self):
        api = API(self.root)
        result = self.run_open(api)
        self.assertEqual(result["pull_request_number"], 1)
        self.assertEqual(result["head_sha"], NEW)
        tree = next(data for method, path, data in api.calls if method == "POST" and path.endswith("/git/trees"))
        self.assertEqual(tree["base_tree"], TREE)
        self.assertEqual({item["path"] for item in tree["tree"]}, {"docs/upstream-release.json"})
        commit = next(data for method, path, data in api.calls if method == "POST" and path.endswith("/git/commits"))
        self.assertEqual(commit["parents"], [BASE])
        self.assertNotIn("author", commit)
        self.assertNotIn("committer", commit)
        pull = next(data for method, path, data in api.calls if method == "POST" and path.endswith("/pulls"))
        self.assertEqual(pull["head"], BRANCH)
        self.assertFalse(pull["draft"])

    def test_diverged_previous_bot_branch_is_preserved_as_second_parent_and_fast_forwarded(self):
        api = API(self.root, OLD)
        self.run_open(api)
        commit = next(data for method, path, data in api.calls if method == "POST" and path.endswith("/git/commits"))
        self.assertEqual(commit["parents"], [BASE, OLD])
        patch = next(data for method, path, data in api.calls if method == "PATCH" and "/git/refs/" in path)
        self.assertEqual(patch, {"sha": NEW, "force": False})

    def test_old_branch_already_in_default_only_needs_current_default_parent(self):
        api = API(self.root, OLD)
        api.ancestor = True
        self.run_open(api)
        commit = next(data for method, path, data in api.calls if method == "POST" and path.endswith("/git/commits"))
        self.assertEqual(commit["parents"], [BASE])

    def test_unmerged_user_code_or_human_commit_is_never_overwritten(self):
        for kind in ("code", "human", "renamed-code"):
            with self.subTest(kind=kind):
                api = API(self.root, OLD)
                if kind == "code":
                    api.files.append({"filename": "helper/app.py"})
                elif kind == "human":
                    api.commits[0]["committer"] = {"type": "User", "login": "owner"}
                else:
                    api.files[0]["previous_filename"] = "helper/app.py"
                with self.assertRaisesRegex(ValueError, "preserving user work"):
                    self.run_open(api)
                self.assertEqual(self.mutations(api), [])
                self.assertEqual(api.head, OLD)

    def test_existing_open_pr_is_updated_without_duplicate_creation(self):
        api = API(self.root, OLD)
        api.prs = [api.pull(8)]
        result = self.run_open(api)
        self.assertEqual(result["pull_request_number"], 8)
        self.assertFalse(any(method == "POST" and path.endswith("/pulls") for method, path, _ in api.calls))

    def test_closed_pr_requires_reopen_instead_of_duplicate_or_ref_mutation(self):
        api = API(self.root, OLD)
        api.prs = [api.pull(8, state="closed")]
        with self.assertRaisesRegex(ValueError, "already closed"):
            self.run_open(api)
        self.assertEqual(self.mutations(api), [])

    def test_ref_race_fails_without_force_or_ref_overwrite(self):
        api = API(self.root, OLD)
        api.race = True
        with self.assertRaisesRegex(ValueError, "branch changed"):
            self.run_open(api)
        self.assertFalse(any("/git/refs" in path for method, path, _ in api.calls if method in {"POST", "PATCH"}))
        self.assertEqual(api.head, RACE)

    def test_default_moved_after_prepare_fails_before_any_write(self):
        api = API(self.root)
        with self.assertRaisesRegex(ValueError, "Default branch advanced"):
            self.run_open(api, checkout_sha=OLD)
        self.assertEqual(self.mutations(api), [])

    def test_missing_optional_approval_is_deleted_not_reused(self):
        api = API(self.root)
        (self.root / "docs/upstream-release-approval.json").unlink()
        self.run_open(api)
        tree = next(data for method, path, data in api.calls if method == "POST" and path.endswith("/git/trees"))
        deleted = [item for item in tree["tree"] if item["sha"] is None]
        self.assertEqual([item["path"] for item in deleted], ["docs/upstream-release-approval.json"])

    def test_api_pr_denial_remains_a_real_error_with_recoverable_branch(self):
        api = API(self.root)
        api.fail_create_pr = True
        with self.assertRaises(GitHubError) as caught:
            self.run_open(api)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(api.head, NEW)

    def test_version_branch_mismatch_and_symlink_fail_before_api(self):
        api = API(self.root)
        with self.assertRaises(ValueError):
            self.run_open(api, branch="automation/upstream-v1.2.3")
        path = self.root / "README.md"
        path.unlink()
        path.symlink_to(self.root / "helper/VERSION")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.run_open(api)
        self.assertEqual(api.calls, [])

    def test_all_repository_actions_use_full_commit_pins_and_allowed_publishers(self):
        workflows = Path(__file__).resolve().parents[1] / ".github/workflows"
        uses = []
        for path in workflows.glob("*.yaml"):
            uses.extend(re.findall(r"uses:\s+([^\s#]+)", path.read_text()))
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^(?:actions|docker)/[a-z0-9-]+@[0-9a-f]{40}$")
        self.assertFalse(any("peter-evans" in action for action in uses))


if __name__ == "__main__":
    unittest.main()
