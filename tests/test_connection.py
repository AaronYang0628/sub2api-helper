"""Connection bootstrap, credential verification and session lifecycle tests."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from helper.app import create_app
from helper.config import Config
from helper.credentials import CredentialVault
from helper.store import Store
from helper.upstream import UpstreamError


URL = "https://sub2api.example.test"
KEY = "valid-sub2api-administrator-credential"


class BindingUpstream:
    def __init__(self, url="", key=""):
        self.base_url = url
        self.admin_key = key
        self.calls = []
        self.closed = False
        self.verification_error = None

    async def request(self, method, path, params=None):
        self.calls.append((method, path, params))
        if self.verification_error:
            raise self.verification_error
        return {"required": False}

    async def close(self):
        self.closed = True


class Candidate:
    def __init__(self, config, responses):
        self.base_url = config.sub2api_url
        self.admin_key = config.sub2api_admin_key
        self.responses = responses
        self.calls = []
        self.closed = False

    async def request(self, method, path, params=None):
        self.calls.append((method, path, params))
        if isinstance(self.responses, Exception):
            raise self.responses
        result = self.responses.get(path, UpstreamError("读取权限不足", 403))
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)

    async def close(self):
        self.closed = True


class CandidateFactory:
    def __init__(self):
        self.instances = []
        self.responses = {}

    def __call__(self, config):
        responses = self.responses.get(config.sub2api_admin_key, {"/api/v1/admin/compliance": {"required": False}})
        candidate = Candidate(config, responses)
        self.instances.append(candidate)
        return candidate


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(sub2api_url="", sub2api_admin_key="", admin_token="internal-secret-not-needed-by-ui", db_path=":memory:")
        self.store = Store(":memory:")
        self.upstream = BindingUpstream()
        self.factory = CandidateFactory()
        self.app = create_app(self.config, store=self.store, upstream=self.upstream, connector=self.factory, background=False)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def connect(self, **changes):
        return self.client.post("/_helper/api/admin/connection", json={"url": URL, "admin_key": KEY} | changes)

    def bearer(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["session"]
        self.assertNotEqual(token, KEY)
        self.assertNotEqual(token, self.config.admin_token)
        self.assertGreaterEqual(len(token), 32)
        self.assertNotIn(KEY, response.text)
        return {"Authorization": "Bearer " + token}

    def test_public_connection_info_never_returns_credentials(self):
        response = self.client.get("/_helper/api/admin/connection")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["configured"])
        self.assertEqual(response.json()["upstream_url"], "")
        self.assertNotIn("admin_key", response.json())
        self.assertNotIn(self.config.admin_token, response.text)
        self.assertEqual(self.client.get("/_helper/api/admin/status").status_code, 401)

    def test_valid_key_binds_instance_and_issues_independent_opaque_session(self):
        response = self.connect()
        auth = self.bearer(response)
        self.assertEqual(self.upstream.base_url, URL)
        self.assertEqual(self.upstream.admin_key, KEY)
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=auth).status_code, 200)
        self.assertEqual(self.client.get("/_helper/api/admin/connection").json()["upstream_url"], URL)
        self.assertTrue(self.client.get("/_helper/api/admin/connection").json()["configured"])
        self.assertNotIn(KEY, self.store.get_setting("connection_sealed"))
        self.assertNotIn(KEY, str(self.store.audits()))
        candidate = self.factory.instances[0]
        self.assertEqual(candidate.calls, [("GET", "/api/v1/admin/compliance", None)])
        self.assertTrue(candidate.closed)

    def test_invalid_key_and_no_response_do_not_change_binding_or_echo_input(self):
        for key, failure in (("invalid-sensitive-admin-key", UpstreamError("凭据无效", 401)), ("unreachable-sensitive-admin-key", UpstreamError("无法连接上游", 502))):
            with self.subTest(key=key):
                self.factory.responses[key] = failure
                response = self.connect(admin_key=key)
                self.assertGreaterEqual(response.status_code, 400)
                self.assertNotIn(key, response.text)
                self.assertEqual(self.upstream.base_url, "")
                self.assertEqual(self.upstream.admin_key, "")
                self.assertIsNone(self.store.get_setting("connection_sealed"))
                self.assertFalse(self.client.get("/_helper/api/admin/connection").json()["configured"])
                self.assertTrue(self.factory.instances[-1].closed)

    def test_malformed_admin_verification_never_establishes_binding(self):
        for i, payload in enumerate((None, {}, [], {"required": "false"}, {"required": 0})):
            with self.subTest(payload=payload):
                key = "malformed-admin-secret-" + str(i)
                self.factory.responses[key] = {"/api/v1/admin/compliance": payload}
                response = self.connect(admin_key=key)
                self.assertEqual(response.status_code, 502)
                self.assertNotIn(key, response.text)
                self.assertIsNone(self.store.get_setting("connection_sealed"))
                self.assertEqual(self.upstream.admin_key, "")

    def test_malformed_connection_input_is_redacted_before_validation_response(self):
        secret = "input-secret-must-never-be-echoed"
        for payload in (
            {"url": "https://user:" + secret + "@sub2api.example.test", "admin_key": secret},
            {"url": URL, "admin_key": {"sensitive": secret}},
            {"url": "file:///private/" + secret, "admin_key": secret},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/_helper/api/admin/connection", json=payload)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(secret, response.text)
                self.assertNotIn("input", str(response.json()))
        self.assertEqual(self.factory.instances, [])

    def test_bound_instance_rejects_url_change_before_any_network(self):
        self.bearer(self.connect())
        calls_before = len(self.factory.instances)
        response = self.connect(url="https://unrelated.example.test", admin_key="another-server-key")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.factory.instances), calls_before)
        self.assertEqual(self.upstream.base_url, URL)
        self.assertEqual(self.upstream.admin_key, KEY)

    def test_environment_url_restriction_applies_before_first_binding(self):
        store = Store(":memory:")
        upstream = BindingUpstream(URL)
        factory = CandidateFactory()
        app = create_app(replace(self.config, sub2api_url=URL), store=store, upstream=upstream, connector=factory, background=False)
        with TestClient(app) as client:
            response = client.post("/_helper/api/admin/connection", json={"url": "https://wrong.example.test", "admin_key": KEY})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(factory.instances, [])
            self.assertIsNone(store.get_setting("connection_sealed"))

    def test_key_rotation_for_same_instance_revokes_old_helper_session(self):
        old_auth = self.bearer(self.connect())
        rotated = "rotated-valid-administrator-credential"
        response = self.connect(admin_key=rotated)
        new_auth = self.bearer(response)
        self.assertEqual(self.upstream.admin_key, rotated)
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=old_auth).status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=new_auth).status_code, 200)
        self.assertNotIn(rotated, response.text)
        self.assertNotIn(rotated, str(self.store.audits()))

    def test_failed_rotation_preserves_current_key_and_current_session(self):
        auth = self.bearer(self.connect())
        self.factory.responses["bad-rotation-key"] = UpstreamError("凭据无效", 401)
        before = self.store.get_setting("connection_sealed")
        response = self.connect(admin_key="bad-rotation-key")
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(self.upstream.admin_key, KEY)
        self.assertEqual(self.store.get_setting("connection_sealed"), before)
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=auth).status_code, 200)

    def test_scoped_key_may_verify_with_accounts_when_compliance_is_forbidden(self):
        scoped = "scoped-valid-administrator-key"
        self.factory.responses[scoped] = {
            "/api/v1/admin/compliance": UpstreamError("无 compliance 权限", 403),
            "/api/v1/admin/accounts": {"items": [], "total": 0},
        }
        response = self.connect(admin_key=scoped)
        auth = self.bearer(response)
        self.assertTrue(response.json()["warning"])
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=auth).status_code, 200)
        self.assertEqual([call[1] for call in self.factory.instances[-1].calls], ["/api/v1/admin/compliance", "/api/v1/admin/accounts"])

    def test_key_without_any_read_permission_is_not_accepted(self):
        self.factory.responses["no-read-permission"] = UpstreamError("读取权限不足", 403)
        response = self.connect(admin_key="no-read-permission")
        self.assertGreaterEqual(response.status_code, 400)
        self.assertIsNone(self.store.get_setting("connection_sealed"))
        self.assertEqual(self.upstream.admin_key, "")
        self.assertEqual(len(self.factory.instances[0].calls), 4)

    def test_scoped_verification_continues_to_users_when_accounts_are_forbidden(self):
        scoped = "users-only-administrator-key"
        self.factory.responses[scoped] = {
            "/api/v1/admin/compliance": UpstreamError("读取权限不足", 403),
            "/api/v1/admin/accounts": UpstreamError("读取权限不足", 403),
            "/api/v1/admin/users": {"items": [], "total": 0},
        }
        response = self.connect(admin_key=scoped)
        self.bearer(response)
        self.assertEqual([call[1] for call in self.factory.instances[-1].calls], ["/api/v1/admin/compliance", "/api/v1/admin/accounts", "/api/v1/admin/users"])

    def test_browser_public_url_is_saved_only_after_successful_verification(self):
        public = "https://helper.example.test"
        self.factory.responses["invalid-key"] = UpstreamError("凭据无效", 401)
        failed = self.connect(admin_key="invalid-key", helper_url=public)
        self.assertGreaterEqual(failed.status_code, 400)
        self.assertIsNone(self.store.get_setting("public_url"))
        self.assertEqual(self.client.get("/_helper/api/admin/connection").json()["public_url"], "")
        self.bearer(self.connect(helper_url=public))
        self.assertEqual(self.store.get_setting("public_url"), public)
        self.assertEqual(self.client.get("/_helper/api/admin/connection").json()["public_url"], public)
        self.bearer(self.connect(helper_url="https://unrelated.example.test"))
        self.assertEqual(self.store.get_setting("public_url"), public, "The existing configured origin must not drift on later logins")

    def test_environment_public_url_takes_precedence_over_browser_suggestion(self):
        store = Store(":memory:")
        public = "https://configured-helper.example.test"
        app = create_app(replace(self.config, public_url=public), store=store, upstream=BindingUpstream(), connector=CandidateFactory(), background=False)
        with TestClient(app) as client:
            response = client.post("/_helper/api/admin/connection", json={"url": URL, "admin_key": KEY, "helper_url": "https://browser-suggestion.example.test"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/_helper/api/admin/connection").json()["public_url"], public)

    def test_compliance_required_is_a_warning_instead_of_a_fake_auth_failure(self):
        self.factory.responses[KEY] = {"/api/v1/admin/compliance": {"required": True}}
        response = self.connect()
        self.bearer(response)
        self.assertIn("确认", response.json()["warning"])
        self.assertTrue(self.client.get("/_helper/api/admin/connection").json()["configured"])

    def test_disconnect_revokes_only_the_provided_opaque_session(self):
        first = self.bearer(self.connect())
        second = self.bearer(self.connect())
        response = self.client.post("/_helper/api/admin/connection/disconnect", headers=first)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=first).status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/admin/status", headers=second).status_code, 200)
        self.assertTrue(self.client.get("/_helper/api/admin/connection").json()["configured"], "Disconnect must not erase the persisted worker credential")

    def test_upstream_revocation_is_rechecked_and_invalidates_issued_sessions(self):
        issued_at = time.monotonic()
        auth = self.bearer(self.connect())
        self.upstream.verification_error = UpstreamError("上游凭据已失效", 401)
        clock = SimpleNamespace(monotonic=lambda: issued_at + 31)
        with patch("helper.app.time", clock):
            response = self.client.get("/_helper/api/admin/status", headers=auth)
            self.assertGreaterEqual(response.status_code, 400)
            self.assertTrue(self.upstream.calls)
            # Clearing local sessions persists after the upstream becomes reachable.
            self.upstream.verification_error = None
            self.assertEqual(self.client.get("/_helper/api/admin/status", headers=auth).status_code, 401)

    def test_expired_connection_session_cannot_access_management(self):
        issued_at = time.monotonic()
        auth = self.bearer(self.connect())
        with patch("helper.app.time", SimpleNamespace(monotonic=lambda: issued_at + 8 * 3600 + 1)):
            self.assertEqual(self.client.get("/_helper/api/admin/status", headers=auth).status_code, 401)
        self.assertEqual(self.upstream.calls, [])

    def test_persisted_binding_survives_restart_but_browser_sessions_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "helper.db")
            config = replace(self.config, db_path=path)
            store = Store(path)
            factory = CandidateFactory()
            first_app = create_app(config, store=store, upstream=BindingUpstream(), connector=factory, background=False)
            with TestClient(first_app) as client:
                response = client.post("/_helper/api/admin/connection", json={"url": URL, "admin_key": KEY, "helper_url": "https://helper.example.test"})
                self.assertEqual(response.status_code, 200)
                old_auth = {"Authorization": "Bearer " + response.json()["session"]}
                self.assertEqual(CredentialVault(store, path).read(), {"url": URL, "admin_key": KEY})
                self.assertNotIn(KEY.encode(), Path(path).read_bytes())
            second_store = Store(path)
            second_factory = CandidateFactory()
            second_app = create_app(config, store=second_store, upstream=BindingUpstream(URL, KEY), connector=second_factory, background=False)
            with TestClient(second_app) as client:
                info = client.get("/_helper/api/admin/connection")
                self.assertEqual(info.status_code, 200)
                self.assertTrue(info.json()["configured"])
                self.assertEqual(info.json()["upstream_url"], URL)
                self.assertEqual(info.json()["public_url"], "https://helper.example.test")
                self.assertNotIn(KEY, info.text)
                self.assertEqual(client.get("/_helper/api/admin/status", headers=old_auth).status_code, 401)
                wrong = client.post("/_helper/api/admin/connection", json={"url": "https://other.example.test", "admin_key": KEY})
                self.assertEqual(wrong.status_code, 409)
                self.assertEqual(second_factory.instances, [])
                valid = client.post("/_helper/api/admin/connection", json={"url": URL, "admin_key": KEY})
                self.assertEqual(valid.status_code, 200)

    def test_configuration_can_bootstrap_without_environment_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            first = Config.from_env()
            second = Config.from_env()
        self.assertEqual(first.sub2api_url, "")
        self.assertEqual(first.sub2api_admin_key, "")
        self.assertGreaterEqual(len(first.admin_token), 24)
        self.assertNotEqual(first.admin_token, second.admin_token)
        self.assertNotIn(first.admin_token, repr(first))


if __name__ == "__main__":
    unittest.main()
