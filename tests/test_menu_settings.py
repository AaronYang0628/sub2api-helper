"""Menu origins, exact ownership, SVGs and safe embed preflight without services."""

from copy import deepcopy
from dataclasses import replace
import tempfile
import unittest
from urllib.parse import urlsplit
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from helper.app import MENU_ICONS, create_app
from helper.config import Config
from helper.store import Store
from helper.upstream import UpstreamError
try:
    from tests.test_app import FakeMailer, FakeUpstream
except ModuleNotFoundError:
    from test_app import FakeMailer, FakeUpstream


PREFIX = "/_helper/api/admin"


class MenuUpstream(FakeUpstream):
    fail_settings = False
    fail_readback = False

    async def settings(self):
        if self.fail_settings or (self.fail_readback and self.writes):
            raise UpstreamError("Settings unavailable", 503)
        return await super().settings()


class MenuSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="upstream-key-secret",
                             admin_token="helper-admin-secret", public_url="https://helper.example.test",
                             db_path=self.temp.name + "/helper.db")
        self.store = Store(self.config.db_path)
        self.upstream = MenuUpstream()
        self.client = self.make_client(self.config, self.store, self.upstream)
        self.auth = {"Authorization": "Bearer " + self.config.admin_token}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    @staticmethod
    def make_client(config, store, upstream, base_url="https://helper.example.test"):
        client = TestClient(create_app(config, upstream=upstream, store=store, mailer=FakeMailer(), background=False),
                            base_url=base_url, raise_server_exceptions=False)
        client.__enter__()
        return client

    def settings(self):
        response = self.client.get(PREFIX + "/menu-settings", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_authenticated_preview_has_absolute_urls_and_real_inline_icons(self):
        self.assertEqual(self.client.get(PREFIX + "/menu-settings").status_code, 401)
        view = self.settings()
        self.assertTrue(view["ready"])
        self.assertEqual(view["origin_source"], "environment")
        self.assertEqual(view["sub2api_public_origin"], "https://sub2api.example.test")
        self.assertEqual({item["key"] for item in view["menus"]}, {"benchmark", "pricing", "ranking"})
        for item in view["menus"]:
            self.assertEqual(item["url"], "https://helper.example.test/_helper/features/" + item["key"])
            self.assertEqual(urlsplit(item["url"]).scheme, "https")
            self.assertEqual(item["id"], "sub2api-helper-" + item["key"])
            svg = ElementTree.fromstring(item["icon_svg"])
            self.assertEqual(svg.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertTrue(list(svg))
            self.assertNotIn("<script", item["icon_svg"])
            self.assertNotIn("href=", item["icon_svg"])
        self.assertEqual(self.upstream.writes, [])

    def test_saving_origin_is_local_only_and_normalizes_root(self):
        response = self.client.put(PREFIX + "/menu-settings", headers=self.auth,
                                   json={"public_origin": " HTTPS://NEW.Example.Test:443/ "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["public_origin"], "https://new.example.test")
        self.assertEqual(response.json()["origin_source"], "saved")
        self.assertEqual(self.store.get_setting("public_url"), "https://new.example.test")
        self.assertEqual(self.upstream.writes, [])
        self.assertEqual(self.client.get(PREFIX + "/status", headers=self.auth).json()["public_url"], "https://new.example.test")

    def test_ui_override_survives_restart_with_original_environment_default(self):
        response = self.client.put(PREFIX + "/menu-settings", headers=self.auth, json={"public_origin": "https://saved.example.test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.client.__exit__(None, None, None)
        self.store = Store(self.config.db_path)
        self.upstream = MenuUpstream()
        self.client = self.make_client(self.config, self.store, self.upstream)
        self.assertEqual(self.settings()["public_origin"], "https://saved.example.test")
        self.assertEqual(self.settings()["origin_source"], "saved")

    def test_public_sub2api_origin_is_effective_default_without_helper_override(self):
        for changes, expected in (({"sub2api_public_url": "https://PUBLIC.Example.Test:443/"}, "https://public.example.test"),
                                  ({}, "https://sub2api.example.test"),
                                  ({"sub2api_url": "http://sub2api:8080"}, ""),
                                  ({"sub2api_url": "http://sub2api:8080", "sub2api_public_url": "http://192.168.1.5:8080"}, "http://192.168.1.5:8080")):
            with self.subTest(changes=changes):
                cfg = replace(self.config, db_path=":memory:", public_url="", **changes)
                client = self.make_client(cfg, Store(":memory:"), MenuUpstream())
                try:
                    response = client.get(PREFIX + "/menu-settings", headers=self.auth)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["public_origin"], expected)
                    self.assertEqual(response.json()["origin_source"], "sub2api" if expected else "unset")
                finally:
                    client.__exit__(None, None, None)

    def test_wrong_loopback_and_internal_targets_rejected_before_any_write(self):
        for value in ("http://localhost:8080", "https://localhost", "https://127.0.0.1", "https://10.0.0.1", "http://sub2api:8080", "https://helper.internal", "https://[::1]"):
            with self.subTest(value=value):
                response = self.client.put(PREFIX + "/menu-settings", headers=self.auth | {"Origin": value, "X-Forwarded-Host": "sub2api.example.test", "X-Forwarded-Proto": "https"}, json={"public_origin": value})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.upstream.writes, [])
        self.assertIsNone(self.store.get_setting("public_url"))

    def test_matching_explicit_local_site_remains_supported(self):
        cfg = replace(self.config, db_path=":memory:", public_url="http://localhost:8080", sub2api_public_url="http://localhost:8080", sub2api_url="http://sub2api:8080")
        upstream = MenuUpstream()
        client = self.make_client(cfg, Store(":memory:"), upstream, "http://localhost:8080")
        try:
            response = client.put(PREFIX + "/modules", headers=self.auth, json={"benchmark": True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(upstream.settings_data["custom_menu_items"][-1]["url"], "http://localhost:8080/_helper/features/benchmark")
            self.assertIn("frame-ancestors 'self' http://localhost:8080", client.get("/_helper/features/benchmark").headers["Content-Security-Policy"])
        finally:
            client.__exit__(None, None, None)

    def test_cross_origin_local_fixture_requires_known_parent_and_actual_helper_origin(self):
        cfg = replace(self.config, db_path=":memory:", public_url="http://127.0.0.1:8090", sub2api_public_url="http://localhost:8080")
        client = self.make_client(cfg, Store(":memory:"), MenuUpstream(), "http://127.0.0.1:8090")
        try:
            view = client.get(PREFIX + "/menu-settings", headers=self.auth)
            self.assertTrue(view.json()["ready"], view.text)
            response = client.put(PREFIX + "/modules", headers=self.auth, json={"pricing": True})
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            client.__exit__(None, None, None)

    def test_https_parent_rejects_plain_http_helper(self):
        response = self.client.put(PREFIX + "/menu-settings", headers=self.auth, json={"public_origin": "http://helper.example.test"})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("HTTPS", response.json()["detail"])
        self.assertIsNone(self.store.get_setting("public_url"))

    def test_non_origins_invalid_authorities_and_secret_urls_are_not_saved_or_echoed(self):
        for value in ("/_helper", "//helper.example.test", "https://helper.example.test/path", "https://helper.example.test?secret=credential", "https://secret-credential@example.test", "https://example.test:0", "https://example.test:99999", "https://127.1", "https://2130706433", "https:\\example.test", "https://example.test/#fragment"):
            with self.subTest(value=value):
                response = self.client.put(PREFIX + "/menu-settings", headers=self.auth, json={"public_origin": value})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn("secret-credential", response.text)
                self.assertNotIn("?secret=credential", response.text)
        self.assertIsNone(self.store.get_setting("public_url"))

    def test_exact_owned_menu_migration_preserves_other_rows_and_repairs_icons(self):
        unrelated = {"id": "sub2api-helper-external", "label": "Do not own", "url": "https://docs.example.test", "icon_svg": "<svg/>", "visibility": "admin", "sort_order": 5, "extra": {"keep": True}}
        old = [{"id": "sub2api-helper-" + key, "label": "Old", "url": "https://old.example.test/features/" + key, "icon_svg": "", "visibility": "user", "sort_order": 10} for key in ("benchmark", "pricing")]
        self.upstream.settings_data["custom_menu_items"] = [deepcopy(unrelated), *old]
        self.store.set_setting("modules", {"benchmark": True, "pricing": True, "ranking": False})
        self.assertFalse(self.settings()["in_sync"])
        response = self.client.post(PREFIX + "/menu-settings/sync", headers=self.auth, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sync_status"], "verified")
        self.assertTrue(response.json()["changed"])
        self.assertTrue(response.json()["in_sync"])
        menus = self.upstream.settings_data["custom_menu_items"]
        self.assertEqual(menus[0], unrelated)
        self.assertEqual(len(menus), 3)
        for item in menus[1:]:
            key = item["id"].removeprefix("sub2api-helper-")
            self.assertEqual(item["url"], "https://helper.example.test/_helper/features/" + key)
            self.assertEqual(item["icon_svg"], MENU_ICONS[key])
        writes = len(self.upstream.writes)
        second = self.client.post(PREFIX + "/menu-settings/sync", headers=self.auth)
        self.assertFalse(second.json()["changed"])
        self.assertEqual(len(self.upstream.writes), writes, "verified no-op must not write upstream again")

    def test_wrong_existing_origin_blocks_sync_but_can_be_explicitly_repaired(self):
        self.store.set_setting("public_url", "http://localhost:8080")
        self.client.__exit__(None, None, None)
        self.store = Store(self.config.db_path); self.upstream = MenuUpstream()
        self.client = self.make_client(self.config, self.store, self.upstream)
        self.assertFalse(self.settings()["ready"])
        response = self.client.put(PREFIX + "/modules", headers=self.auth, json={"ranking": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.upstream.writes, [])
        repaired = self.client.put(PREFIX + "/menu-settings", headers=self.auth, json={"public_origin": "https://helper.example.test"})
        self.assertTrue(repaired.json()["ready"], repaired.text)
        self.assertEqual(self.client.put(PREFIX + "/modules", headers=self.auth, json={"ranking": True}).status_code, 200)

    def test_binding_enabled_or_unknown_blocks_enable_and_does_not_validate_user_jwt(self):
        for value in (True, None, "false", 0):
            with self.subTest(value=value):
                self.upstream.settings_data["session_binding_enabled"] = value
                view = self.settings()
                self.assertFalse(view["ready"])
                response = self.client.put(PREFIX + "/modules", headers=self.auth, json={"benchmark": True})
                self.assertEqual(response.status_code, 409, response.text)
                login = self.client.post("/_helper/api/portal/session", json={"token": "user-jwt"})
                self.assertEqual(login.status_code, 409, login.text)
        self.assertEqual(self.upstream.profile_tokens, [])
        self.assertEqual(self.upstream.writes, [])
        self.assertEqual(self.store.get_setting("modules", {}), {})

    def test_cached_portal_session_rejects_unknown_binding_without_profile_call(self):
        login = self.client.post("/_helper/api/portal/session", json={"token": "user-jwt"})
        self.assertEqual(login.status_code, 200, login.text)
        self.upstream.profile_tokens.clear()
        self.upstream.settings_data.pop("session_binding_enabled")
        response = self.client.get("/_helper/api/portal/modules", headers={"X-Portal-Session": login.json()["session"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.upstream.profile_tokens, [])

    def test_query_origin_hints_never_widen_helper_frame_ancestors(self):
        self.store.set_setting("modules", {"benchmark": True})
        response = self.client.get("/_helper/features/benchmark?token=user-jwt&user_id=99&src_host=https://untrusted.example.test")
        self.assertEqual(response.status_code, 200, response.text)
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'self' https://sub2api.example.test", policy)
        self.assertNotIn("untrusted.example.test", policy)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_turning_every_module_off_still_works_with_binding_enabled(self):
        self.store.set_setting("modules", {"benchmark": True})
        self.upstream.settings_data["custom_menu_items"].append({"id": "sub2api-helper-benchmark", "url": "https://old.example.test/features/benchmark"})
        self.upstream.settings_data["session_binding_enabled"] = True
        response = self.client.put(PREFIX + "/modules", headers=self.auth, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(any(self.store.get_setting("modules").values()))
        self.assertFalse(any(item["id"] == "sub2api-helper-benchmark" for item in self.upstream.settings_data["custom_menu_items"]))

    def test_unverified_write_and_readback_failures_never_change_local_modules(self):
        self.upstream.menu_write_applies = False
        failed = self.client.put(PREFIX + "/modules", headers=self.auth, json={"pricing": True})
        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertEqual(self.store.get_setting("modules", {}), {})
        self.upstream.writes.clear()
        self.upstream.menu_write_applies = True; self.upstream.fail_readback = True
        failed = self.client.put(PREFIX + "/modules", headers=self.auth, json={"ranking": True})
        self.assertEqual(failed.status_code, 502, failed.text)
        self.assertIn("尚未确认", failed.json()["detail"])
        self.assertEqual(self.store.get_setting("modules", {}), {})

    def test_failed_origin_save_does_not_partially_persist(self):
        self.upstream.fail_settings = True
        response = self.client.put(PREFIX + "/menu-settings", headers=self.auth, json={"public_origin": "https://new.example.test"})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIsNone(self.store.get_setting("public_url"))
        self.assertEqual(self.client.get(PREFIX + "/connection").json()["public_url"], self.config.public_url)

    def test_menu_limit_failure_preserves_all_existing_rows(self):
        self.upstream.settings_data["custom_menu_items"] = [{"id": str(index), "url": "https://example.test", "label": "Keep"} for index in range(20)]
        before = deepcopy(self.upstream.settings_data["custom_menu_items"])
        response = self.client.put(PREFIX + "/modules", headers=self.auth, json={"pricing": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.upstream.settings_data["custom_menu_items"], before)
        self.assertEqual(self.upstream.writes, [])


if __name__ == "__main__":
    unittest.main()
