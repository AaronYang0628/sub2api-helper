"""The running helper compares official Releases, independently of main."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx

from helper import __version__
from helper.app import BASELINE, create_app
from helper.config import Config
from helper.store import Store
from test_app import FakeMailer, FakeUpstream

HTTPClient = httpx.AsyncClient


class RuntimeReleaseTests(unittest.TestCase):
    def query(self, tag, sha, prerelease=False):
        upstream = FakeUpstream()

        async def read(method, path, **kwargs):
            if path.endswith("/settings"):
                return {"custom_menu_items": [], "session_binding_enabled": False}
            if path.endswith("/version"):
                return {"version": __version__}
            return {"items": []}

        upstream.request = AsyncMock(side_effect=read)
        requests = []

        def handle(request):
            requests.append(request)
            if request.url.path.endswith("/releases/latest"):
                return httpx.Response(200, json={"tag_name": tag, "draft": False, "prerelease": prerelease})
            return httpx.Response(200, json={"sha": sha})

        def client(**kwargs):
            return HTTPClient(transport=httpx.MockTransport(handle), **kwargs)

        config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="private-fixture-key",
                        admin_token="private-fixture-helper-token", db_path=":memory:")
        app = create_app(config, upstream=upstream, store=Store(":memory:"), mailer=FakeMailer(), background=False)
        with patch("helper.app.httpx.AsyncClient", client), TestClient(app) as browser:
            response = browser.post("/api/compatibility/check", headers={"Authorization": "Bearer " + config.admin_token})
        self.assertEqual(response.status_code, 200)
        for request in requests:
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("x-api-key", request.headers)
        return response.json(), requests

    def test_same_official_version_and_tag_commit_is_current(self):
        report, requests = self.query("v" + __version__, BASELINE)
        self.assertFalse(report["update_available"])
        self.assertEqual(report["latest_version"], __version__)
        self.assertEqual([r.url.path for r in requests], ["/repos/Wei-Shaw/sub2api/releases/latest", "/repos/Wei-Shaw/sub2api/commits/v" + __version__])

    def test_new_official_version_is_an_update(self):
        report, _ = self.query("v99.0.0", "f" * 40)
        self.assertTrue(report["update_available"])
        self.assertEqual(report["latest_tag"], "v99.0.0")

    def test_prerelease_is_not_claimed_as_verified_current(self):
        report, requests = self.query("v99.0.0", "f" * 40, prerelease=True)
        self.assertIsNone(report["update_available"])
        self.assertEqual(len(requests), 1)
        self.assertTrue(any(check["name"] == "上游更新查询" and not check["ok"] for check in report["checks"]))
