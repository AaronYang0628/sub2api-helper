"""Group gallery configuration, generation and ordinary-user access contracts."""

from copy import deepcopy
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from helper.app import create_app
from helper.config import Config
from helper.gallery_generation import FAILURE_MESSAGES
from helper.store import Store
from helper.upstream import UpstreamError
try:
    from tests.test_app import FakeMailer, FakeUpstream
except ModuleNotFoundError:
    from test_app import FakeMailer, FakeUpstream


MODEL_KEY = "ordinary-model-key-server-only"
CONFIG_PROMPT = "Private configured prompt: draw a blue clock as a self-contained SVG."
COMPARISON_PROMPT = "Draw the same animated clock scene for a fair cross-model comparison."


class GalleryUpstream(FakeUpstream):
    def __init__(self):
        super().__init__()
        self.keys = [
            {"id": 31, "name": "Group three key", "group_id": 3, "group_name": "Group three"},
            {"id": 41, "name": "Group four key", "group_id": 4, "group_name": "Group four"},
        ]
        self.key_lookups = []
        self.key_options_reads = 0
        self.key_material = {31: MODEL_KEY, 41: MODEL_KEY}
        self.key_unavailable = False
        self.group_catalog_error = None
        self.key_catalog_error = None
        self.directory_groups = [
            {"id": 3, "name": "Anthropic directory", "platform": "anthropic", "status": "active", "private_internal": "not public"},
            {"id": 4, "name": "Kimi directory", "platform": "openai", "status": "active"},
            {"id": 5, "name": "GLM without model key", "platform": "openai", "status": "active"},
        ]
        self.allowed = {"user-jwt": [3], "other-user-jwt": [4]}
        self.available_calls = []

    async def benchmark_keys(self):
        self.key_options_reads += 1
        if self.key_catalog_error:
            raise self.key_catalog_error
        return deepcopy(self.keys)

    async def list_groups(self):
        if self.group_catalog_error:
            raise self.group_catalog_error
        return deepcopy(self.directory_groups)

    async def benchmark_key(self, key_id, group_id):
        self.key_lookups.append((key_id, group_id))
        if self.key_unavailable or not any(item["id"] == key_id and item["group_id"] == group_id for item in self.keys):
            raise UpstreamError("Selected model key is unavailable for this group", 403)
        return self.key_material[key_id]

    async def available_groups(self, token):
        self.available_calls.append(token)
        result = self.allowed.get(token, [])
        if isinstance(result, Exception):
            raise result
        return list(result)

    async def available_group_rows(self, token):
        return [{"id": gid, "name": f"Group {gid}", "platform": "openai", "status": "active"}
                for gid in await self.available_groups(token)]


class GalleryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="upstream-administrator-secret",
                             admin_token="local-gallery-administrator-token", native_ui=False,
                             public_url="https://helper.example.test", db_path=self.temp.name + "/helper.db")
        self.store = Store(self.config.db_path)
        self.upstream = GalleryUpstream()
        self.generated = []

        async def generate(config, selection):
            self.generated.append({"model": selection.model, "api_format": selection.api_format,
                                   "prompt": selection.prompt, "api_key": selection.api_key,
                                   "reasoning_effort": selection.reasoning_effort})
            return {"kind": "html", "content": '<svg xmlns="http://www.w3.org/2000/svg"><text>Fixture artwork</text></svg>',
                    "mime": "text/html", "latency_ms": 12}

        self.generator = AsyncMock(side_effect=generate)
        self.generator_patch = patch("helper.app.generate_artwork", new=self.generator)
        self.generator_patch.start()
        self.app = create_app(self.config, store=self.store, upstream=self.upstream, mailer=FakeMailer(), background=False)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer " + self.config.admin_token}
        self.store.set_setting("modules", {"benchmark": True, "pricing": False, "ranking": False})

    def tearDown(self):
        try:
            self.client.__exit__(None, None, None)
        finally:
            self.generator_patch.stop()
            self.temp.cleanup()

    @staticmethod
    def job(**changes):
        return {"name": "Group three gallery", "model": "fixture-model", "key_id": 31,
                "api_format": "chat", "prompt": CONFIG_PROMPT, "interval_minutes": 30, "enabled": False} | changes

    def configure(self, group_id=3, **changes):
        value = self.job(**changes)
        if group_id == 4 and "key_id" not in changes:
            value["key_id"] = 41
        response = self.client.put(f"/_helper/api/admin/gallery/{group_id}", headers=self.auth, json=value)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(MODEL_KEY, response.text)
        return response.json()

    def portal(self, jwt="user-jwt"):
        response = self.client.post("/_helper/api/portal/session", json={"token": jwt})
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-Portal-Session": response.json()["session"]}

    def admin_view(self, group_id=3):
        response = self.client.get(f"/_helper/api/admin/gallery/{group_id}", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def generate_one(self, group_id=3):
        previous = len(self.admin_view(group_id)["works"])
        response = self.client.post(f"/_helper/api/admin/gallery/{group_id}/run", headers=self.auth)
        self.assertEqual(response.status_code, 202, response.text)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            view = self.admin_view(group_id)
            if len(view["works"]) > previous:
                latest = view["works"][0]
                if latest["status"] == "success":
                    return latest
                if latest["status"] == "failed":
                    self.fail("The mocked gallery generation failed: " + str(latest.get("error")))
            time.sleep(.01)
        self.fail("The mocked background generation did not persist a work within three seconds")

    @staticmethod
    def comparison(**changes):
        return {"group_ids": [3, 4], "prompt": COMPARISON_PROMPT, "interval_minutes": 30, "enabled": False} | changes

    def configure_comparison(self, **changes):
        self.upstream.key_material = {31: "anthropic-key-server-only", 41: "kimi-key-server-only"}
        self.configure(3, name="Anthropic gallery", model="fixture-claude", prompt="Private Anthropic standalone prompt", api_format="chat")
        self.configure(4, name="Kimi gallery", model="fixture-kimi", prompt="Private Kimi standalone prompt", api_format="responses")
        response = self.client.put("/_helper/api/admin/gallery/comparison", headers=self.auth, json=self.comparison(**changes))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def generate_comparison(self):
        response = self.client.post("/_helper/api/admin/gallery/comparison/run", headers=self.auth)
        self.assertEqual(response.status_code, 202, response.text)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = self.client.get("/_helper/api/admin/gallery/comparison", headers=self.auth)
            self.assertEqual(response.status_code, 200, response.text)
            latest = response.json().get("round")
            if latest and latest["status"] not in {"queued", "running"}:
                self.assertEqual(latest["status"], "success", response.text)
                return latest
            time.sleep(.01)
        self.fail("The mocked comparison did not finish within three seconds")

    def test_gallery_configuration_and_manual_generation_require_management_auth(self):
        self.configure()
        ordinary = self.portal()
        requests = [("GET", "/_helper/api/admin/gallery", None), ("GET", "/_helper/api/admin/gallery/3", None),
                    ("PUT", "/_helper/api/admin/gallery/3", self.job()), ("POST", "/_helper/api/admin/gallery/3/run", None),
                    ("POST", "/_helper/api/admin/gallery/3/pause", None), ("GET", "/_helper/api/admin/gallery/3/works/1", None)]
        for method, path, value in requests:
            for headers in ({}, ordinary, {"Authorization": "Bearer " + self.config.sub2api_admin_key}):
                with self.subTest(method=method, path=path, headers=list(headers)):
                    response = self.client.request(method, path, headers=headers, json=value)
                    self.assertEqual(response.status_code, 401, response.text)
        self.generator.assert_not_awaited()

    def test_configuration_checks_owned_key_group_and_defaults_to_paused(self):
        value = self.job()
        del value["enabled"]
        response = self.client.put("/_helper/api/admin/gallery/3", headers=self.auth, json=value)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.upstream.key_lookups, [(31, 3)])
        view = self.admin_view()
        self.assertEqual(view["group"]["group_id"], 3)
        self.assertFalse(view["group"]["enabled"])
        self.assertEqual(view["group"]["prompt"], CONFIG_PROMPT)
        self.assertEqual(view["group"]["key_id"], 31)
        response = self.client.get("/_helper/api/admin/gallery", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["groups"][0]["group_id"], 3)
        self.assertNotIn(MODEL_KEY, response.text)
        self.generator.assert_not_awaited()

    def test_unusable_or_cross_group_model_key_cannot_replace_existing_config(self):
        self.configure()
        before = self.admin_view()["group"]
        response = self.client.put("/_helper/api/admin/gallery/3", headers=self.auth, json=self.job(key_id=41, prompt="wrong group"))
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertEqual(self.admin_view()["group"], before)
        self.upstream.key_unavailable = True
        response = self.client.put("/_helper/api/admin/gallery/3", headers=self.auth, json=self.job(enabled=True))
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertEqual(self.admin_view()["group"], before)
        self.generator.assert_not_awaited()

    def test_invalid_gallery_config_is_rejected_before_key_lookup(self):
        invalid = [{"name": ""}, {"model": ""}, {"key_id": 0}, {"prompt": ""},
                   {"api_format": "unsupported"}, {"interval_minutes": 4}, {"interval_minutes": 1441}]
        for change in invalid:
            with self.subTest(change=change):
                response = self.client.put("/_helper/api/admin/gallery/3", headers=self.auth, json=self.job(**change))
                self.assertEqual(response.status_code, 422, response.text)
        for group_id in (0, -1):
            response = self.client.put(f"/_helper/api/admin/gallery/{group_id}", headers=self.auth, json=self.job())
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.upstream.key_lookups, [])
        self.assertEqual(self.client.get("/_helper/api/admin/gallery", headers=self.auth).json()["groups"], [])

    def test_group_timeout_is_saved_cleared_and_preserved_for_old_clients_and_pause(self):
        self.configure(timeout_seconds=600)
        self.assertEqual(self.admin_view()["group"]["timeout_seconds"], 600)
        self.assertEqual(self.client.get("/_helper/api/admin/gallery", headers=self.auth).json()["default_timeout_seconds"], 240)
        self.configure()  # Older client omitted the added field.
        self.assertEqual(self.admin_view()["group"]["timeout_seconds"], 600)
        response = self.client.post("/_helper/api/admin/gallery/3/pause", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group"]["timeout_seconds"], 600)
        self.generate_one()
        self.assertEqual(self.generator.call_args.args[1].timeout_seconds, 600)
        self.configure(timeout_seconds=None)
        self.assertIsNone(self.admin_view()["group"]["timeout_seconds"])
        for invalid in (29, 901, 600.5, True, "600", "bad-secret-value"):
            with self.subTest(invalid=invalid):
                response = self.client.put("/_helper/api/admin/gallery/3", headers=self.auth, json=self.job(timeout_seconds=invalid))
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("bad-secret-value", response.text)
        with self.store._db:
            self.store._db.execute("UPDATE gallery_jobs SET config=json_remove(config, '$.timeout_seconds') WHERE group_id=3")
        response = self.client.post("/_helper/api/admin/gallery/3/pause", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["group"]["timeout_seconds"])

    def test_interval_boundaries_are_supported(self):
        for interval in (5, 1440):
            with self.subTest(interval=interval):
                self.configure(interval_minutes=interval)
                self.assertEqual(self.admin_view()["group"]["interval_minutes"], interval)

    def test_manual_generation_uses_existing_group_key_and_persists_exact_executed_prompt(self):
        self.configure(api_format="responses")
        summary = self.generate_one()
        self.assertEqual(self.generator.await_count, 1)
        self.assertEqual(self.generated[0], {"model": "fixture-model", "api_format": "responses",
                                            "prompt": CONFIG_PROMPT, "api_key": MODEL_KEY, "reasoning_effort": "default"})
        self.assertGreaterEqual(self.upstream.key_lookups.count((31, 3)), 2, "The worker must resolve the key again at execution time")
        response = self.client.get(f"/_helper/api/admin/gallery/3/works/{summary['id']}", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Fixture artwork", response.text)
        self.assertEqual(response.json()["prompt"], self.generated[0]["prompt"])
        for secret in (MODEL_KEY, self.config.sub2api_admin_key):
            self.assertNotIn(secret, response.text)

    def test_key_revoked_before_execution_has_safe_diagnostic_and_no_model_call(self):
        self.configure()
        self.upstream.key_unavailable = True
        response = self.client.post("/_helper/api/admin/gallery/3/run", headers=self.auth)
        self.assertEqual(response.status_code, 202, response.text)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            latest = self.admin_view()["works"][0]
            if latest["status"] == "failed":
                break
            time.sleep(.01)
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["error"], FAILURE_MESSAGES["key_lookup"])
        self.assertNotIn("Selected model key", str(latest))
        self.assertNotIn(MODEL_KEY, str(latest))
        self.generator.assert_not_awaited()

    def test_pause_stops_future_generation_even_if_the_selected_key_was_revoked(self):
        self.configure(enabled=True)
        self.upstream.key_unavailable = True
        previous = list(self.upstream.key_lookups)
        response = self.client.post("/_helper/api/admin/gallery/3/pause", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.admin_view()["group"]["enabled"])
        self.assertEqual(self.upstream.key_lookups, previous, "Pausing must not depend on a still-usable billing key")
        self.generator.assert_not_awaited()

    def test_portal_requires_session_and_enabled_benchmark_module(self):
        self.configure()
        self.assertEqual(self.client.get("/_helper/api/portal/gallery").status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/portal/gallery/3").status_code, 401)
        ordinary = self.portal()
        self.store.set_setting("modules", {"benchmark": False, "pricing": False, "ranking": False})
        for path in ("/_helper/api/portal/gallery", "/_helper/api/portal/gallery/3", "/_helper/api/portal/gallery/3/works/1"):
            with self.subTest(path=path):
                response = self.client.get(path, headers=ordinary)
                self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.upstream.available_calls, [])

    def test_ordinary_list_and_group_config_are_projected_to_current_accessible_groups(self):
        self.configure()
        self.configure(4, name="Inaccessible gallery", prompt="Hidden group configuration")
        ordinary = self.portal()
        response = self.client.get("/_helper/api/portal/gallery", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        groups = response.json()["groups"]
        self.assertEqual([group["group_id"] for group in groups], [3])
        self.assertNotIn("key_id", groups[0])
        self.assertNotIn("prompt", groups[0])
        self.assertNotIn(CONFIG_PROMPT, response.text)
        self.assertNotIn("Hidden group configuration", response.text)
        self.assertNotIn(MODEL_KEY, response.text)
        response = self.client.get("/_helper/api/portal/gallery/3", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("key_id", response.json()["group"])
        self.assertNotIn("prompt", response.json()["group"])
        self.assertTrue(self.upstream.available_calls)
        self.assertTrue(all(token == "user-jwt" for token in self.upstream.available_calls))

    def test_portal_work_detail_contains_executed_prompt_without_exposing_configuration_key(self):
        self.configure()
        summary = self.generate_one()
        ordinary = self.portal()
        listing = self.client.get("/_helper/api/portal/gallery/3", headers=ordinary)
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertNotIn(CONFIG_PROMPT, listing.text)
        response = self.client.get(f"/_helper/api/portal/gallery/3/works/{summary['id']}", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["prompt"], self.generated[0]["prompt"])
        self.assertIn("Fixture artwork", response.text)
        self.assertNotIn("key_id", response.json())
        self.assertNotIn(MODEL_KEY, response.text)
        self.assertNotIn(self.config.sub2api_admin_key, response.text)
        self.assertIn("application/json", response.headers["content-type"])
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertNotIn("'unsafe-inline'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_existing_work_retains_its_executed_prompt_after_configuration_changes(self):
        self.configure()
        summary = self.generate_one()
        executed = self.generated[0]["prompt"]
        self.configure(prompt="New private configuration for future work only")
        response = self.client.get(f"/_helper/api/portal/gallery/3/works/{summary['id']}", headers=self.portal())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["prompt"], executed)
        self.assertNotIn("New private configuration for future work only", response.text)

    def test_cross_group_and_work_id_access_cannot_reveal_another_groups_work(self):
        self.configure()
        self.configure(4)
        summary = self.generate_one(4)
        ordinary = self.portal()
        for path in ("/_helper/api/portal/gallery/4", f"/_helper/api/portal/gallery/4/works/{summary['id']}",
                     f"/_helper/api/portal/gallery/3/works/{summary['id']}"):
            with self.subTest(path=path):
                response = self.client.get(path, headers=ordinary)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertNotIn("Fixture artwork", response.text)

    def test_group_access_is_rechecked_after_a_permission_change(self):
        self.configure()
        summary = self.generate_one()
        ordinary = self.portal()
        self.assertEqual(self.client.get("/_helper/api/portal/gallery/3", headers=ordinary).status_code, 200)
        self.upstream.allowed["user-jwt"] = []
        response = self.client.get("/_helper/api/portal/gallery", headers=ordinary)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["groups"], [])
        for path in ("/_helper/api/portal/gallery/3", f"/_helper/api/portal/gallery/3/works/{summary['id']}"):
            self.assertEqual(self.client.get(path, headers=ordinary).status_code, 404)

    def test_group_lookup_failure_does_not_fall_back_to_the_admins_all_group_visibility(self):
        self.configure()
        ordinary = self.portal()
        self.upstream.allowed["user-jwt"] = UpstreamError("Group access lookup failed", 503)
        for path in ("/_helper/api/portal/gallery", "/_helper/api/portal/gallery/3"):
            with self.subTest(path=path):
                response = self.client.get(path, headers=ordinary)
                self.assertGreaterEqual(response.status_code, 400)
                self.assertNotIn("Group three gallery", response.text)
                self.assertNotIn(CONFIG_PROMPT, response.text)

    def test_administrator_portal_can_view_all_configured_groups(self):
        self.configure()
        self.configure(4)
        administrator = self.portal("admin-jwt")
        response = self.client.get("/_helper/api/portal/gallery", headers=administrator)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({group["group_id"] for group in response.json()["groups"]}, {3, 4})
        self.assertEqual(self.upstream.available_calls, [])

    def test_comparison_configuration_and_execution_require_management_auth(self):
        ordinary = self.portal()
        for method, path, value in (("GET", "/_helper/api/admin/gallery/comparison", None),
                                    ("PUT", "/_helper/api/admin/gallery/comparison", self.comparison()),
                                    ("POST", "/_helper/api/admin/gallery/comparison/run", None),
                                    ("POST", "/_helper/api/admin/gallery/comparison/pause", None)):
            for headers in ({}, ordinary, {"Authorization": "Bearer " + self.config.sub2api_admin_key}):
                with self.subTest(method=method, path=path, headers=list(headers)):
                    response = self.client.request(method, path, json=value, headers=headers)
                    self.assertEqual(response.status_code, 401, response.text)
        self.generator.assert_not_awaited()

    def test_comparison_requires_distinct_configured_groups_and_valid_shared_settings(self):
        invalid = [{"group_ids": [3]}, {"group_ids": [3, 3]}, {"group_ids": [0, 4]},
                   {"group_ids": [-1, 4]}, {"group_ids": [3, 2**63]}, {"group_ids": list(range(1, 34))},
                   {"prompt": ""}, {"interval_minutes": 4}, {"interval_minutes": 1441}]
        for changes in invalid:
            with self.subTest(changes=changes):
                response = self.client.put("/_helper/api/admin/gallery/comparison", headers=self.auth, json=self.comparison(**changes))
                self.assertEqual(response.status_code, 422, response.text)
        self.configure(3)
        response = self.client.put("/_helper/api/admin/gallery/comparison", headers=self.auth, json=self.comparison())
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.upstream.key_options_reads, 0, "Unconfigured participants must be rejected before key discovery")
        self.generator.assert_not_awaited()

    def test_comparison_validates_the_exact_stored_key_for_every_group_before_updating(self):
        self.configure_comparison()
        self.assertEqual(self.upstream.key_options_reads, 1)
        before = self.client.get("/_helper/api/admin/gallery/comparison", headers=self.auth).json()["config"]
        invalid_live_options = [
            [{"id": 32, "group_id": 3}, {"id": 41, "group_id": 4}],
            [{"id": 31, "group_id": 4}, {"id": 41, "group_id": 4}],
            [{"id": 31, "group_id": 3}],
        ]
        for options in invalid_live_options:
            with self.subTest(options=options):
                self.upstream.keys = options
                response = self.client.put("/_helper/api/admin/gallery/comparison", headers=self.auth,
                                           json=self.comparison(prompt="Should not replace the valid shared prompt", enabled=True))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.client.get("/_helper/api/admin/gallery/comparison", headers=self.auth).json()["config"], before)
        self.generator.assert_not_awaited()

    def test_comparison_runs_distinct_model_keys_with_one_identical_persisted_prompt(self):
        self.configure_comparison(enabled=True)
        latest = self.generate_comparison()
        self.assertEqual(latest["prompt"], COMPARISON_PROMPT)
        self.assertEqual({item["group_id"] for item in latest["items"]}, {3, 4})
        self.assertEqual(self.generator.await_count, 2)
        self.assertEqual({item["prompt"] for item in self.generated}, {COMPARISON_PROMPT})
        self.assertEqual({item["model"] for item in self.generated}, {"fixture-claude", "fixture-kimi"})
        self.assertEqual({item["api_key"] for item in self.generated}, {"anthropic-key-server-only", "kimi-key-server-only"})
        self.assertEqual({item["api_format"] for item in self.generated}, {"chat", "responses"})
        for item in latest["items"]:
            self.assertEqual(item["status"], "success")
            self.assertEqual(item["work"]["round_id"], latest["id"])
            self.assertEqual(item["work"]["group_id"], item["group_id"])
            response = self.client.get(f"/_helper/api/admin/gallery/{item['group_id']}/works/{item['work']['id']}", headers=self.auth)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["round_id"], latest["id"])
            self.assertEqual(response.json()["prompt"], COMPARISON_PROMPT)
            self.assertNotIn("anthropic-key-server-only", response.text)
            self.assertNotIn("kimi-key-server-only", response.text)

    def test_individual_generation_cannot_use_a_different_prompt_for_a_shared_participant(self):
        self.configure_comparison(enabled=True)
        for group_id in (3, 4):
            response = self.client.post(f"/_helper/api/admin/gallery/{group_id}/run", headers=self.auth)
            self.assertEqual(response.status_code, 409, response.text)
        self.generator.assert_not_awaited()

    def test_shared_comparison_pause_does_not_need_currently_usable_model_keys(self):
        self.configure_comparison(enabled=True)
        options_before = self.upstream.key_options_reads
        lookups_before = list(self.upstream.key_lookups)
        self.upstream.key_unavailable = True
        self.upstream.keys = []
        response = self.client.post("/_helper/api/admin/gallery/comparison/pause", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get("/_helper/api/admin/gallery/comparison", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["config"]["enabled"])
        self.assertEqual(self.upstream.key_options_reads, options_before)
        self.assertEqual(self.upstream.key_lookups, lookups_before)
        self.generator.assert_not_awaited()

    def test_portal_comparison_filters_future_config_and_only_returns_currently_allowed_participants(self):
        self.configure_comparison()
        latest = self.generate_comparison()
        response = self.client.put("/_helper/api/admin/gallery/comparison", headers=self.auth,
                                   json=self.comparison(prompt="Future private shared prompt; not yet executed"))
        self.assertEqual(response.status_code, 200, response.text)
        ordinary = self.portal()
        response = self.client.get("/_helper/api/portal/gallery/comparison", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        visible = response.json()
        self.assertEqual(visible["config"]["group_ids"], [3])
        self.assertNotIn("prompt", visible["config"])
        self.assertEqual(visible["round"]["id"], latest["id"])
        self.assertEqual(visible["round"]["prompt"], COMPARISON_PROMPT)
        self.assertEqual([item["group_id"] for item in visible["round"]["items"]], [3])
        self.assertEqual(visible["round"]["items"][0]["work"]["round_id"], latest["id"])
        for hidden in ("Kimi gallery", "fixture-kimi", "Private Kimi standalone prompt", "Future private shared prompt",
                       "key_id", "anthropic-key-server-only", "kimi-key-server-only"):
            self.assertNotIn(hidden, response.text)
        self.upstream.allowed["user-jwt"] = []
        response = self.client.get("/_helper/api/portal/gallery/comparison", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["config"])
        self.assertIsNone(response.json()["round"])

    def test_public_comparison_status_depends_only_on_visible_items_not_hidden_failures(self):
        ordinary = self.portal()
        hidden_state = {
            "config": {"group_ids": [3, 4], "prompt": "Unexecuted private configuration", "enabled": True,
                       "interval_minutes": 30, "next_at": None, "running": False},
            "round": {"id": 17, "prompt": COMPARISON_PROMPT, "status": "partial", "created_at": "2026-11-18T00:00:00Z", "finished_at": "2026-11-18T00:00:01Z",
                      "items": [
                          {"group_id": 3, "name": "Anthropic gallery", "model": "fixture-claude", "status": "success",
                           "work": {"id": 11, "group_id": 3, "round_id": 17, "status": "success", "kind": "html", "content": "<svg></svg>",
                                    "prompt": "Do not expose prompt in nested list metadata", "key_id": 31, "api_key": MODEL_KEY}},
                          {"group_id": 4, "name": "Hidden failed Kimi", "model": "hidden-model", "status": "failed",
                           "work": {"id": 12, "group_id": 4, "round_id": 17, "status": "failed", "error": "Hidden failure details"}},
                      ]},
        }
        with patch.object(self.app.state.gallery, "comparison", return_value=hidden_state):
            response = self.client.get("/_helper/api/portal/gallery/comparison", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["round"]["status"], "success")
        self.assertEqual(len(response.json()["round"]["items"]), 1)
        for hidden in ("Hidden failed Kimi", "hidden-model", "Hidden failure details", "Unexecuted private configuration",
                       "Do not expose prompt", "key_id", MODEL_KEY):
            self.assertNotIn(hidden, response.text)

    def test_public_comparison_requires_a_session_and_an_enabled_benchmark_module(self):
        self.assertEqual(self.client.get("/_helper/api/portal/gallery/comparison").status_code, 401)
        ordinary = self.portal()
        self.store.set_setting("modules", {"benchmark": False, "pricing": False, "ranking": False})
        response = self.client.get("/_helper/api/portal/gallery/comparison", headers=ordinary)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.upstream.available_calls, [])

    def test_admin_options_include_official_groups_even_before_keys_or_gallery_config_exist(self):
        self.assertEqual(self.client.get("/_helper/api/admin/gallery/options").status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/admin/gallery/options", headers=self.portal()).status_code, 401)
        response = self.client.get("/_helper/api/admin/gallery/options", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual({group["id"] for group in data["groups"]}, {3, 4, 5})
        self.assertTrue(any(group["name"] == "GLM without model key" for group in data["groups"]))
        self.assertEqual({key["group_id"] for key in data["keys"]}, {3, 4})
        self.assertIsNone(data["group_error"])
        self.assertIsNone(data["key_error"])
        self.assertNotIn("private_internal", response.text)
        self.assertNotIn(MODEL_KEY, response.text)
        self.assertEqual(self.client.get("/_helper/api/admin/gallery", headers=self.auth).json()["groups"], [])

    def test_admin_options_degrade_independently_when_one_catalog_is_unavailable(self):
        self.upstream.key_catalog_error = UpstreamError("Model key catalog unavailable", 403)
        response = self.client.get("/_helper/api/admin/gallery/options", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({group["id"] for group in response.json()["groups"]}, {3, 4, 5})
        self.assertEqual(response.json()["keys"], [])
        self.assertIsNotNone(response.json()["key_error"])
        self.assertIsNone(response.json()["group_error"])
        self.upstream.key_catalog_error = None
        self.upstream.group_catalog_error = UpstreamError("Group directory unavailable", 503)
        response = self.client.get("/_helper/api/admin/gallery/options", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["groups"], [])
        self.assertEqual({key["id"] for key in response.json()["keys"]}, {31, 41})
        self.assertIsNotNone(response.json()["group_error"])
        self.assertIsNone(response.json()["key_error"])

    def test_reasoning_effort_supports_the_documented_choices_and_defaults(self):
        self.configure()
        self.assertEqual(self.admin_view()["group"]["reasoning_effort"], "default")
        for effort in ("default", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                self.configure(reasoning_effort=effort)
                self.assertEqual(self.admin_view()["group"]["reasoning_effort"], effort)
        for effort in ("auto", "HIGH", "very-high", None, 1):
            with self.subTest(invalid=effort):
                lookups = list(self.upstream.key_lookups)
                response = self.client.put("/_helper/api/admin/gallery/3", headers=self.auth, json=self.job(reasoning_effort=effort))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.upstream.key_lookups, lookups)

    def test_public_work_preserves_executed_reasoning_setting_after_group_configuration_changes(self):
        self.configure(reasoning_effort="high")
        summary = self.generate_one()
        self.assertEqual(self.generated[0]["reasoning_effort"], "high")
        self.configure(reasoning_effort="low", prompt="Future prompt stays private")
        ordinary = self.portal()
        listing = self.client.get("/_helper/api/portal/gallery", headers=ordinary)
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()["groups"][0]["reasoning_effort"], "low")
        self.assertNotIn("key_id", listing.text)
        detail = self.client.get("/_helper/api/portal/gallery/3", headers=ordinary)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["group"]["reasoning_effort"], "low")
        self.assertEqual(detail.json()["works"][0]["reasoning_effort"], "high")
        self.assertNotIn("Future prompt stays private", detail.text)
        work = self.client.get(f"/_helper/api/portal/gallery/3/works/{summary['id']}", headers=ordinary)
        self.assertEqual(work.status_code, 200, work.text)
        self.assertEqual(work.json()["reasoning_effort"], "high")
        self.assertEqual(work.json()["prompt"], CONFIG_PROMPT)
        self.assertNotIn(MODEL_KEY, work.text)

    def test_public_comparison_retains_each_visible_models_requested_reasoning_setting(self):
        self.configure_comparison()
        self.configure(3, model="fixture-claude", reasoning_effort="xhigh")
        self.configure(4, model="fixture-kimi", reasoning_effort="none")
        completed = self.generate_comparison()
        self.assertEqual({item["group_id"]: item["reasoning_effort"] for item in completed["items"]}, {3: "xhigh", 4: "none"})
        self.configure(3, reasoning_effort="low")
        response = self.client.get("/_helper/api/portal/gallery/comparison", headers=self.portal())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["round"]["items"]), 1)
        item = response.json()["round"]["items"][0]
        self.assertEqual(item["reasoning_effort"], "xhigh")
        self.assertEqual(item["work"]["reasoning_effort"], "xhigh")
        self.assertNotIn("fixture-kimi", response.text)
        self.assertNotIn("key_id", response.text)

    def test_feed_literal_routes_require_their_own_auth_and_return_an_empty_snapshot(self):
        ordinary = self.portal()
        self.assertEqual(self.client.get("/_helper/api/admin/gallery/feed").status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/admin/gallery/feed", headers=ordinary).status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/admin/gallery/feed", headers={"Authorization": "Bearer " + self.config.sub2api_admin_key}).status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/portal/gallery/feed").status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/portal/gallery/feed", headers=self.auth).status_code, 401)
        for path, headers in (("/_helper/api/admin/gallery/feed", self.auth), ("/_helper/api/portal/gallery/feed", ordinary)):
            with self.subTest(path=path):
                response = self.client.get(path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"items": [], "next_at": None, "timezone": self.config.timezone})
        self.assertEqual(self.upstream.available_calls, ["user-jwt"])
        self.generator.assert_not_awaited()

    def test_feed_returns_recent_works_together_after_one_current_group_access_check(self):
        self.configure(3, name="Visible Anthropic", model="visible-claude", reasoning_effort="high")
        self.configure(4, name="Private Kimi source", model="private-kimi", prompt="Hidden future instructions")
        first = self.generate_one(3)
        latest = self.generate_one(3)
        hidden = self.generate_one(4)
        ordinary = self.portal()
        self.upstream.available_calls.clear()
        with patch.object(self.app.state.gallery, "feed", wraps=self.app.state.gallery.feed) as snapshot:
            response = self.client.get("/_helper/api/portal/gallery/feed", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        snapshot.assert_called_once_with(allowed={3})
        self.assertEqual(self.upstream.available_calls, ["user-jwt"], "One aggregate feed must resolve current ACL only once")
        items = response.json()["items"]
        self.assertEqual([item["work"]["id"] for item in items], [latest["id"]])
        self.assertEqual([w["id"] for w in items[0]["history"]], [first["id"]])
        self.assertNotIn("benchmark", items[0])
        self.assertEqual((items[0]["group_name"], items[0]["platform"]), ("Group 3", "openai"))
        self.assertTrue(all(item["group_id"] == 3 and item["name"] == "Visible Anthropic" for item in items))
        self.assertTrue(all(item["reasoning_effort"] == "high" and item["work"]["reasoning_effort"] == "high" for item in items))
        self.assertIn("preview", items[0]["work"])
        self.assertIn("preview", items[0]["history"][0])
        for private in ("Private Kimi source", "private-kimi", "Hidden future instructions", CONFIG_PROMPT,
                        "key_id", "api_key", "content_bytes", MODEL_KEY, self.config.sub2api_admin_key):
            self.assertNotIn(private, response.text)
        self.assertIsNone(response.json()["next_at"])
        self.upstream.available_calls.clear()
        admin = self.client.get("/_helper/api/admin/gallery/feed", headers=self.auth)
        self.assertEqual(admin.status_code, 200, admin.text)
        self.assertEqual({item["work"]["id"] for item in admin.json()["items"]}, {latest["id"], hidden["id"]}, "One row per source with latest work")
        self.assertEqual(self.upstream.available_calls, [], "Management feed does not borrow an ordinary user's group access")

    def test_feed_hides_restricted_schedule_and_rechecks_revoked_group_access(self):
        self.configure(3, enabled=False)
        self.configure(4, name="Private scheduled source", enabled=True, interval_minutes=5)
        self.generate_one(3)
        ordinary = self.portal()
        management = self.client.get("/_helper/api/admin/gallery/feed", headers=self.auth)
        self.assertEqual(management.status_code, 200, management.text)
        self.assertIsNotNone(management.json()["next_at"])
        response = self.client.get("/_helper/api/portal/gallery/feed", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["next_at"], "A hidden source must not disclose its upcoming execution time")
        self.assertNotIn("Private scheduled source", response.text)
        self.upstream.allowed["user-jwt"] = []
        self.upstream.available_calls.clear()
        response = self.client.get("/_helper/api/portal/gallery/feed", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])
        self.assertIsNone(response.json()["next_at"])
        self.assertEqual(self.upstream.available_calls, ["user-jwt"])

    def test_feed_does_not_invent_placeholders_for_unexecuted_configured_sources(self):
        self.configure(3)
        self.configure(4)
        for path, headers in (("/_helper/api/admin/gallery/feed", self.auth), ("/_helper/api/portal/gallery/feed", self.portal())):
            response = self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["items"], [], "Configuration alone does not prove participation in a generation round")
        self.generator.assert_not_awaited()

    def test_feed_projection_excludes_future_and_nested_internal_fields(self):
        ordinary = self.portal()
        data = {"items": [
            {"group_id": 3, "name": "Visible source", "model": "visible-model", "reasoning_effort": "xhigh", "status": "success",
             "created_at": "2026-11-18T00:00:00Z", "round_id": 12, "prompt": "Private future prompt", "key_id": 31,
             "work": {"id": 123, "group_id": 3, "round_id": 12, "title": "Visible artwork", "model": "visible-model",
                      "reasoning_effort": "xhigh", "status": "success", "kind": "html", "preview": "<p>Safe preview</p>",
                      "prompt": "Private executed prompt only belongs in detail", "api_key": MODEL_KEY, "key_id": 31,
                      "content_bytes": 10, "future_internal": "Do not expose future metadata"}},
            {"group_id": 4, "name": "Unauthorized source", "model": "unauthorized-model", "status": "queued", "round_id": 12},
        ], "next_at": None, "config": {"prompt": "Private future prompt"}}
        with patch.object(self.app.state.gallery, "feed", return_value=data) as snapshot:
            response = self.client.get("/_helper/api/portal/gallery/feed", headers=ordinary)
        self.assertEqual(response.status_code, 200, response.text)
        snapshot.assert_called_once_with(allowed={3})
        self.assertEqual(set(response.json()), {"items", "next_at", "timezone"})
        self.assertEqual(len(response.json()["items"]), 1)
        item = response.json()["items"][0]
        self.assertEqual(set(item), {"group_id", "name", "model", "reasoning_effort", "status", "created_at", "round_id", "work", "history", "group_name", "platform"})
        self.assertEqual(item["work"]["preview"], "<p>Safe preview</p>")
        for private in ("Unauthorized source", "unauthorized-model", "Private future prompt", "Private executed prompt", "key_id",
                        "api_key", "content_bytes", "future_internal", MODEL_KEY):
            self.assertNotIn(private, response.text)

    def test_feed_rejects_disabled_module_profile_and_acl_errors_before_snapshot_read(self):
        ordinary = self.portal()
        with patch.object(self.app.state.gallery, "feed", wraps=self.app.state.gallery.feed) as snapshot:
            self.store.set_setting("modules", {"benchmark": False})
            response = self.client.get("/_helper/api/portal/gallery/feed", headers=ordinary)
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(self.upstream.available_calls, [])
            snapshot.assert_not_called()
            self.store.set_setting("modules", {"benchmark": True})
            self.upstream.users[0]["status"] = "disabled"
            response = self.client.get("/_helper/api/portal/gallery/feed", headers=ordinary)
            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(self.upstream.available_calls, [])
            snapshot.assert_not_called()
            self.upstream.users[0]["status"] = "active"
            self.upstream.allowed["user-jwt"] = UpstreamError("Current group access unavailable", 503)
            response = self.client.get("/_helper/api/portal/gallery/feed", headers=ordinary)
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(self.upstream.available_calls, ["user-jwt"])
            snapshot.assert_not_called()

    def test_shared_round_feed_exposes_only_visible_participants_and_no_shared_prompt(self):
        self.configure_comparison()
        completed = self.generate_comparison()
        response = self.client.get("/_helper/api/portal/gallery/feed", headers=self.portal())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["items"]), 1)
        item = response.json()["items"][0]
        self.assertEqual(item["group_id"], 3)
        self.assertEqual(item["round_id"], completed["id"])
        self.assertEqual(item["work"]["round_id"], completed["id"])
        self.assertEqual(item["model"], "fixture-claude")
        for private in ("Kimi gallery", "fixture-kimi", COMPARISON_PROMPT, "Private Anthropic standalone prompt",
                        "key_id", "anthropic-key-server-only", "kimi-key-server-only"):
            self.assertNotIn(private, response.text)


if __name__ == "__main__":
    unittest.main()
