"""Native UI routing and browser-JWT authorization, without a live sub2api."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
import httpx

from helper.app import create_app
from helper.config import Config
from helper.store import Store
from helper.upstream import UpstreamError
try:
    from tests.test_app import FakeMailer, FakeUpstream
except ModuleNotFoundError:
    from test_app import FakeMailer, FakeUpstream


USER_AGENT = "NativeIntegrationBrowser/1.0"
JWT = "native-admin-browser-jwt"
UPSTREAM_CSP = "default-src 'self'; script-src 'self' https://assets.sub2api.example.test"


class FakeNativeProxy:
    def __init__(self):
        self.forward_calls = []
        self.profile_calls = []
        self.websocket_calls = []
        self.closed = False
        self.profiles = {
            JWT: {"id": 99, "role": "admin", "status": "active", "username": "Native admin"},
            "ordinary-jwt": {"id": 7, "role": "user", "status": "active"},
            "disabled-jwt": {"id": 99, "role": "admin", "status": "disabled"},
            "revoked-jwt": UpstreamError("Browser login revoked", 401),
        }

    async def profile(self, token, user_agent):
        self.profile_calls.append((token, user_agent))
        result = self.profiles.get(token, UpstreamError("Not an upstream browser session", 401))
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)

    async def forward(self, request, path):
        request.state.helper_proxied = True
        body = b"".join([chunk async for chunk in request.stream()])
        self.forward_calls.append({"method": request.method, "path": request.url.path, "argument": path,
                                   "query": request.url.query, "body": body,
                                   "content_length": request.headers.get("content-length"),
                                   "transfer_encoding": request.headers.get("transfer-encoding")})
        output = b"<html><body>Original sub2api application</body></html>" if request.url.path == "/" else body or b"native upstream response"

        async def stream():
            yield output[:11]
            yield output[11:]

        return StreamingResponse(stream(), status_code=207, headers={
            "Content-Security-Policy": UPSTREAM_CSP,
            "Cache-Control": "public, max-age=300",
            "Referrer-Policy": "same-origin",
            "X-Upstream-Response": "preserved",
        }, media_type="text/html" if request.url.path == "/" else "application/octet-stream")

    async def websocket(self, websocket, path):
        self.websocket_calls.append((path, websocket.url.path, websocket.url.query))
        await websocket.accept()
        await websocket.send_json({"source": "native-proxy"})
        await websocket.close(code=1000)

    async def close(self):
        self.closed = True


class NativeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="server-administrator-key",
                             admin_token="helper-local-automation-token", native_ui=True,
                             public_url="https://helper.example.test", db_path=self.temp.name + "/helper.db")
        self.store = Store(self.config.db_path)
        self.upstream = FakeUpstream()
        self.proxy = FakeNativeProxy()
        self.app = create_app(self.config, upstream=self.upstream, store=self.store, mailer=FakeMailer(),
                              background=False, native_proxy=self.proxy)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()

    def tearDown(self):
        if self.client is not None:
            self.client.__exit__(None, None, None)
        self.temp.cleanup()

    @staticmethod
    def auth(token=JWT, user_agent=USER_AGENT):
        return {"Authorization": "Bearer " + token, "User-Agent": user_agent}

    def session(self):
        response = self.client.post("/_helper/api/session", headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()), {"session"})
        self.assertNotIn(JWT, response.text)
        self.assertNotEqual(response.json()["session"], JWT)
        return response.json()["session"]

    def test_native_root_uses_original_application_and_preserves_proxy_response_headers(self):
        response = self.client.get("/?from=integration", headers={"User-Agent": USER_AGENT})
        self.assertEqual(response.status_code, 207, response.text)
        self.assertIn("Original sub2api application", response.text)
        self.assertEqual(self.proxy.forward_calls[0]["path"], "/")
        self.assertEqual(self.proxy.forward_calls[0]["query"], "from=integration")
        self.assertEqual(response.headers["content-security-policy"], UPSTREAM_CSP)
        self.assertEqual(response.headers["cache-control"], "public, max-age=300")
        self.assertEqual(response.headers["referrer-policy"], "same-origin")
        self.assertEqual(response.headers["x-upstream-response"], "preserved")
        self.assertEqual(self.proxy.profile_calls, [])

    def test_native_status_requires_an_actual_active_admin_browser_jwt(self):
        response = self.client.get("/_helper/api/status", headers=self.auth())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.proxy.profile_calls, [(JWT, USER_AGENT)])
        for secret in (JWT, self.config.sub2api_admin_key, self.config.admin_token):
            self.assertNotIn(secret, response.text)
        self.assertEqual(self.proxy.forward_calls, [])

    def test_management_keys_do_not_bypass_native_browser_identity_validation(self):
        for token in (None, "", self.config.sub2api_admin_key, self.config.admin_token, "not-a-session", "revoked-jwt"):
            with self.subTest(token_kind="missing" if token is None else "invalid-or-management-key"):
                headers = {} if token is None else self.auth(token)
                response = self.client.get("/_helper/api/status", headers=headers)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertNotIn(self.config.sub2api_admin_key, response.text)
                self.assertNotIn(self.config.admin_token, response.text)
        self.assertEqual(self.proxy.forward_calls, [])

    def test_ordinary_and_disabled_users_cannot_mint_helper_sessions(self):
        for token in ("ordinary-jwt", "disabled-jwt"):
            with self.subTest(token=token):
                for method, endpoint in (("GET", "/_helper/api/status"), ("POST", "/_helper/api/session")):
                    response = self.client.request(method, endpoint, headers=self.auth(token))
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertNotIn("session", response.json().keys())

    def test_malformed_native_profiles_are_rejected_instead_of_defaulting_to_admin(self):
        malformed = [None, [], {}, {"id": 99, "role": "admin"}, {"role": "admin", "status": "active"},
                     {"id": -1, "role": "admin", "status": "active"}, {"id": True, "role": "admin", "status": "active"},
                     {"id": "99", "role": "admin", "status": "active"}, {"id": 99, "role": "owner", "status": "active"}]
        for profile in malformed:
            with self.subTest(profile=profile):
                self.proxy.profiles[JWT] = profile
                response = self.client.get("/_helper/api/status", headers=self.auth())
                self.assertEqual(response.status_code, 502, response.text)
                self.assertNotIn(JWT, response.text)

    def test_binding_must_be_explicitly_false_before_any_browser_profile_request(self):
        original = deepcopy(self.upstream.settings_data)
        for value in (True, None, 0, "false", "unknown"):
            with self.subTest(value=value):
                self.upstream.settings_data = original | {"session_binding_enabled": value}
                self.proxy.profile_calls.clear()
                response = self.client.get("/_helper/api/status", headers=self.auth())
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(self.proxy.profile_calls, [])
        self.upstream.settings_data = {key: value for key, value in original.items() if key != "session_binding_enabled"}
        response = self.client.get("/_helper/api/status", headers=self.auth())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.proxy.profile_calls, [])

    def test_settings_failure_prevents_profile_validation_and_does_not_fallback(self):
        async def unavailable():
            raise UpstreamError("Cannot read upstream settings", 503)

        self.upstream.settings = unavailable
        response = self.client.get("/_helper/api/status", headers=self.auth())
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.proxy.profile_calls, [])

    def test_opaque_native_session_authorizes_legacy_management_with_fresh_identity(self):
        opaque = self.session()
        self.proxy.profile_calls.clear()
        for _ in range(2):
            response = self.client.get("/api/status", headers=self.auth(opaque))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn(JWT, response.text)
        self.assertEqual(self.proxy.profile_calls, [(JWT, USER_AGENT), (JWT, USER_AGENT)])
        for path in Path(self.temp.name).glob("*.db*"):
            self.assertNotIn(JWT.encode(), path.read_bytes(), "Browser JWT must not be persisted in SQLite or WAL")
        self.assertEqual(self.client.get("/api/status", headers=self.auth()).status_code, 401)

    def test_native_session_rechecks_revocation_on_the_very_next_legacy_request(self):
        opaque = self.session()
        self.proxy.profiles[JWT] = UpstreamError("Browser login revoked", 401)
        response = self.client.get("/api/status", headers=self.auth(opaque))
        self.assertEqual(response.status_code, 401, response.text)
        self.proxy.profiles[JWT] = {"id": 99, "role": "admin", "status": "active"}
        response = self.client.get("/api/status", headers=self.auth(opaque))
        self.assertEqual(response.status_code, 401, "An invalidated opaque session must not return when the JWT becomes valid again")

    def test_native_session_cannot_switch_browser_user_agent(self):
        opaque = self.session()
        self.proxy.profile_calls.clear()
        response = self.client.get("/api/status", headers=self.auth(opaque, "DifferentBrowser/2.0"))
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.proxy.profile_calls, [], "Reject changed UA before sending JWT upstream")
        self.assertEqual(self.client.get("/api/status", headers=self.auth(opaque)).status_code, 401)

    def test_native_session_cannot_change_administrator_identity(self):
        opaque = self.session()
        self.proxy.profiles[JWT] = {"id": 100, "role": "admin", "status": "active"}
        response = self.client.get("/api/status", headers=self.auth(opaque))
        self.assertEqual(response.status_code, 401, response.text)
        self.proxy.profiles[JWT] = {"id": 99, "role": "admin", "status": "active"}
        self.assertEqual(self.client.get("/api/status", headers=self.auth(opaque)).status_code, 401)

    def test_native_session_expires_after_fifteen_minutes(self):
        issued_at = time.monotonic()
        opaque = self.session()
        self.proxy.profile_calls.clear()
        with patch("helper.app.time", SimpleNamespace(monotonic=lambda: issued_at + 15 * 60 + 1)):
            response = self.client.get("/api/status", headers=self.auth(opaque))
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.proxy.profile_calls, [])

    def test_upstream_binding_becoming_unknown_also_revokes_native_session(self):
        opaque = self.session()
        self.upstream.settings_data["session_binding_enabled"] = True
        self.proxy.profile_calls.clear()
        response = self.client.get("/api/status", headers=self.auth(opaque))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.proxy.profile_calls, [])
        self.upstream.settings_data["session_binding_enabled"] = False
        self.assertEqual(self.client.get("/api/status", headers=self.auth(opaque)).status_code, 401)

    def test_disconnect_revokes_only_the_opaque_session(self):
        opaque = self.session()
        response = self.client.post("/api/connection/disconnect", headers=self.auth(opaque))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/status", headers=self.auth(opaque)).status_code, 401)
        self.assertEqual(self.client.get("/_helper/api/status", headers=self.auth()).status_code, 200)

    def test_upstream_logout_revokes_all_matching_helper_sessions_but_not_another_admin(self):
        first = self.session()
        second = self.session()
        other_jwt = "another-native-admin-jwt"
        self.proxy.profiles[other_jwt] = {"id": 100, "role": "admin", "status": "active"}
        response = self.client.post("/_helper/api/session", headers=self.auth(other_jwt))
        self.assertEqual(response.status_code, 200, response.text)
        other = response.json()["session"]
        for session in (first, second, other):
            self.assertEqual(self.client.get("/api/status", headers=self.auth(session)).status_code, 200)

        # The fake upstream intentionally still considers this JWT valid. This
        # proves the forwarding hook revokes local sessions immediately, rather
        # than relying only on an eventual upstream-profile rejection.
        logout = self.client.post("/api/v1/auth/logout", headers=self.auth(), json={"refresh_token": "fixture-refresh"})
        self.assertEqual(logout.status_code, 207, logout.text)
        self.assertEqual(self.proxy.forward_calls[-1]["path"], "/api/v1/auth/logout")
        self.proxy.profile_calls.clear()
        for session in (first, second):
            self.assertEqual(self.client.get("/api/status", headers=self.auth(session)).status_code, 401)
        self.assertEqual(self.proxy.profile_calls, [])
        self.assertEqual(self.client.get("/api/status", headers=self.auth(other)).status_code, 200)
        self.assertEqual(self.proxy.profile_calls, [(other_jwt, USER_AGENT)])

    def test_large_original_event_logging_request_is_not_limited_as_a_helper_api(self):
        payload = b'{"events":[{"message":"' + b"x" * 100000 + b'"}]}'
        response = self.client.post("/api/event_logging/batch?source=native", content=payload,
                                    headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 207, response.text[:100])
        self.assertEqual(response.content, payload)
        forwarded = self.proxy.forward_calls[-1]
        self.assertEqual(forwarded["path"], "/api/event_logging/batch")
        self.assertEqual(forwarded["query"], "source=native")
        self.assertEqual(forwarded["body"], payload)
        self.assertEqual(response.headers["content-security-policy"], UPSTREAM_CSP)
        self.assertEqual(response.headers["cache-control"], "public, max-age=300")
        self.assertEqual(self.proxy.profile_calls, [])

    def test_large_original_api_requests_bypass_helper_body_cap_and_stream_unmodified(self):
        payload = b"0123456789abcdef" * 8192
        for path in ("/api/v1/admin/original-import?dry_run=true", "/v1/chat/completions?stream=true"):
            with self.subTest(path=path):
                response = self.client.post(path, content=payload, headers={"Content-Type": "application/octet-stream"})
                self.assertEqual(response.status_code, 207, response.text[:100])
                self.assertEqual(response.content, payload)
                call = self.proxy.forward_calls[-1]
                self.assertEqual(call["method"], "POST")
                self.assertEqual(call["path"], path.split("?")[0])
                self.assertEqual(call["query"], path.split("?")[1])
                self.assertEqual(call["body"], payload)
                self.assertEqual(response.headers["content-security-policy"], UPSTREAM_CSP)
                self.assertEqual(response.headers["cache-control"], "public, max-age=300")

    def test_helper_mutations_keep_their_body_limit(self):
        payload = b"x" * 65537
        for path in ("/api/connection", "/api/schedules", "/_helper/api/session"):
            with self.subTest(path=path):
                response = self.client.post(path, content=payload, headers=self.auth() | {"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 413, response.text[:100])
        self.assertEqual(self.proxy.forward_calls, [])
        self.assertEqual(self.proxy.profile_calls, [])

    def test_health_keeps_helper_security_headers_and_does_not_proxy(self):
        response = self.client.get("/healthz", headers={"X-Helper-Proxied": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotEqual(response.headers["content-security-policy"], UPSTREAM_CSP)
        self.assertIn("frame-ancestors", response.headers["content-security-policy"])
        self.assertEqual(self.proxy.forward_calls, [])

    def test_console_hides_legacy_navigation_but_setup_keeps_manual_key_form(self):
        console = self.client.get("/_helper/console")
        self.assertEqual(console.status_code, 200, console.text)
        self.assertIn('href="/_helper/console.css"', console.text)
        css = self.client.get("/_helper/console.css")
        self.assertEqual(css.status_code, 200, css.text)
        compact = "".join(css.text.split())
        self.assertIn(".connection-card", compact)
        self.assertIn(".tabs-nav", compact)
        self.assertIn("display:none!important", compact)
        setup = self.client.get("/_helper/setup")
        self.assertEqual(setup.status_code, 200)
        self.assertIn('id="connection-key"', setup.text)
        self.assertIn('type="password"', setup.text)
        self.assertNotIn('href="/_helper/console.css"', setup.text)
        self.assertEqual(self.proxy.forward_calls, [])

    def test_original_websocket_routes_use_the_proxy_without_helper_credentials(self):
        with self.client.websocket_connect("/api/v1/ws/ops?stream=true") as socket:
            self.assertEqual(socket.receive_json(), {"source": "native-proxy"})
        self.assertEqual(len(self.proxy.websocket_calls), 1)
        _, path, query = self.proxy.websocket_calls[0]
        self.assertEqual(path, "/api/v1/ws/ops")
        self.assertEqual(query, "stream=true")
        self.assertEqual(self.proxy.profile_calls, [])

    def test_proxy_is_closed_during_application_shutdown(self):
        self.client.__exit__(None, None, None)
        self.client = None
        self.assertTrue(self.proxy.closed)
        self.assertTrue(self.upstream.closed)


class NativeConfigurationTests(unittest.TestCase):
    def test_environment_defaults_to_native_ui_while_direct_config_keeps_legacy_compatibility(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(Config.from_env().native_ui)
        with patch.dict(os.environ, {"HELPER_NATIVE_UI": "false"}, clear=True):
            self.assertFalse(Config.from_env().native_ui)
        self.assertFalse(Config(sub2api_url="", sub2api_admin_key="", admin_token="local-test-token").native_ui)

    def test_explicitly_disabled_native_ui_keeps_legacy_index_and_rejects_native_auth(self):
        store = Store(":memory:")
        proxy = FakeNativeProxy()
        config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="server-admin-key",
                        admin_token="local-test-token", native_ui=False, db_path=":memory:")
        app = create_app(config, store=store, upstream=FakeUpstream(), mailer=FakeMailer(), background=False, native_proxy=proxy)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertIn('id="connection-key"', response.text)
            response = client.get("/_helper/api/status", headers=NativeIntegrationTests.auth())
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(client.post("/api/v1/original", content=b"large" * 20000).status_code, 404)
            self.assertEqual(proxy.forward_calls, [])

    def test_unconfigured_native_ui_does_not_send_browser_credentials_anywhere(self):
        store = Store(":memory:")
        proxy = FakeNativeProxy()
        config = Config(sub2api_url="", sub2api_admin_key="", admin_token="local-test-token", native_ui=True, db_path=":memory:")
        app = create_app(config, store=store, upstream=FakeUpstream(), mailer=FakeMailer(), background=False, native_proxy=proxy)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/_helper/api/status", headers=NativeIntegrationTests.auth())
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(proxy.profile_calls, [])
            self.assertEqual(proxy.forward_calls, [])
            self.assertEqual(client.get("/_helper/setup").status_code, 200)


class NativeChunkedBodyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(":memory:")
        self.upstream = FakeUpstream()
        self.proxy = FakeNativeProxy()
        self.config = Config(sub2api_url="https://sub2api.example.test", sub2api_admin_key="server-administrator-key",
                             admin_token="local-chunked-test-token", native_ui=True, db_path=":memory:")
        self.app = create_app(self.config, upstream=self.upstream, store=self.store, mailer=FakeMailer(),
                              background=False, native_proxy=self.proxy)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://helper.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.proxy.close()
        await self.upstream.close()
        self.store.close()

    async def stream_request(self, path, body, observed, chunk_size=4096):
        async def chunks():
            for offset in range(0, len(body), chunk_size):
                chunk = body[offset:offset + chunk_size]
                observed.append(len(chunk))
                yield chunk

        request = self.client.build_request("POST", path, content=chunks(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.config.admin_token,
        })
        self.assertNotIn("content-length", request.headers)
        self.assertEqual(request.headers.get("transfer-encoding"), "chunked")
        return await self.client.send(request)

    async def test_chunked_helper_json_is_rejected_at_limit_without_reading_the_full_body(self):
        payload = b'{"padding":"' + b"x" * 200000 + b'"}'
        for path in ("/api/schedules", "/_helper/api/session"):
            with self.subTest(path=path):
                consumed = []
                response = await self.stream_request(path, payload, consumed)
                self.assertEqual(response.status_code, 413, response.text)
                self.assertGreater(sum(consumed), 65536)
                self.assertLessEqual(sum(consumed), 65536 + 4096)
                self.assertLess(sum(consumed), len(payload), "The body limit must stop reading instead of buffering the entire request")
        self.assertEqual(self.store.list("schedules"), [])
        self.assertEqual(self.proxy.forward_calls, [])
        self.assertEqual(self.proxy.profile_calls, [])

    async def test_valid_chunked_helper_json_is_replayed_for_validation_and_storage(self):
        value = {"name": "跨夜分块计划", "account_ids": [1], "start": "23:00", "end": "09:00",
                 "timezone": "Asia/Shanghai", "weekdays": list(range(7)), "enabled": False}
        payload = json.dumps(value, ensure_ascii=False).encode()
        consumed = []
        response = await self.stream_request("/api/schedules", payload, consumed, chunk_size=7)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(sum(consumed), len(payload))
        saved = self.store.list("schedules")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], value["name"])
        self.assertEqual(saved[0]["account_ids"], [1])
        self.assertEqual(saved[0]["start"], "23:00")
        self.assertEqual(self.proxy.forward_calls, [])

    async def test_large_chunked_original_requests_still_stream_in_full_without_helper_cap(self):
        payload = b'{"events":[{"message":"' + b"x" * 100000 + b'"}]}'
        for path in ("/api/event_logging/batch", "/api/v1/admin/original-import"):
            with self.subTest(path=path):
                consumed = []
                response = await self.stream_request(path, payload, consumed)
                self.assertEqual(response.status_code, 207, response.text[:100])
                self.assertEqual(sum(consumed), len(payload))
                self.assertEqual(response.content, payload)
                forwarded = self.proxy.forward_calls[-1]
                self.assertEqual(forwarded["path"], path)
                self.assertEqual(forwarded["body"], payload)
                self.assertIsNone(forwarded["content_length"])
                self.assertEqual(forwarded["transfer_encoding"], "chunked")
                self.assertEqual(response.headers["content-security-policy"], UPSTREAM_CSP)
                self.assertEqual(response.headers["cache-control"], "public, max-age=300")


if __name__ == "__main__":
    unittest.main()
