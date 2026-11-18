"""Model gateway tests with real HTTP request construction and fake responses."""

import asyncio
import base64
import gzip
import json
import struct
import unittest
from unittest.mock import patch
import zlib

import httpx

from helper.benchmark import QUESTIONS, extract_answer, extract_content, run_benchmark
from helper.config import Config
from helper.schemas import Benchmark


def png(width=1, height=1):
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b"")


PNG = png()
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()
GIF_URI = "data:image/gif;base64," + base64.b64encode(GIF).decode()


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self):
        self.closed = True


class BenchmarkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = Config(
            sub2api_url="https://sub2api.example.test",
            sub2api_admin_key="management-secret-never-send-to-model",
            admin_token="console-secret-never-send-to-model",
        )
        self.request = Benchmark(model="sample-model", api_key="ordinary-user-api-key")

    async def run_with(self, handler, request=None, **client_options):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), **client_options) as client:
            return await run_benchmark(self.config, request or self.request, client=client)

    async def test_all_correct_chat_answers_score_five_and_only_use_user_key(self):
        requests = []

        def handler(request):
            requests.append(request)
            body = json.loads(request.content)
            self.assertEqual(request.url, httpx.URL("https://sub2api.example.test/v1/chat/completions"))
            self.assertEqual(request.headers["Authorization"], "Bearer ordinary-user-api-key")
            self.assertNotIn("X-Api-Key", request.headers)
            for secret in (self.config.sub2api_admin_key, self.config.admin_token):
                self.assertNotIn(secret, str(request.headers))
                self.assertNotIn(secret.encode(), request.content)
            self.assertEqual(body["model"], "sample-model")
            self.assertFalse(body["stream"])
            self.assertEqual(body["max_tokens"], 128)
            expected_question, expected = QUESTIONS[len(requests) - 1]
            self.assertEqual(body["messages"], [{"role": "user", "content": expected_question}])
            # Whitespace and letter case are harmless for the fixed answer format.
            return httpx.Response(200, json={"choices": [{"message": {"content": "  " + expected.lower() + "\n"}}]})

        result = await self.run_with(handler)
        self.assertEqual((result["score"], result["completed"], result["total"]), (5, 5, 5))
        self.assertEqual(len(requests), 5)
        self.assertTrue(all(item["passed"] and item["error"] is None for item in result["results"]))
        self.assertTrue(all(isinstance(item["latency_ms"], int) and item["latency_ms"] >= 0 for item in result["results"]))

    async def test_wrong_answer_is_not_accepted_as_matching_substring(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            # Mentioning the expected answer with extra content must not pass.
            answer = "391 is incorrect, the answer is 392" if calls == 1 else QUESTIONS[calls - 1][1]
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})

        result = await self.run_with(handler)
        self.assertEqual((result["score"], result["completed"]), (4, 5))
        self.assertFalse(result["results"][0]["passed"])
        self.assertIsNone(result["results"][0]["error"])

    async def test_responses_endpoint_collects_only_output_text_parts(self):
        calls = 0

        def handler(request):
            nonlocal calls
            question, expected = QUESTIONS[calls]
            calls += 1
            body = json.loads(request.content)
            self.assertEqual(request.url.path, "/v1/responses")
            self.assertEqual(request.headers["Authorization"], "Bearer ordinary-user-api-key")
            self.assertEqual(body["input"], question)
            self.assertEqual(body["max_output_tokens"], 256)
            self.assertNotIn("messages", body)
            midpoint = len(expected) // 2
            return httpx.Response(200, json={"output": [
                {"type": "reasoning", "content": [{"type": "summary_text", "text": "private reasoning is not an answer"}]},
                {"type": "message", "content": [{"type": "output_text", "text": expected[:midpoint]}, {"type": "output_text", "text": expected[midpoint:]}]},
            ]})

        result = await self.run_with(handler, self.request.model_copy(update={"api_format": "responses"}))
        self.assertEqual((result["score"], result["completed"]), (5, 5))
        self.assertNotIn("private reasoning", str(result))

    async def test_http_failure_stops_after_first_request_without_credential_echo(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(403, text="Rejected ordinary-user-api-key management-secret-never-send-to-model console-secret-never-send-to-model")

        result = await self.run_with(handler)
        self.assertEqual((result["score"], result["completed"], result["total"]), (0, 1, 5))
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["results"][0]["passed"])
        self.assertTrue(result["results"][0]["error"])
        for secret in (self.request.api_key, self.config.sub2api_admin_key, self.config.admin_token):
            self.assertNotIn(secret, str(result))

    async def test_timeout_stops_after_first_question_and_sanitizes_exception(self):
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("sensitive ordinary-user-api-key timeout detail", request=request)

        result = await self.run_with(handler)
        self.assertEqual((result["score"], result["completed"]), (0, 1))
        self.assertEqual(len(calls), 1)
        self.assertNotIn(self.request.api_key, str(result))

    async def test_malformed_chat_and_responses_bodies_fail_safely(self):
        for api_format, payload in (("chat", {}), ("chat", {"choices": []}), ("chat", {"choices": None}), ("responses", {"output": None})):
            with self.subTest(api_format=api_format, payload=payload):
                result = await self.run_with(lambda request: httpx.Response(200, json=payload), self.request.model_copy(update={"api_format": api_format}))
                self.assertEqual((result["score"], result["completed"]), (0, 1))
                self.assertTrue(result["results"][0]["error"])

    async def test_non_json_response_stops_without_echoing_raw_body(self):
        result = await self.run_with(lambda request: httpx.Response(200, text="ordinary-user-api-key invalid gateway response"))
        self.assertEqual((result["score"], result["completed"]), (0, 1))
        self.assertNotIn(self.request.api_key, str(result))

    async def test_gateway_redirect_is_never_followed_even_if_client_defaults_allow_it(self):
        destinations = []

        def handler(request):
            destinations.append(str(request.url))
            if request.url.path == "/v1/chat/completions":
                return httpx.Response(307, headers={"Location": "https://unexpected.example.test/receive"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "391"}}]})

        result = await self.run_with(handler, follow_redirects=True)
        self.assertEqual(destinations, ["https://sub2api.example.test/v1/chat/completions"])
        self.assertEqual((result["score"], result["completed"]), (0, 1))
        self.assertTrue(result["results"][0]["error"])

    async def test_display_answer_is_bounded_and_not_silently_scored_from_truncation(self):
        calls = 0
        long_invalid = "391" + " " * 3997 + "this appended text makes the exact answer incorrect"

        def handler(request):
            nonlocal calls
            calls += 1
            answer = long_invalid if calls == 1 else QUESTIONS[calls - 1][1]
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})

        result = await self.run_with(handler)
        self.assertLessEqual(len(result["results"][0]["answer"]), 4000)
        self.assertFalse(result["results"][0]["passed"], "Truncation must not turn an incorrect full answer into a passing one")
        self.assertEqual(result["score"], 4)

    def test_response_text_parser_ignores_non_text_content(self):
        payload = {"output": [{"content": [{"type": "refusal", "text": "not answer"}, {"type": "output_text", "text": "YES"}]}]}
        self.assertEqual(extract_answer(payload, "responses"), "YES")
        self.assertEqual(extract_answer({"choices": [{"message": {"content": "391"}}]}, "chat"), "391")

    async def test_mixed_structured_chat_text_and_images_still_scores_five(self):
        calls = 0

        def handler(request):
            nonlocal calls
            answer = QUESTIONS[calls][1]
            calls += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": [
                {"type": "text", "text": answer}, {"type": "image_url", "image_url": {"url": GIF_URI}},
            ]}}]})

        result = await self.run_with(handler)
        self.assertEqual((result["score"], result["completed"]), (5, 5))
        for item in result["results"]:
            self.assertTrue(item["scored"])
            self.assertEqual(item["media"], [{"src": GIF_URI, "mime": "image/gif"}])
            self.assertNotIn("base64", item["answer"])

    async def test_responses_output_image_string_and_text_score_independently(self):
        calls = 0

        def handler(request):
            nonlocal calls
            answer = QUESTIONS[calls][1]
            calls += 1
            return httpx.Response(200, json={"output": [{"type": "message", "content": [
                {"type": "output_text", "text": answer}, {"type": "output_image", "image_url": "https://cdn.example.test/answer.gif?sig=signed-value"},
            ]}]})

        result = await self.run_with(handler, self.request.model_copy(update={"api_format": "responses"}))
        self.assertEqual(result["score"], 5)
        self.assertEqual(result["results"][0]["media"][0]["mime"], "image/gif")
        self.assertIn("sig=signed-value", result["results"][0]["media"][0]["src"])

    async def test_media_only_results_are_completed_unscored_and_do_not_stop_next_question(self):
        result = await self.run_with(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "image_url", "image_url": PNG_URI}]}}]}))
        self.assertEqual((result["score"], result["completed"], result["total"]), (0, 5, 5))
        self.assertTrue(all(not item["scored"] and not item["passed"] and item["error"] is None and item["answer"] == "" for item in result["results"]))

    async def test_markdown_images_are_removed_from_text_before_exact_scoring(self):
        calls = 0

        def handler(request):
            nonlocal calls
            answer = QUESTIONS[calls][1]
            calls += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": answer + '\n![示意图](https://cdn.example.test/image(1).png "preview")'}}]})

        result = await self.run_with(handler)
        self.assertEqual(result["score"], 5)
        self.assertEqual(result["results"][0]["answer"], "391")
        self.assertEqual(result["results"][0]["media"][0]["alt"], "示意图")
        self.assertEqual(result["results"][0]["media"][0]["src"], "https://cdn.example.test/image(1).png")

    def test_bare_urls_require_image_extension_but_structured_sources_can_be_extensionless(self):
        payload = {"choices": [{"message": {"content": "https://cdn.example.test/result.GIF?size=small"}}]}
        answer, media, omitted = extract_content(payload, "chat")
        self.assertEqual(answer, "")
        self.assertEqual(media[0]["mime"], "image/gif")
        payload["choices"][0]["message"]["content"] = "https://example.test/page"
        self.assertEqual(extract_content(payload, "chat")[0], "https://example.test/page")
        payload["choices"][0]["message"]["content"] = [{"type": "output_image", "image_url": "https://cdn.example.test/render?id=1"}]
        self.assertEqual(extract_content(payload, "chat")[1], [{"src": "https://cdn.example.test/render?id=1"}])

    def test_unsafe_sources_and_svg_are_omitted_without_retaining_base64(self):
        unsafe = ["javascript:alert(1)", "http://cdn.example.test/image.png", "https://user:password@cdn.example.test/image.png", "https://cdn.example.test/x.svg", "https://cdn.example.test\\evil.png", "https://cdn.example.test/a\n.png", "data:text/html;base64,PHNjcmlwdD4=", "data:image/svg+xml;base64,PHN2Zz4=", "data:image/png;base64,not-valid-base64"]
        for source in unsafe:
            with self.subTest(source=source):
                answer, media, omitted = extract_content({"choices": [{"message": {"content": [{"type": "image_url", "image_url": source}]}}]}, "chat")
                self.assertEqual((answer, media, omitted), ("", [], 1))
        answer, media, omitted = extract_content({"choices": [{"message": {"content": "391\ndata:image/svg+xml;base64,PHN2Zz4="}}]}, "chat")
        self.assertEqual((answer, media, omitted), ("391", [], 1))

    def test_signature_mismatch_truncation_and_huge_dimensions_are_rejected(self):
        header_only = PNG[:24]
        for source in ("data:image/jpeg;base64," + base64.b64encode(PNG).decode(), "data:image/png;base64," + base64.b64encode(header_only).decode(), "data:image/png;base64," + base64.b64encode(png(100000, 100000)).decode()):
            answer, media, omitted = extract_content({"choices": [{"message": {"content": [{"type": "image_url", "image_url": source}]}}]}, "chat")
            self.assertEqual((answer, media, omitted), ("", [], 1))

    def test_responses_image_generation_call_decodes_only_supported_signed_raster_data(self):
        payload = {"output": [{"type": "image_generation_call", "result": base64.b64encode(PNG).decode(), "output_format": "png"}]}
        self.assertEqual(extract_content(payload, "responses"), ("", [{"src": PNG_URI, "mime": "image/png"}], 0))
        payload["output"][0]["result"] = base64.b64encode(b"<svg>script</svg>").decode()
        self.assertEqual(extract_content(payload, "responses"), ("", [], 1))

    def test_provider_message_images_side_field_is_preserved(self):
        payload = {"choices": [{"message": {"content": "391", "images": [{"type": "image_url", "image_url": {"url": PNG_URI}}]}}]}
        answer, media, omitted = extract_content(payload, "chat")
        self.assertEqual((answer, omitted), ("391", 0))
        self.assertEqual(media[0]["src"], PNG_URI)
        payload["choices"][0]["message"].pop("content")
        answer, media, omitted = extract_content(payload, "chat")
        self.assertEqual((answer, omitted), ("", 0))
        self.assertEqual(media[0]["src"], PNG_URI)
        with self.assertRaises(ValueError):
            extract_content({"choices": [{"message": {}}]}, "chat")

    def test_ordinary_data_prefix_text_is_not_mistaken_for_an_image(self):
        payload = {"choices": [{"message": {"content": "data:example"}}]}
        self.assertEqual(extract_content(payload, "chat"), ("data:example", [], 0))
        payload["choices"][0]["message"]["content"] = "data:text/html;base64,PHNjcmlwdD4="
        self.assertEqual(extract_content(payload, "chat"), ("", [], 1))

    def test_per_result_media_count_and_per_round_inline_budget_are_bounded(self):
        parts = [{"type": "image_url", "image_url": "https://cdn.example.test/" + str(n) + ".png"} for n in range(8)]
        answer, media, omitted = extract_content({"choices": [{"message": {"content": parts}}]}, "chat")
        self.assertEqual((len(media), omitted), (6, 2))
        budget = [len(PNG)]
        payload = {"choices": [{"message": {"content": [{"type": "image_url", "image_url": PNG_URI}]}}]}
        self.assertEqual(len(extract_content(payload, "chat", budget)[1]), 1)
        self.assertEqual(extract_content(payload, "chat", budget)[1:], ([], 1))

    async def test_all_omitted_media_is_explained_without_fake_request_error(self):
        result = await self.run_with(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "image_url", "image_url": "data:image/svg+xml;base64,PHN2Zz4="}]}}]}))
        self.assertEqual(result["completed"], 5)
        self.assertTrue(all(item["media_omitted"] == 1 and not item["scored"] and item["error"] is None for item in result["results"]))

    async def test_binary_gif_response_is_preserved_as_unscored_media(self):
        result = await self.run_with(lambda request: httpx.Response(200, content=GIF, headers={"Content-Type": "image/gif"}))
        self.assertEqual(result["completed"], 5)
        self.assertEqual(result["results"][0]["media"], [{"src": GIF_URI, "mime": "image/gif"}])
        self.assertFalse(result["results"][0]["scored"])

    async def test_oversize_binary_image_is_omitted_and_stream_is_closed(self):
        streams = []

        def handler(request):
            stream = Stream([b"a" * 20])
            streams.append(stream)
            return httpx.Response(200, stream=stream, headers={"content-type": "image/gif"})

        with patch("helper.benchmark.MAX_IMAGE_BYTES", 10):
            result = await self.run_with(handler)
        self.assertEqual(result["completed"], 5)
        self.assertTrue(all(item["media_omitted"] == 1 and item["error"] is None for item in result["results"]))
        self.assertTrue(all(stream.closed for stream in streams))

    async def test_streamed_json_limit_aborts_without_reading_the_entire_body(self):
        stream = Stream([b" " * 65536, b" " * 65536, b"unread"])
        with patch("helper.benchmark.MAX_RESPONSE_BYTES", 100):
            result = await self.run_with(lambda request: httpx.Response(200, stream=stream, headers={"content-type": "application/json"}))
        self.assertEqual(result["completed"], 1)
        self.assertTrue(result["results"][0]["error"])
        self.assertEqual(stream.consumed, 1)
        self.assertTrue(stream.closed)

    async def test_round_body_budget_stops_additional_gateway_calls(self):
        calls = 0
        payload = {"choices": [{"message": {"content": "391"}}]}
        encoded = json.dumps(payload).encode()

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=encoded, headers={"content-type": "application/json"})

        with patch("helper.benchmark.MAX_ROUND_BYTES", len(encoded) + 1):
            result = await self.run_with(handler)
        self.assertEqual((calls, result["completed"]), (2, 2))
        self.assertTrue(result["results"][1]["error"])

    async def test_unrequested_compression_is_rejected_before_any_decompression(self):
        stream = Stream([gzip.compress(b" " * 1_000_000)])
        result = await self.run_with(lambda request: httpx.Response(200, stream=stream, headers={"content-type": "application/json", "content-encoding": "gzip"}))
        self.assertEqual(result["completed"], 1)
        self.assertTrue(result["results"][0]["error"])
        self.assertEqual(stream.consumed, 0)
        self.assertTrue(stream.closed)

    async def test_injected_client_credentials_are_not_forwarded_to_model_gateway(self):
        calls = []

        def handler(request):
            calls.append(request)
            self.assertEqual(request.headers["Authorization"], "Bearer ordinary-user-api-key")
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            self.assertNotIn("X-Api-Key", request.headers)
            self.assertNotIn("Cookie", request.headers)
            return httpx.Response(200, json={"choices": [{"message": {"content": "wrong"}}]})

        await self.run_with(handler, headers={"X-API-Key": self.config.sub2api_admin_key}, cookies={"admin": "private"}, auth=("admin", "secret"))
        self.assertEqual(len(calls), 5)


if __name__ == "__main__":
    unittest.main()
