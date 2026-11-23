"""Static artwork preservation and network-free model generation contracts."""

import asyncio
import base64
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from helper.config import Config
from helper.gallery_generation import (
    ARTWORK_CSP, GenerationFailure, GalleryGenerationError, MAX_HTML_BYTES, MAX_RESPONSE_BYTES,
    MAX_TEXT_BYTES, REASONING_EFFORTS, UnsupportedReasoningEffort, generate_artwork, sanitize_artwork,
)


GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
GIF_URI = "data:image/gif;base64," + base64.b64encode(GIF).decode()


class SanitizerTests(unittest.TestCase):
    def test_svg_shapes_gradients_and_animations_keep_canonical_attributes(self):
        source = '''<svg viewBox="0 0 200 100" xmlns="http://www.w3.org/2000/svg">
        <defs><linearGradient id="sun" gradientTransform="rotate(30)"><stop offset="0%" stop-color="#ff0"/><stop offset="100%" stop-color="orange"/></linearGradient></defs>
        <circle cx="50" cy="50" r="20" fill="url(#sun)"><animate attributeName="r" values="20;25;20" dur="3s" repeatCount="indefinite"/></circle>
        <g><animateTransform attributeName="transform" type="rotate" from="0 50 50" to="360 50 50" dur="5s" repeatCount="indefinite"/><path d="M0 0L20 20"/></g></svg>'''
        content = sanitize_artwork(source)
        for expected in ('viewBox="0 0 200 100"', '<linearGradient', 'gradientTransform="rotate(30)"', 'fill="url(#sun)"', '<animate attributeName="r"', 'values="20;25;20"', 'repeatCount="indefinite"', '<animateTransform', '<path d="M0 0L20 20"'):
            self.assertIn(expected, content)

    def test_document_fence_css_animation_and_html_layout_survive(self):
        source = '''Here is the artwork:
```html
<!doctype html><html><head><style>
@import "https://evil.test/font.css";
.sky {background:linear-gradient(#123,#456); display:grid; --sun:#fc0}
@keyframes float {from {transform:translateY(0)} 50%,to {transform:translateY(-10px)}}
@media (max-width:600px) {.sky {width:100%}}
.sun {animation:float 3s ease-in-out infinite; border-radius:50%; box-shadow:0 0 20px gold}
</style></head><body><main class="sky"><h1>星光 &amp; 海洋</h1><div class="sun" style="width:40px;height:40px;background:var(--sun)"></div></main></body></html>
```
'''
        content = sanitize_artwork(source)
        self.assertIn("@keyframes float", content)
        self.assertIn("@media (max-width:600px)", content)
        self.assertIn("linear-gradient(#123,#456)", content)
        self.assertIn('class="sun"', content)
        self.assertIn("星光 &amp; 海洋", content)
        self.assertNotIn("@import", content)
        self.assertNotIn("evil.test", content)
        self.assertNotIn("<html", content)
        self.assertNotIn("<body", content)

    def test_script_events_external_elements_and_forms_are_removed(self):
        source = '''<div onclick="alert(1)">safe<script>alert('BAD')</script>
        <iframe src="https://evil.test">BAD</iframe><object data="https://evil.test">BAD</object>
        <form action="https://evil.test"><input value="BAD"><button>BAD</button></form>
        <meta http-equiv="refresh" content="0;url=https://evil.test"><base href="https://evil.test">
        <link rel="stylesheet" href="https://evil.test"><a href="javascript:alert(1)">BAD</a>
        <template><img src="https://evil.test"></template><noscript>BAD</noscript>
        <img src="https://evil.test/x.png" srcset="https://evil.test/y.png 2x" onerror="alert(1)" alt="photo">
        <svg onload="alert(1)"><foreignObject><div>BAD</div></foreignObject><script>BAD</script><rect width="10"/></svg></div>'''
        content = sanitize_artwork(source)
        self.assertIn("safe", content)
        self.assertIn('<rect width="10">', content)
        for forbidden in ("BAD", "alert", "evil.test", "onclick", "onload", "onerror", "foreignObject", "srcset", "<script", "<form", "<iframe", "<object", "<base", "<link", "<meta"):
            self.assertNotIn(forbidden, content)

    def test_css_fetching_functions_escapes_bindings_and_script_are_dropped(self):
        source = r'''<div style="background:url(https://evil.test/a);color:orange;behavior:url(#bad);filter:expression(alert(1));background-image:image-set('https://evil.test' 1x);--image:url(https://evil.test)">hi</div>
        <style>.ok{color:red;background:u/**/rl(https://evil.test)} @font-face{font-family:x;src:url(https://evil.test)} .no{background:\75rl(https://evil.test)}</style>'''
        content = sanitize_artwork(source)
        self.assertIn("color:orange", content)
        for forbidden in ("evil.test", "behavior", "expression", "image-set", "@font-face", "\\75"):
            self.assertNotIn(forbidden, content)

    def test_svg_only_local_references_and_safe_animation_targets(self):
        source = '''<svg xml:base="https://evil.test/"><defs><path id="p" d="M0 0L1 1"/></defs>
        <use href="#p"/><use href="https://evil.test/x.svg#p"/><use xlink:href="&#106;avascript:alert(1)"/>
        <animate attributeName="href" values="https://evil.test"/>
        <set attributeName="onload" to="alert(1)"/>
        <animate attributeName="fill" values="red;url(https://evil.test)" begin="click"/>
        <animate attributeName="opacity" from="0" to="1" begin="0s" dur="2s"/>
        </svg>'''
        content = sanitize_artwork(source)
        self.assertIn('href="#p"', content)
        self.assertIn('attributeName="opacity"', content)
        self.assertIn('begin="0s"', content)
        for forbidden in ("xml:base", "evil.test", "javascript", "alert", 'attributeName="href"', 'attributeName="onload"', 'begin="click"'):
            self.assertNotIn(forbidden, content)

    def test_inline_gif_is_validated_and_animation_bytes_preserved(self):
        content = sanitize_artwork('<div><img src="' + GIF_URI + '"></div>')
        self.assertIn(GIF_URI, content)
        malicious = sanitize_artwork('<div><img src="data:image/gif;base64,PHNjcmlwdD4="/><img src="data:image/svg+xml;base64,PHN2Zz4="/></div>')
        self.assertNotIn("src=", malicious)

    def test_entities_are_text_and_attributes_remain_escaped(self):
        content = sanitize_artwork('<div title="&quot; onmouseover=&quot;alert(1)">&lt;script&gt;&amp;</div>')
        self.assertIn('title="&quot; onmouseover=&quot;alert(1)"', content)
        self.assertIn("&lt;script&gt;&amp;", content)
        self.assertNotIn('<script>', content)

    def test_unknown_namespaces_and_malformed_nesting_cannot_emit_scripts(self):
        content = sanitize_artwork('<div>before<math><annotation-xml encoding="text/html"><script>BAD</script></annotation-xml></math><svg><g><script>BAD</script></svg>after</div>')
        self.assertIn("before", content)
        self.assertIn("after", content)
        self.assertNotIn("BAD", content)
        self.assertTrue(content.endswith("</div>"))

    def test_plain_text_is_not_changed_or_rendered_as_markup(self):
        self.assertIsNone(sanitize_artwork("A < B and nothing else."))
        self.assertIsNone(sanitize_artwork("```python\nprint('hello')\n```"))

    def test_html_bound_and_deep_nesting_fail_closed(self):
        with self.assertRaises(ValueError):
            sanitize_artwork("<div>" + "x" * MAX_HTML_BYTES + "</div>")
        with self.assertRaises(ValueError):
            sanitize_artwork("<div>" * 129)

    def test_policy_has_independent_execution_and_network_boundaries(self):
        for directive in ("default-src 'none'", "img-src data:", "script-src 'none'", "connect-src 'none'", "form-action 'none'", "frame-src 'none'", "base-uri 'none'"):
            self.assertIn(directive, ARTWORK_CSP)


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks, self.closed, self.consumed = chunks, False, 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self):
        self.closed = True


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = Config(sub2api_url="https://sub2api.test", sub2api_admin_key="admin-secret", admin_token="helper-secret")
        self.request = SimpleNamespace(model="art-model", api_format="chat", api_key="ordinary-key", prompt="画一幅静态 SVG 海景。")

    async def run_with(self, handler, **client_options):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_options) as client:
            return await generate_artwork(self.config, self.request, client)

    async def test_chat_generation_keeps_raw_svg_and_uses_only_ordinary_key(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(str(request.url), "https://sub2api.test/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer ordinary-key")
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            for forbidden in ("x-api-key", "cookie"):
                self.assertNotIn(forbidden, request.headers)
            body = json.loads(request.content)
            self.assertGreaterEqual(body["max_tokens"], 6000)
            self.assertEqual(body["messages"][0]["content"], self.request.prompt)
            self.assertFalse(body["stream"])
            return httpx.Response(200, json={"choices": [{"message": {"content": '<svg viewBox="0 0 10 10"><text>https://example.test/a.png</text><circle r="2"/></svg>'}}]})

        result = await self.run_with(handler, headers={"x-api-key": "admin-secret"}, cookies={"admin": "helper-secret"}, auth=("admin", "secret"))
        self.assertEqual(result["kind"], "html")
        self.assertIn("https://example.test/a.png", result["content"])
        self.assertIn("<circle", result["content"])
        self.assertIsInstance(result["latency_ms"], int)
        self.assertEqual(len(requests), 1)

    async def test_responses_generation_parses_text_parts_and_keeps_css(self):
        self.request.api_format = "responses"

        def handler(request):
            self.assertEqual(request.url.path, "/v1/responses")
            body = json.loads(request.content)
            self.assertEqual(body["input"], self.request.prompt)
            self.assertGreaterEqual(body["max_output_tokens"], 6000)
            return httpx.Response(200, json={"output": [{"type": "reasoning", "summary": []}, {"type": "message", "content": [{"type": "output_text", "text": '<div style="color:gold">海</div>'}]}]})

        result = await self.run_with(handler)
        self.assertEqual(result["content"], '<div style="color:gold">海</div>')

    async def test_missing_and_default_effort_preserve_original_request_shape(self):
        for api_format in ("chat", "responses"):
            for explicit_default in (False, True):
                self.request.api_format = api_format
                if explicit_default:
                    self.request.reasoning_effort = "default"
                elif hasattr(self.request, "reasoning_effort"):
                    del self.request.reasoning_effort

                def handler(request):
                    payload = json.loads(request.content)
                    self.assertNotIn("reasoning_effort", payload)
                    self.assertNotIn("reasoning", payload)
                    self.assertNotIn("thinking", payload)
                    return httpx.Response(200, json={"choices": [{"message": {"content": "完成"}}]} if api_format == "chat" else {"output": [{"type": "message", "content": [{"type": "output_text", "text": "完成"}]}]})

                await self.run_with(handler)

    async def test_effort_uses_exact_protocol_field_without_provider_or_model_rewrites(self):
        for api_format in ("chat", "responses"):
            for effort in sorted(REASONING_EFFORTS - {"default"}):
                self.request.api_format = api_format
                self.request.reasoning_effort = effort
                self.request.model = "provider-alias/glm-5.3"

                def handler(request):
                    payload = json.loads(request.content)
                    self.assertEqual(payload["model"], self.request.model)
                    self.assertNotIn("thinking", payload)
                    self.assertNotIn("output_config", payload)
                    if api_format == "chat":
                        self.assertEqual(payload["reasoning_effort"], effort)
                        self.assertNotIn("reasoning", payload)
                        return httpx.Response(200, json={"choices": [{"message": {"content": "完成"}}]})
                    self.assertEqual(payload["reasoning"], {"effort": effort})
                    self.assertNotIn("reasoning_effort", payload)
                    return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "完成"}]}]})

                await self.run_with(handler)

    async def test_invalid_effort_is_rejected_before_network(self):
        calls = []
        for invalid in (None, True, [], {}, "", "HIGH", "ultra", "none\nsecret"):
            self.request.reasoning_effort = invalid
            with self.assertRaises(GalleryGenerationError):
                await self.run_with(lambda request: calls.append(request))
        self.assertEqual(calls, [])

    async def test_rejected_explicit_effort_is_safe_closed_and_never_retried(self):
        self.request.reasoning_effort = "xhigh"
        for status in (400, 422):
            calls = []
            stream = Stream([b'{"error":"ordinary-key upstream secret details"}'])

            def handler(request):
                calls.append(request)
                return httpx.Response(status, stream=stream, headers={"Content-Type": "application/json"})

            with self.assertRaises(UnsupportedReasoningEffort) as rejected:
                await self.run_with(handler)
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(calls[0].content)["reasoning_effort"], "xhigh")
            self.assertIn("不会自动重试", str(rejected.exception))
            self.assertNotIn("ordinary-key", str(rejected.exception))
            self.assertNotIn("secret", str(rejected.exception))
            self.assertEqual(stream.consumed, 0)
            self.assertTrue(stream.closed)

    async def test_default_rejection_and_permission_failure_do_not_claim_effort_error(self):
        for effort, status in (("default", 400), ("high", 403), ("high", 500)):
            self.request.reasoning_effort = effort
            with self.assertRaises(GalleryGenerationError) as failed:
                await self.run_with(lambda request: httpx.Response(status, json={"error": "upstream secret"}))
            self.assertNotIsInstance(failed.exception, UnsupportedReasoningEffort)
            self.assertNotIn("upstream secret", str(failed.exception))

    async def test_safe_failure_codes_distinguish_http_response_and_artwork_errors(self):
        cases = [
            (httpx.Response(503, text="ordinary-key secret no accounts"), "unavailable"),
            (httpx.Response(401, text="ordinary-key secret"), "authentication"),
            (httpx.Response(403, text="ordinary-key secret"), "authentication"),
            (httpx.Response(429, text="ordinary-key secret"), "rate_limited"),
            (httpx.Response(400, text="ordinary-key secret"), "request_rejected"),
            (httpx.Response(502, text="ordinary-key secret"), "gateway"),
            (httpx.Response(200, text="ordinary-key secret invalid JSON"), "response_format"),
            (httpx.Response(200, json={"output": []}), "response_format"),
            (httpx.Response(200, json={"choices": [{"message": {}}]}), "response_format"),
            (httpx.Response(200, json={"choices": [{"message": {"content": None, "reasoning_content": "secret thinking"}, "finish_reason": "length"}]}), "empty_output"),
            (httpx.Response(200, json={"choices": [{"message": {"content": "<div>" + "x" * MAX_HTML_BYTES + "</div>"}}]}), "artwork_invalid"),
            (httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)), "response_too_large"),
        ]
        for response, code in cases:
            calls = []

            def handler(request):
                calls.append(request)
                return response

            with self.subTest(code=code), self.assertRaises(GenerationFailure) as failed:
                await self.run_with(handler)
            self.assertEqual(failed.exception.code, code)
            self.assertEqual(len(calls), 1)
            for forbidden in ("ordinary-key", "secret", "sub2api.test", self.request.prompt):
                self.assertNotIn(forbidden, str(failed.exception))
            self.assertTrue(response.is_closed)

    async def test_invalid_request_configuration_never_claims_a_gateway_response(self):
        self.request.api_key = "模型密钥"
        calls = []
        with self.assertRaises(GenerationFailure) as failed:
            await self.run_with(lambda request: calls.append(request))
        self.assertEqual(failed.exception.code, "request_invalid")
        self.assertIn("本次未调用模型", str(failed.exception))
        self.assertNotIn(self.request.api_key, str(failed.exception))
        self.assertEqual(calls, [])

    async def test_connection_failure_has_distinct_safe_code(self):
        def handler(request):
            raise httpx.ConnectError("ordinary-key admin-secret", request=request)

        with self.assertRaises(GenerationFailure) as failed:
            await self.run_with(handler)
        self.assertEqual(failed.exception.code, "connection")
        self.assertNotIn("admin-secret", str(failed.exception))

    async def test_configured_deadline_applies_to_total_and_read_wait_without_retry(self):
        self.config = replace(self.config, gallery_timeout_seconds=600)
        calls, total_limits = [], []
        original_timeout = asyncio.timeout

        def short_deadline(seconds):
            total_limits.append(seconds)
            return original_timeout(.01)

        async def handler(request):
            calls.append(request)
            self.assertEqual(request.extensions["timeout"]["read"], 600)
            await asyncio.sleep(1)
            return httpx.Response(200, json={"choices": [{"message": {"content": "never reached"}}]})

        with patch("helper.gallery_generation.asyncio.timeout", side_effect=short_deadline):
            with self.assertRaises(GenerationFailure) as failed:
                await self.run_with(handler)
        self.assertEqual(failed.exception.code, "timeout")
        self.assertEqual(total_limits, [600])
        self.assertEqual(len(calls), 1)
        self.assertIn("结果未确认", str(failed.exception))
        self.assertIn("不会自动重试", str(failed.exception))

    async def test_per_group_deadline_overrides_default_and_stays_out_of_gateway_body(self):
        original_timeout = asyncio.timeout
        for selected, expected in ((600, 600), (900, 900), (30, 30), (None, 240)):
            self.request.timeout_seconds = selected
            observed = []

            def deadline(seconds):
                observed.append(seconds)
                return original_timeout(seconds)

            def handler(request):
                self.assertEqual(request.extensions["timeout"]["read"], expected)
                self.assertNotIn("timeout_seconds", json.loads(request.content))
                return httpx.Response(200, json={"choices": [{"message": {"content": "完成"}}]})

            with self.subTest(selected=selected), patch("helper.gallery_generation.asyncio.timeout", side_effect=deadline):
                self.assertEqual((await self.run_with(handler))["content"], "完成")
            self.assertEqual(observed, [expected])
        for invalid in (True, 29, 901, 600.5, "600", "secret"):
            self.request.timeout_seconds = invalid
            calls = []
            with self.subTest(invalid=invalid), self.assertRaises(GalleryGenerationError):
                await self.run_with(lambda request: calls.append(request))
            self.assertEqual(calls, [])

    async def test_optional_title_uses_plain_document_title_or_first_heading(self):
        for source, expected in (
            ('<html><head><title>星光 &amp; 海洋</title></head><body><h1>后备标题</h1></body></html>', "星光 & 海洋"),
            ('<main><h2>  第一幅 <span>作品</span> </h2><h1>另一标题</h1></main>', "第一幅 作品"),
            ('<svg><title>' + "海" * 110 + '</title><circle r="1"/></svg>', "海" * 100),
        ):
            with self.subTest(expected=expected):
                result = await self.run_with(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": source}}]}))
                self.assertEqual(result["title"], expected)

    async def test_inline_response_gif_saved_without_fetching_or_reencoding(self):
        self.request.api_format = "responses"
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"output": [{"type": "image_generation_call", "result": base64.b64encode(GIF).decode(), "output_format": "gif"}]})

        result = await self.run_with(handler)
        self.assertEqual((result["kind"], result["mime"], result["content"]), ("image", "image/gif", GIF_URI))
        self.assertEqual(len(calls), 1)

    async def test_direct_raster_gif_response_is_supported(self):
        result = await self.run_with(lambda request: httpx.Response(200, content=GIF, headers={"content-type": "image/gif"}))
        self.assertEqual(result["content"], GIF_URI)

    async def test_external_media_is_never_fetched_or_stored(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "![art](https://evil.test/a.gif)"}}]})

        with self.assertRaises(GalleryGenerationError):
            await self.run_with(handler)
        self.assertEqual(len(calls), 1)

    async def test_unsupported_inline_data_is_not_stored_as_plain_base64(self):
        with self.assertRaises(GalleryGenerationError):
            await self.run_with(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "data:image/svg+xml;base64,PHN2Zz4="}}]}))

    async def test_schema_valid_eight_thousand_character_prompt_is_accepted(self):
        self.request.prompt = "海" * 8000
        result = await self.run_with(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "完成"}}]}))
        self.assertEqual(result["content"], "完成")

    async def test_plain_text_truncation_is_utf8_safe(self):
        result = await self.run_with(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "海" * MAX_TEXT_BYTES}}]}))
        self.assertEqual(result["kind"], "text")
        self.assertLessEqual(len(result["content"].encode()), MAX_TEXT_BYTES)
        self.assertTrue(result["content"].endswith("海"))

    async def test_redirect_error_and_upstream_secret_are_not_exposed_or_retried(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(307, headers={"location": "https://evil.test/admin-secret"}, text="upstream ordinary-key admin-secret")

        with self.assertRaises(GalleryGenerationError) as raised:
            await self.run_with(handler, follow_redirects=True)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("ordinary-key", str(raised.exception))

    async def test_oversized_stream_fails_and_response_is_closed(self):
        stream = Stream([b"x" * 65536] * (MAX_RESPONSE_BYTES // 65536 + 4))
        with self.assertRaises(GalleryGenerationError):
            await self.run_with(lambda request: httpx.Response(200, stream=stream))
        self.assertTrue(stream.closed)
        self.assertLess(stream.consumed, len(stream.chunks))

    async def test_compressed_response_is_rejected_without_decompression(self):
        stream = Stream([b"not even gzip"])
        with self.assertRaises(GalleryGenerationError):
            await self.run_with(lambda request: httpx.Response(200, stream=stream, headers={"content-encoding": "gzip"}))
        self.assertEqual(stream.consumed, 0)
        self.assertTrue(stream.closed)

    async def test_timeout_has_safe_error_and_no_retry(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("ordinary-key admin-secret", request=request)

        with self.assertRaises(GalleryGenerationError) as raised:
            await self.run_with(handler)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", str(raised.exception))

    async def test_malformed_payload_cannot_fall_back_to_unsanitized_html(self):
        for payload in ({"choices": []}, {"choices": [{"message": {"content": {"html": "<script>bad</script>"}}}]}, {"choices": [{"message": {"content": "<div>" + "x" * MAX_HTML_BYTES + "</div>"}}]}):
            with self.subTest(payload_type=type(payload)):
                with self.assertRaises(GalleryGenerationError):
                    await self.run_with(lambda request: httpx.Response(200, json=payload))


if __name__ == "__main__":
    unittest.main()
