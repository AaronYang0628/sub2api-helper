"""Model gateway tests with real HTTP request construction and fake responses."""

import json
import unittest

import httpx

from helper.benchmark import QUESTIONS, extract_answer, run_benchmark
from helper.config import Config
from helper.schemas import Benchmark


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


if __name__ == "__main__":
    unittest.main()
