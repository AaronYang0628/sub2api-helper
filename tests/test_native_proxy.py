import asyncio
import gzip
import json
import unittest
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit
from unittest.mock import patch

import httpx
from starlette.requests import ClientDisconnect, Request
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocket
from websockets.asyncio.server import serve

from helper.native_proxy import NativeProxy, is_document_request
from helper.upstream import UpstreamError


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks, gate=None):
        self.chunks = chunks
        self.gate = gate
        self.sent = 0
        self.closed = 0
        self.started = asyncio.Event()

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            if index and self.gate:
                await self.gate.wait()
            self.sent += 1
            self.started.set()
            yield chunk

    async def aclose(self):
        self.closed += 1


def request(path="/", method="GET", headers=(), chunks=(), scheme="https", host="panel.example.test"):
    split = urlsplit(path)
    raw_headers = [(b"host", host.encode())] + [(name.lower().encode(), value.encode("latin-1")) for name, value in headers]
    events = [{"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1} for index, chunk in enumerate(chunks)]
    if not events:
        events = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        return events.pop(0) if events else {"type": "http.disconnect"}

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": method, "scheme": scheme, "path": unquote(split.path), "raw_path": split.path.encode(),
             "query_string": split.query.encode(), "headers": raw_headers, "server": (host, 443 if scheme == "https" else 80), "client": ("192.0.2.10", 1234)}
    return Request(scope, receive)


async def body(response):
    if isinstance(response, StreamingResponse):
        return b"".join([chunk async for chunk in response.body_iterator])
    return response.body


class NativeProxyHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()

    def proxy(self, handler, *, base="http://upstream.internal:3000", public=None, **options):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)
        self.clients.append(client)
        return NativeProxy(lambda: base, client=client, public_url_provider=(lambda: public) if public else None)

    async def test_streamed_request_preserves_browser_credentials_without_inheriting_administrator_defaults(self):
        captured = {}

        async def handler(outgoing):
            captured["url"] = str(outgoing.url)
            captured["headers"] = outgoing.headers
            captured["body"] = await outgoing.aread()
            return httpx.Response(200, json={"ok": True})

        proxy = self.proxy(handler, headers={"X-API-Key": "server-admin-secret", "Authorization": "Bearer server-secret"}, cookies={"server": "private"}, auth=("admin", "password"))
        incoming = request("/api/v1/example?key=user-query", "POST", chunks=[b"hello", b"world"], headers=[
            ("Content-Length", "10"), ("Content-Type", "application/json"), ("Authorization", "Bearer browser-jwt"),
            ("Cookie", "browser=session"), ("User-Agent", "Native Browser"), ("Connection", "X-Remove"),
            ("X-Remove", "hop-only"), ("Forwarded", "for=attacker"), ("X-Forwarded-For", "attacker"),
            ("X-Forwarded-Proto", "attacker"), ("X-Real-IP", "attacker"), ("X-Helper-Verified-Admin", "true"),
        ])
        response = await proxy.forward(incoming, "api/v1/example")
        self.assertTrue(incoming.state.helper_proxied)
        self.assertEqual(captured["url"], "http://upstream.internal:3000/api/v1/example?key=user-query")
        self.assertEqual(captured["body"], b"helloworld")
        self.assertEqual(captured["headers"]["host"], "upstream.internal:3000")
        self.assertEqual(captured["headers"]["authorization"], "Bearer browser-jwt")
        self.assertEqual(captured["headers"]["cookie"], "browser=session")
        self.assertEqual(captured["headers"]["user-agent"], "Native Browser")
        self.assertEqual(captured["headers"]["x-forwarded-proto"], "https")
        for name in ("x-api-key", "x-remove", "forwarded", "x-forwarded-for", "x-real-ip", "x-helper-verified-admin"):
            self.assertNotIn(name, captured["headers"])
        self.assertEqual(json.loads(await body(response)), {"ok": True})

    async def test_html_injection_preserves_csp_cookies_cache_and_copies_nonce(self):
        original = b'<html><head><script nonce="nonce123" src="/assets/app.js"></script></head><body>native</body></html>'
        csp = "default-src 'self'; script-src 'nonce-nonce123'; style-src 'self'"
        stream = TrackedStream([original[:40], original[40:]])

        def handler(outgoing):
            self.assertEqual(outgoing.headers["accept-encoding"], "identity")
            self.assertNotIn("if-none-match", outgoing.headers)
            self.assertNotIn("if-modified-since", outgoing.headers)
            return httpx.Response(200, stream=stream, headers=[("content-type", "text/html; charset=utf-8"), ("content-length", str(len(original))),
                ("content-security-policy", csp), ("cache-control", "private, max-age=30"), ("etag", '"original"'),
                ("last-modified", "Tue, 17 Nov 2026 00:00:00 GMT"), ("set-cookie", "one=1; HttpOnly"), ("set-cookie", "two=2; Path=/"),
                ("connection", "X-Upstream-Hop"), ("x-upstream-hop", "remove")])

        response = await self.proxy(handler).forward(request(headers=[("Accept", "text/html"), ("Accept-Encoding", "gzip, br"), ("If-None-Match", '"old-index"'), ("If-Modified-Since", "Tue, 17 Nov 2026 00:00:00 GMT")]), "")
        result = await body(response)
        self.assertIn(b'src="/_helper/native.js"', result)
        self.assertIn(b'href="/_helper/native.css"', result)
        self.assertIn(b'data-sub2api-helper-native="script" nonce="nonce123"', result)
        self.assertIn(b'data-sub2api-helper-native="style" nonce="nonce123"', result)
        self.assertLess(result.index(b"/_helper/native.js"), result.index(b"</head>"))
        self.assertEqual(response.headers["content-security-policy"], csp)
        self.assertEqual(response.headers["cache-control"], "private, max-age=30")
        self.assertEqual(response.headers.getlist("set-cookie"), ["one=1; HttpOnly", "two=2; Path=/"])
        self.assertEqual(int(response.headers["content-length"]), len(result))
        for name in ("etag", "last-modified", "content-encoding", "x-upstream-hop", "connection"):
            self.assertNotIn(name, response.headers)
        self.assertEqual(stream.closed, 1)

    async def test_nonce_can_come_from_existing_csp_and_no_inline_script_is_added(self):
        csp = "script-src 'nonce-header-nonce'; style-src 'self'"
        response = await self.proxy(lambda outgoing: httpx.Response(200, text="<html><head></head></html>", headers={"content-type": "text/html", "content-security-policy": csp})).forward(request(), "")
        result = (await body(response)).decode()
        self.assertIn('nonce="header-nonce"', result)
        self.assertEqual(response.headers["content-security-policy"], csp)
        self.assertIn('src="/_helper/native.js"', result)

    async def test_compressed_html_is_safely_decoded_and_reencoded_without_stale_validators(self):
        compressed = gzip.compress(b"<html><head></head><body>compressed</body></html>")
        stream = TrackedStream([compressed])
        response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "text/html", "content-encoding": "gzip", "content-length": str(len(compressed)), "etag": '"gzip"'})).forward(request(), "")
        rewritten = await body(response)
        self.assertIn(b"compressed", rewritten)
        self.assertIn(b"/_helper/native.js", rewritten)
        self.assertNotIn("content-encoding", response.headers)
        self.assertNotIn("etag", response.headers)
        self.assertEqual(int(response.headers["content-length"]), len(rewritten))

    async def test_cached_mock_gzip_html_does_not_get_double_decoded(self):
        compressed = gzip.compress(b"<html><head></head><body>cached</body></html>")
        response = await self.proxy(lambda outgoing: httpx.Response(200, content=compressed, headers={"content-type": "text/html", "content-encoding": "gzip"})).forward(request(), "")
        self.assertIn(b"/_helper/native.js", await body(response))
        self.assertNotIn("content-encoding", response.headers)

    async def test_non_html_gzip_bytes_and_headers_are_streamed_unchanged(self):
        compressed = gzip.compress(b'{"data":"gateway"}')
        stream = TrackedStream([compressed[:10], compressed[10:]])
        response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "application/json", "content-encoding": "gzip", "content-length": str(len(compressed)), "etag": '"preserve"'})).forward(request("/v1/messages"), "v1/messages")
        self.assertEqual(stream.sent, 0, "A non-HTML response must not be eagerly buffered")
        self.assertEqual(await body(response), compressed)
        self.assertEqual(response.headers["content-encoding"], "gzip")
        self.assertEqual(response.headers["etag"], '"preserve"')
        self.assertEqual(stream.closed, 1)

    async def test_sse_first_event_arrives_before_the_later_event_exists(self):
        gate = asyncio.Event()
        stream = TrackedStream([b"data: first\n\n", b"data: second\n\n"], gate)
        response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream", "cache-control": "no-cache"})).forward(request("/v1/chat/completions"), "v1/chat/completions")
        iterator = response.body_iterator
        self.assertEqual(await asyncio.wait_for(anext(iterator), 1), b"data: first\n\n")
        self.assertEqual(stream.sent, 1)
        next_event = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        self.assertFalse(next_event.done())
        gate.set()
        self.assertEqual(await asyncio.wait_for(next_event, 1), b"data: second\n\n")
        await iterator.aclose()
        self.assertEqual(stream.closed, 1)

    async def test_oversize_html_falls_back_to_raw_stream_without_truncation(self):
        original = b"<html><head></head>" + b"a" * 150 + b"</html>"
        stream = TrackedStream([original[:40], original[40:80], original[80:]])
        with patch("helper.native_proxy.MAX_HTML_BYTES", 64):
            response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "text/html", "etag": '"preserve"'})).forward(request(), "")
            self.assertIsInstance(response, StreamingResponse)
            self.assertEqual(stream.sent, 2)
            self.assertEqual(await body(response), original)
        self.assertEqual(response.headers["etag"], '"preserve"')
        self.assertEqual(stream.closed, 1)

    async def test_decompression_limit_keeps_original_compressed_response(self):
        compressed = gzip.compress(b"<html>" + b"a" * 5000 + b"</html>")
        stream = TrackedStream([compressed])
        with patch("helper.native_proxy.MAX_HTML_BYTES", 100):
            response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "text/html", "content-encoding": "gzip"})).forward(request(), "")
        self.assertEqual(await body(response), compressed)
        self.assertEqual(response.headers["content-encoding"], "gzip")

    async def test_error_and_partial_html_are_not_injected(self):
        for status in (401, 404, 500, 206):
            with self.subTest(status=status):
                original = b"<html><head></head><body>original</body></html>"
                response = await self.proxy(lambda outgoing: httpx.Response(status, stream=TrackedStream([original]), headers={"content-type": "text/html", "etag": '"original"'})).forward(request(), "")
                self.assertEqual(await body(response), original)
                self.assertEqual(response.headers["etag"], '"original"')

    async def test_existing_native_marker_is_not_injected_twice(self):
        html = b'<html><head><script src="/_helper/native.js" data-sub2api-helper-native="script"></script></head></html>'
        response = await self.proxy(lambda outgoing: httpx.Response(200, content=html, headers={"content-type": "text/html"})).forward(request(), "")
        self.assertEqual(await body(response), html)

    async def test_head_response_closes_stream_without_consuming_or_rewriting_body(self):
        stream = TrackedStream([b"must not consume"])
        response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "text/html", "content-length": "99"})).forward(request(method="HEAD"), "")
        self.assertEqual(await body(response), b"")
        self.assertEqual(response.headers["content-length"], "99")
        self.assertEqual(stream.sent, 0)
        self.assertEqual(stream.closed, 1)

    async def test_client_disconnect_closes_upstream_connection(self):
        stream = TrackedStream([b"first", b"second"])
        incoming = request("/v1/events")
        response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})).forward(incoming, "v1/events")

        async def send(event):
            if event["type"] == "http.response.body":
                raise OSError("browser disconnected")

        with self.assertRaises(ClientDisconnect):
            await response(incoming.scope, incoming.receive, send)
        self.assertEqual(stream.closed, 1)

    async def test_cancellation_while_buffering_html_closes_upstream_connection(self):
        stream = TrackedStream([b"<html><head>", b"</head></html>"], asyncio.Event())
        proxy = self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"content-type": "text/html"}))
        task = asyncio.create_task(proxy.forward(request(), ""))
        await asyncio.wait_for(stream.started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(stream.closed, 1)

    async def test_locations_rewrite_only_upstream_origin_and_preserve_oauth_redirect(self):
        for original, expected in (("http://upstream.internal:3000/login?next=%2Fadmin#token", "https://panel.example.test/login?next=%2Fadmin#token"), ("//upstream.internal:3000/admin", "https://panel.example.test/admin"), ("https://oauth.example.test/authorize", "https://oauth.example.test/authorize"), ("/login", "/login")):
            with self.subTest(original=original):
                response = await self.proxy(lambda outgoing: httpx.Response(302, headers={"location": original})).forward(request(), "")
                self.assertEqual(response.headers["location"], expected)

    async def test_configured_public_https_origin_overrides_plain_http_backend_request(self):
        observed = []

        def handler(outgoing):
            observed.append(outgoing.headers)
            return httpx.Response(302, headers={"location": "http://upstream.internal:3000/login"})

        proxy = self.proxy(handler, public="https://admin.example.test")
        response = await proxy.forward(request(scheme="http", host="private-helper:8080", headers=[("X-Forwarded-Proto", "attacker")]), "")
        self.assertEqual(response.headers["location"], "https://admin.example.test/login")
        self.assertEqual(observed[0]["x-forwarded-proto"], "https")

    async def test_international_public_hostname_is_idna_encoded_in_location(self):
        stream = TrackedStream([b"redirect body"])
        response = await self.proxy(lambda outgoing: httpx.Response(302, stream=stream, headers={"location": "http://upstream.internal:3000/login"}), public="https://助手.example").forward(request(scheme="http"), "")
        self.assertEqual(response.headers["location"], str(httpx.URL("https://助手.example/login")))
        self.assertEqual(await body(response), b"redirect body")
        self.assertEqual(stream.closed, 1)

    async def test_xhtml_injection_remains_well_formed_xml(self):
        original = b'<html xmlns="http://www.w3.org/1999/xhtml"><head></head><body>native</body></html>'
        response = await self.proxy(lambda outgoing: httpx.Response(200, content=original, headers={"content-type": "application/xhtml+xml"})).forward(request(), "")
        injected = await body(response)
        ET.fromstring(injected)
        self.assertIn(b"/_helper/native.js", injected)

    async def test_insertion_ignores_head_end_tag_inside_script_text_or_comment(self):
        original = b'<html><head><!-- </head> --><script nonce="abc">window.title="</head>";</script></head><body>native</body></html>'
        response = await self.proxy(lambda outgoing: httpx.Response(200, content=original, headers={"content-type": "text/html"})).forward(request(), "")
        injected = await body(response)
        self.assertIn(b'<!-- </head> -->', injected)
        self.assertIn(b'window.title="</head>";</script>', injected)
        self.assertLess(injected.index(b"</script>"), injected.index(b"/_helper/native.js"))
        self.assertLess(injected.index(b"/_helper/native.js"), injected.index(b"</head><body>"))

    async def test_http_redirects_are_never_followed_even_with_permissive_injected_client(self):
        requests = []

        def handler(outgoing):
            requests.append(outgoing)
            return httpx.Response(307, headers={"location": "https://external.example.test"})

        response = await self.proxy(handler, follow_redirects=True).forward(request(headers=[("Authorization", "Bearer browser-jwt")]), "")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(len(requests), 1)

    async def test_profile_sends_only_user_credentials_and_uses_finite_read_timeout(self):
        seen = []

        def handler(outgoing):
            seen.append(outgoing)
            return httpx.Response(200, json={"code": 0, "data": {"id": 99, "role": "admin", "status": "active"}})

        proxy = self.proxy(handler, headers={"X-API-Key": "server-admin-key", "Authorization": "Bearer unwanted"}, cookies={"admin": "server-cookie"}, auth=("root", "secret"))
        profile = await proxy.profile("native-jwt", "Browser UA")
        self.assertEqual(profile["id"], 99)
        self.assertEqual(seen[0].url.path, "/api/v1/user/profile")
        self.assertEqual(set(seen[0].headers), {"host", "authorization", "user-agent", "accept"})
        self.assertEqual(seen[0].headers["authorization"], "Bearer native-jwt")
        self.assertEqual(seen[0].headers["user-agent"], "Browser UA")
        self.assertEqual(seen[0].extensions["timeout"]["read"], 20)

    async def test_profile_redirect_failure_and_incompatible_payload_fail_safely(self):
        for status, payload in ((302, {}), (401, {}), (200, {"code": True, "data": {}}), (200, {"code": 0, "data": []})):
            with self.subTest(status=status, payload=payload):
                proxy = self.proxy(lambda outgoing: httpx.Response(status, json=payload, headers={"location": "https://external.example.test"}), follow_redirects=True)
                with self.assertRaises(UpstreamError) as error:
                    await proxy.profile("sensitive-browser-jwt", "Browser")
                self.assertNotIn("sensitive-browser-jwt", str(error.exception))

    async def test_transport_errors_and_invalid_provider_never_echo_credentials(self):
        def handler(outgoing):
            raise httpx.ConnectError("sensitive-browser-jwt upstream unavailable", request=outgoing)
        response = await self.proxy(handler).forward(request(), "")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b"sensitive-browser-jwt", await body(response))
        seen = []
        response = await self.proxy(lambda outgoing: seen.append(outgoing), base="https://user:server-secret@bad.example.test").forward(request(), "")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(b"server-secret", await body(response))
        self.assertEqual(seen, [])

    async def test_document_predicate_accepts_spa_navigation_across_native_routes(self):
        for path in ("/", "/index.html", "/home", "/login", "/register", "/setup", "/auth/oidc/callback?code=opaque",
                     "/auth/linuxdo/callback", "/dashboard", "/usage", "/admin", "/admin/users", "/admin/accounts",
                     "/admin/ops", "/admin/groups", "/admin/settings", "/settings/mcp", "/legal/terms", "/custom/12"):
            with self.subTest(path=path):
                incoming = request(path, headers=[("Accept", "text/html,application/xhtml+xml;q=0.9"), ("Sec-Fetch-Dest", "document")])
                self.assertTrue(is_document_request(incoming, incoming.scope["path"].lstrip("/")))
        self.assertTrue(is_document_request(request("/login", headers=[("Accept", "application/xhtml+xml"), ("Sec-Fetch-Dest", "iframe")]), "login"))
        self.assertTrue(is_document_request(request("/login", headers=[("Accept", "text/html")]), "login"))

    async def test_document_predicate_excludes_all_audited_gateway_and_static_namespaces(self):
        paths = ("/api", "/api/v1/user/profile", "/v1", "/v1/models", "/v1beta/models", "/backend-api/codex/responses",
                 "/antigravity/models", "/responses", "/responses/compact", "/models", "/models/gpt", "/images", "/images/tasks/task",
                 "/videos/request/content", "/tts", "/stt", "/custom-voices/voice/audio", "/realtime", "/chat/completions",
                 "/messages/count_tokens", "/embeddings", "/alpha/search", "/web_search", "/x_search", "/contents/generations/tasks",
                 "/v3/contents/generations/tasks", "/setup/api/config", "/.well-known/oauth-authorization-server", "/health", "/healthz",
                 "/readyz", "/metrics", "/_helper/native.js", "/assets/index.js", "/assets/no-extension", "/static/a", "/logo.svg",
                 "/favicon.ico", "/robots.txt", "/manifest.webmanifest", "/style.css/", "/nested/script.js", "/openapi.json")
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(is_document_request(request(path, headers=[("Accept", "text/html")]), path.lstrip("/")))

    async def test_document_predicate_requires_real_html_accept_and_navigation_destination(self):
        for accept in ("*/*", "application/json", "text/htmlx", "text/html;q=0", "text/html;q=bad", "text/html;q=nan", "text/html;q=2"):
            self.assertFalse(is_document_request(request(headers=[("Accept", accept)]), ""), accept)
        for accept in ("text/html", "text/html; q=0.5", "application/json,text/html;q=0.2", "application/xhtml+xml"):
            self.assertTrue(is_document_request(request(headers=[("Accept", accept)]), ""), accept)
        self.assertFalse(is_document_request(request(), ""))
        for destination in ("empty", "script", "style", "image", "font", "worker", ""):
            self.assertFalse(is_document_request(request(headers=[("Accept", "text/html"), ("Sec-Fetch-Dest", destination)]), ""))
        for method in ("POST", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"):
            self.assertFalse(is_document_request(request(method=method, headers=[("Accept", "text/html")]), ""))
        self.assertFalse(is_document_request(request(headers=[("Accept", "text/html"), ("Upgrade", "websocket")]), ""))

    async def test_document_guard_rejects_ambiguous_paths_and_never_dispatches_non_documents(self):
        seen = []
        proxy = self.proxy(lambda outgoing: seen.append(outgoing))
        for path in ("/api/v1/admin/users", "/v1/responses", "/assets/index.js", "/a/../admin/users", "/admin//users", "/%252fapi/v1/users"):
            incoming = request(path, headers=[("Accept", "text/html")])
            response = await proxy.forward_document(incoming, incoming.scope["path"].lstrip("/"))
            self.assertEqual(response.status_code, 404)
            self.assertFalse(getattr(incoming.state, "helper_proxied", False))
        response = await proxy.forward_document(request("/api/users", headers=[("Accept", "text/html")]), "admin/users")
        self.assertEqual(response.status_code, 404)
        response = await proxy.forward_document(request("/admin/users", "POST", headers=[("Accept", "text/html")]), "admin/users")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(seen, [])

    async def test_document_injection_requires_real_spa_mount_and_asset_module(self):
        shell = b'<html><head><script nonce="upstream-nonce" type="module" src="/assets/index-hash.js"></script></head><body><div id="app"></div></body></html>'
        csp = "default-src 'self'; script-src 'nonce-upstream-nonce'"
        observed = []

        def handler(outgoing):
            observed.append(outgoing)
            return httpx.Response(200, stream=TrackedStream([shell]), headers=[("Content-Type", "text/html"), ("Content-Security-Policy", csp),
                ("Set-Cookie", "one=1; HttpOnly"), ("Set-Cookie", "two=2; Secure"), ("ETag", '"original"')])

        response = await self.proxy(handler).forward_document(request("/admin/ops", headers=[("Accept", "text/html"), ("Sec-Fetch-Dest", "document")]), "admin/ops")
        result = await body(response)
        self.assertIn(b'data-sub2api-helper-native="script" nonce="upstream-nonce"', result)
        self.assertIn(b'data-sub2api-helper-native="style" nonce="upstream-nonce"', result)
        self.assertEqual(response.headers["content-security-policy"], csp)
        self.assertEqual(response.headers.getlist("set-cookie"), ["one=1; HttpOnly", "two=2; Secure"])
        self.assertNotIn("etag", response.headers)
        self.assertEqual(observed[0].url.path, "/admin/ops")
        self.assertEqual(observed[0].headers["accept-encoding"], "identity")

    async def test_document_does_not_inject_lookalikes_comments_scripts_or_unrelated_html(self):
        asset = '<script type="module" src="/assets/index.js"></script>'
        candidates = ("<h1>Maintenance</h1>", '<div id="app"></div>', asset,
                      '<!-- <div id="app"></div>' + asset + ' -->',
                      '<script>window.fake=\'<div id="app"></div><script type="module" src="/assets/index.js">\';</script>',
                      '<template><div id="app"></div>' + asset + '</template>',
                      '<textarea><div id="app"></div>' + asset + '</textarea>',
                      '<div id="app"></div><script>window.__APP_CONFIG__={};</script>',
                      '<div id="app"></div><script src="/assets/index.js"></script>',
                      '<div id="app"></div><script type="module" src="https://external.example/assets/index.js"></script>',
                      '<div id="app"></div><script type="module" src="/assets/../api/index.js"></script>',
                      '<div id="app"></div><script type src="/assets/index.js"></script>',
                      '<template/><div id="app"></div>' + asset,
                      '<select><div id="app"></div></select>' + asset,
                      '<select><body><div id="app"></div>' + asset + '</body></select>',
                      '<div id="other" id="app"></div>' + asset,
                      '<div id="app"></div><script type="text/plain" type="module" src="/assets/index.js"></script>')
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                original = ("<html><head></head><body>" + candidate + "</body></html>").encode()
                response = await self.proxy(lambda outgoing: httpx.Response(200, stream=TrackedStream([original]), headers={"Content-Type": "text/html", "ETag": '"untouched"'})).forward_document(request(headers=[("Accept", "text/html")]), "")
                self.assertEqual(await body(response), original)
                self.assertEqual(response.headers["etag"], '"untouched"')

    async def test_document_xhtml_is_preserved_without_assuming_html_case_rules(self):
        for marker in (b'id="app"', b'ID="app"'):
            original = b'<html xmlns="http://www.w3.org/1999/xhtml"><head><script type="module" src="/assets/main.js"/></head><body><div ' + marker + b'/></body></html>'
            response = await self.proxy(lambda outgoing: httpx.Response(200, stream=TrackedStream([original]), headers={"Content-Type": "application/xhtml+xml"})).forward_document(request(headers=[("Accept", "application/xhtml+xml")]), "")
            self.assertEqual(await body(response), original)

    async def test_document_duplicate_html_nonce_matches_browsers_first_value(self):
        original = b'<html><head><script type="module" src="/assets/main.js" nonce="first" nonce="last"></script></head><body><div id="app"></div></body></html>'
        response = await self.proxy(lambda outgoing: httpx.Response(200, stream=TrackedStream([original]), headers={"Content-Type": "text/html"})).forward_document(request(headers=[("Accept", "text/html")]), "")
        result = await body(response)
        self.assertIn(b'data-sub2api-helper-native="script" nonce="first"', result)
        self.assertNotIn(b'data-sub2api-helper-native="script" nonce="last"', result)

    async def test_document_timeout_covers_headers_and_body_and_closes_upstream(self):
        async def slow_headers(outgoing):
            await asyncio.Event().wait()

        with patch("helper.native_proxy.DOCUMENT_TIMEOUT", .02):
            response = await self.proxy(slow_headers).forward_document(request(headers=[("Accept", "text/html")]), "")
        self.assertEqual(response.status_code, 504)
        stream = TrackedStream([b"<html>", b"never"], asyncio.Event())
        with patch("helper.native_proxy.DOCUMENT_TIMEOUT", .02):
            response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"Content-Type": "text/html"})).forward_document(request(headers=[("Accept", "text/html")]), "")
        self.assertEqual(response.status_code, 504)
        self.assertEqual(stream.closed, 1)

    async def test_document_body_limit_fails_before_returning_streaming_headers(self):
        stream = TrackedStream([b"<html>", b"x" * 100])
        with patch("helper.native_proxy.MAX_HTML_BYTES", 32):
            response = await self.proxy(lambda outgoing: httpx.Response(200, stream=stream, headers={"Content-Type": "text/html"})).forward_document(request(headers=[("Accept", "text/html")]), "")
        self.assertEqual(response.status_code, 502)
        self.assertNotIsInstance(response, StreamingResponse)
        self.assertEqual(stream.closed, 1)

    async def test_document_non_html_and_error_responses_preserve_original_bytes(self):
        for status, content_type, original in ((200, "application/json", b'{"message":"not SPA"}'),
                                               (503, "text/html", b'<div id="app"></div><script type="module" src="/assets/index.js"></script>')):
            stream = TrackedStream([original])
            response = await self.proxy(lambda outgoing: httpx.Response(status, stream=stream, headers={"Content-Type": content_type, "ETag": '"original"'})).forward_document(request("/login", headers=[("Accept", "text/html")]), "login")
            self.assertEqual(response.status_code, status)
            self.assertEqual(await body(response), original)
            self.assertEqual(response.headers["etag"], '"original"')
            self.assertEqual(stream.closed, 1)


class NativeProxyWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def websocket(self, proxy, *, origin="https://panel.example.test", scheme="wss", host="panel.example.test", protocols=("native-admin",)):
        incoming, sent = asyncio.Queue(), asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        headers = [(b"host", host.encode()), (b"user-agent", b"Native Browser"), (b"authorization", b"Bearer browser-jwt"),
                   (b"cookie", b"browser=session"), (b"x-forwarded-for", b"spoof"), (b"x-forwarded-proto", b"spoof"),
                   (b"x-helper-admin", b"true"), (b"sec-websocket-key", b"browser-key")]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        scope = {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"}, "scheme": scheme,
                 "path": "/ops", "raw_path": b"/ops", "query_string": b"", "headers": headers,
                 "server": (host, 443), "client": ("192.0.2.10", 1234), "subprotocols": list(protocols)}
        socket = WebSocket(scope, incoming.get, sent.put)
        task = asyncio.create_task(proxy.websocket(socket, "ops"))
        return incoming, sent, task

    async def test_text_binary_subprotocol_credentials_and_safe_origin_rewrite(self):
        observed = {}

        async def echo(connection):
            observed["headers"] = connection.request.headers
            async for item in connection:
                await connection.send(item)

        async with serve(echo, "127.0.0.1", 0, subprotocols=["native-admin"]) as server:
            port = server.sockets[0].getsockname()[1]
            proxy = NativeProxy(lambda: f"http://127.0.0.1:{port}")
            try:
                incoming, sent, task = await self.websocket(proxy)
                accepted = await asyncio.wait_for(sent.get(), 2)
                self.assertEqual(accepted["type"], "websocket.accept")
                self.assertEqual(accepted["subprotocol"], "native-admin")
                await incoming.put({"type": "websocket.receive", "text": "hello"})
                self.assertEqual(await asyncio.wait_for(sent.get(), 2), {"type": "websocket.send", "text": "hello"})
                await incoming.put({"type": "websocket.receive", "bytes": b"\x00\x01"})
                self.assertEqual(await asyncio.wait_for(sent.get(), 2), {"type": "websocket.send", "bytes": b"\x00\x01"})
                await incoming.put({"type": "websocket.disconnect", "code": 1000})
                await asyncio.wait_for(task, 2)
                headers = observed["headers"]
                self.assertEqual(headers["Origin"], f"http://127.0.0.1:{port}")
                self.assertEqual(headers["User-Agent"], "Native Browser")
                self.assertEqual(headers["Authorization"], "Bearer browser-jwt")
                self.assertEqual(headers["Cookie"], "browser=session")
                self.assertEqual(headers["X-Forwarded-Proto"], "https")
                self.assertNotIn("X-Forwarded-For", headers)
                self.assertNotIn("X-Helper-Admin", headers)
                self.assertNotEqual(headers["Sec-WebSocket-Key"], "browser-key")
            finally:
                await proxy.close()

    async def test_configured_https_origin_works_behind_plain_ws_connection(self):
        observed = {}

        async def echo(connection):
            observed["proto"] = connection.request.headers["X-Forwarded-Proto"]
            await connection.close()

        async with serve(echo, "127.0.0.1", 0, subprotocols=["native-admin"]) as server:
            port = server.sockets[0].getsockname()[1]
            proxy = NativeProxy(lambda: f"http://127.0.0.1:{port}", public_url_provider=lambda: "https://panel.example.test")
            try:
                incoming, sent, task = await self.websocket(proxy, scheme="ws", host="private-helper:8080")
                self.assertEqual((await asyncio.wait_for(sent.get(), 2))["type"], "websocket.accept")
                await asyncio.wait_for(task, 2)
                self.assertEqual(observed["proto"], "https")
            finally:
                await proxy.close()

    async def test_cross_origin_is_rejected_before_opening_upstream_connection(self):
        hits = []

        async def echo(connection):
            hits.append(True)

        async with serve(echo, "127.0.0.1", 0, subprotocols=["native-admin"]) as server:
            port = server.sockets[0].getsockname()[1]
            proxy = NativeProxy(lambda: f"http://127.0.0.1:{port}")
            try:
                incoming, sent, task = await self.websocket(proxy, origin="https://attacker.example.test")
                self.assertEqual(await asyncio.wait_for(sent.get(), 2), {"type": "websocket.close", "code": 1008, "reason": ""})
                await asyncio.wait_for(task, 2)
                self.assertEqual(hits, [])
            finally:
                await proxy.close()

    async def test_websocket_redirects_are_not_followed(self):
        redirected = []

        async def destination(connection):
            redirected.append(True)

        async with serve(destination, "127.0.0.1", 0, subprotocols=["native-admin"]) as second:
            port2 = second.sockets[0].getsockname()[1]

            async def redirect(connection, incoming):
                response = connection.respond(307, "redirect")
                response.headers["Location"] = f"ws://127.0.0.1:{port2}/ops"
                return response

            async with serve(destination, "127.0.0.1", 0, process_request=redirect) as first:
                port = first.sockets[0].getsockname()[1]
                proxy = NativeProxy(lambda: f"http://127.0.0.1:{port}")
                try:
                    incoming, sent, task = await self.websocket(proxy)
                    self.assertEqual((await asyncio.wait_for(sent.get(), 2))["type"], "websocket.close")
                    await asyncio.wait_for(task, 2)
                    self.assertEqual(redirected, [])
                finally:
                    await proxy.close()


if __name__ == "__main__":
    unittest.main()
