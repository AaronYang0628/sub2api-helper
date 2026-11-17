"""Offline release queue coverage, including real complete-list pagination."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("release_watcher", SCRIPTS / "watch_upstream.py")
watcher = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC.loader.exec_module(watcher)
finally:
    sys.path.pop(0)

REPOSITORY = "Example/sub2api-helper"
CURRENT = "0.2.22"
TOKEN = "offline-fixture-credential-must-not-appear"
DEFAULT_PUBLICATION = object()


def release(tag="v0.2.23", release_id=23, **changes):
    return {
        "id": release_id, "tag_name": tag, "draft": False, "prerelease": False,
        "published_at": "2026-11-17T00:00:00Z",
        "html_url": "https://github.com/Wei-Shaw/sub2api/releases/tag/" + tag,
        **changes,
    }


def pull(number=7, branch="automation/upstream-v0.2.23", repository=REPOSITORY,
         base="main", state="open", merged_at=None):
    return {"number": number, "state": state, "merged_at": merged_at,
            "head": {"ref": branch, "repo": {"full_name": repository}},
            "base": {"ref": base, "repo": {"full_name": REPOSITORY}}}


class FakeAPI(watcher.GitHub):
    """Use production pagination over offline responses, with no HTTP transport."""

    def __init__(self, releases=(), opened=(), history=(), published=DEFAULT_PUBLICATION):
        super().__init__(token=TOKEN)
        self.releases = list(releases)
        self.opened = list(opened)
        self.history = list(history)
        self.published = (release("v" + CURRENT, 22,
                                  html_url=f"https://github.com/{REPOSITORY}/releases/tag/v{CURRENT}")
                          if published is DEFAULT_PUBLICATION else published)
        self.calls = []

    def request(self, method, path, data=None):
        self.calls.append((method, path, deepcopy(data)))
        if method != "GET" or data is not None:
            raise AssertionError("Release discovery must only read GitHub data")
        endpoint = urlsplit(path)
        query = parse_qs(endpoint.query)
        if endpoint.path.startswith(f"repos/{REPOSITORY}/releases/tags/"):
            if isinstance(self.published, Exception):
                raise self.published
            if self.published is None:
                raise watcher.GitHubError(404)
            return deepcopy(self.published)
        if query.get("per_page") != ["100"]:
            raise AssertionError("Production pagination must request full pages")
        page = int(query["page"][0])
        if endpoint.path == "repos/Wei-Shaw/sub2api/releases":
            records = self.releases
        elif endpoint.path == f"repos/{REPOSITORY}/pulls":
            if query.get("state") == ["open"]:
                records = self.opened
            elif query.get("state") == ["all"] and "head" in query:
                records = self.history
            else:
                raise AssertionError("Unexpected PR query")
        else:
            raise AssertionError("Unexpected API endpoint")
        return deepcopy(records[(page - 1) * 100:page * 100])

    def endpoint_calls(self, suffix):
        return [urlsplit(path) for _, path, _ in self.calls if urlsplit(path).path.endswith(suffix)]


class WatcherDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "helper").mkdir()
        (self.root / "helper/VERSION").write_text(CURRENT + "\n")
        (self.root / "docs").mkdir()
        self.manifest = self.root / "docs/upstream-release.json"
        self.manifest.write_text(json.dumps({"version": CURRENT, "tag": "v" + CURRENT}) + "\n")

    def discover(self, api, default_branch="main"):
        return watcher.discover(api, REPOSITORY, default_branch, self.root)

    def test_oldest_newer_release_is_selected_after_complete_three_page_scan(self):
        # The latest 200 entries must not hide an older pending stable release.
        records = [release(f"v1.0.{patch}", 1000 + patch) for patch in range(200)]
        records.extend([release("v0.2.24", 24), release("v0.2.23", 23)])
        api = FakeAPI(releases=records)
        values, selected = self.discover(api)
        self.assertTrue(values["found"])
        self.assertEqual(values["version"], "0.2.23")
        self.assertEqual(values["branch"], "automation/upstream-v0.2.23")
        self.assertEqual(selected["id"], 23)
        self.assertEqual([parse_qs(call.query)["page"][0] for call in api.endpoint_calls("/releases")], ["1", "2", "3"])
        history = [parse_qs(call.query) for call in api.endpoint_calls("/pulls") if parse_qs(call.query)["state"] == ["all"]]
        self.assertEqual(history[0]["head"], ["Example:automation/upstream-v0.2.23"])

    def test_absent_manifest_bootstraps_current_official_version_before_later_releases(self):
        self.manifest.unlink()
        api = FakeAPI(releases=[release("v0.2.24", 24), release("v0.2.23", 23), release("v0.2.22", 22)])
        values, selected = self.discover(api)
        self.assertTrue(values["found"])
        self.assertEqual(selected["tag"], "v0.2.22")
        self.assertEqual(values["branch"], "automation/upstream-v0.2.22")
        # Once evidence exists, discovery continues with the next oldest release.
        self.manifest.write_text(json.dumps({"version": CURRENT, "tag": "v" + CURRENT}) + "\n")
        values, _ = self.discover(api)
        self.assertEqual(values["version"], "0.2.23")

    def test_current_helper_not_published_pauses_before_advancing_release_queue(self):
        for published in (None, {}, release("v" + CURRENT, 22, draft=True),
                          release("v" + CURRENT, 22, prerelease=True),
                          release("v0.2.21", 21), release("v" + CURRENT, 22, published_at=None),
                          release("v" + CURRENT, 22, published_at="not-a-timestamp"),
                          release("v" + CURRENT, 22, published_at="2026-11-17T00:00:00")):
            with self.subTest(published=published):
                api = FakeAPI(releases=[release(), release("v0.2.24", 24)], published=published)
                values, selected = self.discover(api)
                self.assertFalse(values["found"])
                self.assertIsNone(selected)
                self.assertIn("not published yet", values["reason"])
                self.assertIn("v" + CURRENT, values["reason"])
                history = [call for call in api.endpoint_calls("/pulls") if parse_qs(call.query)["state"] == ["all"]]
                self.assertEqual(history, [])

    def test_publication_lookup_permission_error_cannot_advance_queue(self):
        api = FakeAPI(releases=[release()], published=watcher.GitHubError(403))
        with self.assertRaises(watcher.GitHubError):
            self.discover(api)
        history = [call for call in api.endpoint_calls("/pulls") if parse_qs(call.query)["state"] == ["all"]]
        self.assertEqual(history, [])

    def test_successful_current_publication_unblocks_exact_next_release(self):
        api = FakeAPI(releases=[release("v0.2.24", 24), release()], published=None)
        values, selected = self.discover(api)
        self.assertFalse(values["found"])
        self.assertIsNone(selected)
        api.published = release("v" + CURRENT, 22, html_url=f"https://github.com/{REPOSITORY}/releases/tag/v{CURRENT}")
        values, selected = self.discover(api)
        self.assertTrue(values["found"])
        self.assertEqual(values["version"], "0.2.23")
        self.assertEqual(selected["tag"], "v0.2.23")
        self.assertTrue(any(urlsplit(path).path == f"repos/{REPOSITORY}/releases/tags/v{CURRENT}"
                            for _, path, _ in api.calls))

    def test_manifest_must_match_canonical_version_and_tag_before_publication_query(self):
        for manifest in ({}, {"version": "0.2.21", "tag": "v" + CURRENT},
                         {"version": CURRENT, "tag": "v0.2.23"}):
            with self.subTest(manifest=manifest):
                self.manifest.write_text(json.dumps(manifest))
                api = FakeAPI(releases=[release()])
                with self.assertRaisesRegex(ValueError, "canonical version"):
                    self.discover(api)
                self.assertFalse(any("/releases/tags/" in path for _, path, _ in api.calls))

    def test_missing_current_release_does_not_prevent_first_newer_release(self):
        self.manifest.unlink()
        values, selected = self.discover(FakeAPI(releases=[release("v0.2.24", 24), release("v0.2.23", 23)]))
        self.assertTrue(values["found"])
        self.assertEqual(selected["version"], "0.2.23")

    def test_same_repository_open_sync_pr_blocks_current_and_later_releases(self):
        for branch in ("automation/upstream-v0.2.22", "automation/upstream-v0.2.23", "automation/upstream-v0.2.24"):
            with self.subTest(branch=branch):
                api = FakeAPI(releases=[release(), release("v0.2.24", 24)], opened=[pull(branch=branch)])
                values, selected = self.discover(api)
                self.assertFalse(values["found"])
                self.assertIsNone(selected)
                self.assertIn("#7", values["reason"])
                self.assertIn("still open", values["reason"])
                self.assertEqual(api.endpoint_calls("/releases"), [])

    def test_open_sync_pr_on_second_page_still_blocks_the_queue(self):
        opened = [pull(number=n, branch=f"feature/{n}") for n in range(1, 101)]
        opened.append(pull(number=101))
        api = FakeAPI(releases=[release()], opened=opened)
        values, selected = self.discover(api)
        self.assertFalse(values["found"])
        self.assertIsNone(selected)
        self.assertIn("#101", values["reason"])
        self.assertEqual([parse_qs(call.query)["page"][0] for call in api.endpoint_calls("/pulls")], ["1", "2"])
        self.assertEqual(api.endpoint_calls("/releases"), [])

    def test_fork_spoofed_prefix_unrelated_base_and_deleted_head_do_not_block(self):
        deleted_head = pull(number=14)
        deleted_head["head"]["repo"] = None
        deleted_base = pull(number=15)
        deleted_base["base"] = None
        impostors = [pull(number=10, repository="attacker/sub2api-helper"),
                     pull(number=11, branch="automation/upstream-v0.2.23-extra"),
                     pull(number=12, branch="automation/upstream-v0.2.023"),
                     pull(number=13, base="develop"), deleted_head, deleted_base]
        api = FakeAPI(releases=[release()], opened=impostors, history=impostors)
        values, selected = self.discover(api)
        self.assertTrue(values["found"])
        self.assertEqual(selected["version"], "0.2.23")

    def test_same_repository_identity_is_case_insensitive(self):
        values, selected = self.discover(FakeAPI(releases=[release()], opened=[pull(repository=REPOSITORY.upper())]))
        self.assertFalse(values["found"])
        self.assertIsNone(selected)

    def test_closed_unmerged_same_branch_stops_without_duplicate_or_skipping(self):
        history = [pull(number=51, branch="automation/upstream-v0.2.24", state="closed"),
                   pull(number=52, repository="attacker/sub2api-helper", state="closed"),
                   pull(number=53, state="closed")]
        values, selected = self.discover(FakeAPI(releases=[release("v0.2.24", 24), release()], history=history))
        self.assertFalse(values["found"])
        self.assertIsNone(selected)
        self.assertIn("#53", values["reason"])
        self.assertIn("closed without merge", values["reason"])
        self.assertIn("reopen", values["reason"])

    def test_merged_history_on_stale_checkout_stops_instead_of_advancing_queue(self):
        api = FakeAPI(releases=[release("v0.2.24", 24), release()],
                      history=[pull(number=64, state="closed", merged_at="2026-11-17T01:00:00Z")])
        values, selected = self.discover(api)
        self.assertFalse(values["found"])
        self.assertIsNone(selected)
        self.assertIn("#64", values["reason"])
        self.assertIn("already merged", values["reason"])
        self.assertIn("refresh the default checkout", values["reason"])

    def test_invalid_draft_prerelease_and_unofficial_records_are_skipped_in_discovery(self):
        invalid = [None, "not-a-release", {}, release("v0.2.23", 1, draft=True),
                   release("v0.2.23", 2, prerelease=True), release("v0.2.23-rc.1", 3),
                   release("v00.2.23", 4), release("v0.2.23", 5, published_at=None),
                   release("v0.2.23", 6, published_at="2026-11-17T00:00:00"),
                   release("v0.2.23", 7, html_url="https://github.com/attacker/sub2api/releases/tag/v0.2.23"),
                   release("v0.2.23", True)]
        values, selected = self.discover(FakeAPI(releases=invalid + [release("v0.2.24", 24)]))
        self.assertTrue(values["found"])
        self.assertEqual(selected["version"], "0.2.24")

    def test_no_newer_stable_release_stops_without_history_query(self):
        api = FakeAPI(releases=[release("v0.2.22", 22), release("v0.2.21", 21)])
        values, selected = self.discover(api)
        self.assertFalse(values["found"])
        self.assertIsNone(selected)
        self.assertIn("No newer", values["reason"])
        self.assertEqual(len(api.endpoint_calls("/pulls")), 1)

    def test_default_branch_is_passed_as_a_query_value_not_path(self):
        api = FakeAPI(releases=[release()])
        values, _ = self.discover(api, default_branch="release/production")
        self.assertTrue(values["found"])
        for call in api.endpoint_calls("/pulls"):
            self.assertEqual(parse_qs(call.query)["base"], ["release/production"])

    def test_cli_outputs_manifest_and_summary_without_credentials(self):
        api = FakeAPI(releases=[release()])
        release_json, outputs, summary = (self.root / name for name in ("selected.json", "outputs", "summary"))
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["watch_upstream.py", "discover", "--repository", REPOSITORY, "--default-branch", "main",
                "--helper-root", str(self.root), "--release-json", str(release_json), "--outputs", str(outputs)]
        with patch.object(sys, "argv", argv), patch.object(watcher, "GitHub", return_value=api), \
                patch.dict("os.environ", {"GH_TOKEN": TOKEN, "GITHUB_STEP_SUMMARY": str(summary)}), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(watcher.main(), 0)
        self.assertEqual(json.loads(release_json.read_text())["id"], 23)
        values = outputs.read_text()
        self.assertIn("found=true\n", values)
        self.assertIn("version=0.2.23\n", values)
        self.assertNotIn("reason=", values)
        for emitted in (values, release_json.read_text(), summary.read_text(), stdout.getvalue(), stderr.getvalue()):
            self.assertNotIn(TOKEN, emitted)
            self.assertNotIn("Authorization", emitted)
        self.assertIn("official stable release", stdout.getvalue())

    def test_cli_api_failure_reports_safe_error_without_credentials(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["watch_upstream.py", "discover", "--repository", REPOSITORY, "--default-branch", "main",
                "--helper-root", str(self.root), "--release-json", str(self.root / "selected.json")]
        with patch.object(sys, "argv", argv), patch.object(watcher, "GitHub", side_effect=watcher.GitHubError(401)), \
                patch.dict("os.environ", {"GH_TOKEN": TOKEN}), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(watcher.main(), 1)
        self.assertIn("HTTP 401", stderr.getvalue())
        self.assertNotIn(TOKEN, stdout.getvalue() + stderr.getvalue())
        self.assertFalse((self.root / "selected.json").exists())


class PullRequestBodyTests(unittest.TestCase):
    def manifest(self, review=False):
        relevant = [{"status": "M", "path": "frontend/src/style.css"}] if review else []
        return {"tag": "v0.2.23", "version": "0.2.23", "release_url": release()["html_url"],
                "target_sha": "a" * 40, "requires_review": review,
                "evidence": {"all_changes": [{"status": "M", "path": "docs/guide.md"}] + relevant,
                             "relevant_changes": relevant}}

    def test_no_impact_still_requires_explicit_pr_and_same_version_release(self):
        body = watcher.pull_request_body(self.manifest())
        self.assertIn("同步官方 sub2api **v0.2.23**", body)
        self.assertIn("Helper 版本为 **0.2.23**", body)
        self.assertIn("仍按要求提交 PR 并在合并后发布同版本", body)
        self.assertIn("GitHub Release", body)
        self.assertIn("不自动合并", body)
        self.assertNotIn("sync_upstream_release.py approve", body)

    def test_relevant_changes_body_requires_fresh_explicit_compatibility_approval(self):
        body = watcher.pull_request_body(self.manifest(review=True))
        self.assertIn("frontend/src/style.css", body)
        self.assertIn("sync_upstream_release.py approve", body)
        self.assertIn("--source /path/to/upstream-exact-tag", body)
        self.assertIn("批准绑定本次提交和证据指纹", body)
        self.assertIn("不能沿用旧版本的批准", body)
        self.assertIn("a" * 40, body)
        self.assertIn("不自动合并", body)


if __name__ == "__main__":
    unittest.main()
