"""Small deterministic smoke benchmark; not an IQ measurement or contamination detector."""
import asyncio
import re
import time

import httpx

QUESTIONS = (
    ("计算 17 × 23。只输出整数答案。", "391"),
    ("字符串 SUB2API 中有多少个字母（数字不计）？只输出整数。", "6"),
    ("所有蓝球都是圆的。某个物体是蓝球。该物体是否一定是圆的？只输出 YES 或 NO。", "YES"),
    ("将列表 [9, 2, 11, -1] 从小到大排序，只输出 JSON 数组，不加解释。", "[-1,2,9,11]"),
    ("有 3 个盒子，每盒 4 个球，拿走 5 个球，还剩多少？只输出整数。", "7"),
)


def extract_answer(payload, api_format):
    if api_format == "chat":
        return str(payload["choices"][0]["message"]["content"])
    return "".join(
        part.get("text", "")
        for item in payload.get("output", [])
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def normalize(answer):
    return re.sub(r"\s+", "", answer).strip("`").upper()


async def run_benchmark(config, request, client=None):
    own = client is None
    client = client or httpx.AsyncClient(timeout=45, follow_redirects=False)
    results = []
    try:
        for question, expected in QUESTIONS:
            started = time.monotonic()
            body = {"model": request.model, "stream": False}
            if request.api_format == "chat":
                body.update(messages=[{"role": "user", "content": question}], max_tokens=128)
                path = "/v1/chat/completions"
            else:
                body.update(input=question, max_output_tokens=256)
                path = "/v1/responses"
            error = None
            answer = ""
            try:
                response = await client.post(
                    config.sub2api_url.rstrip("/") + path,
                    headers={"Authorization": "Bearer " + request.api_key}, json=body,
                    follow_redirects=False,
                )
                response.raise_for_status()
                answer = extract_answer(response.json(), request.api_format)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
                error = "模型请求失败或响应不兼容，请检查模型、普通 API Key 及网关格式"
            results.append({"question": question, "expected": expected, "answer": answer[:4000],
                            "passed": error is None and normalize(answer) == normalize(expected),
                            "latency_ms": round((time.monotonic() - started) * 1000), "error": error})
            if error:
                break
        return {"model": request.model, "score": sum(r["passed"] for r in results),
                "total": len(QUESTIONS), "completed": len(results), "results": results,
                "notice": "固定题集仅供基本能力自检，不代表模型身份、智商或真实性。请求会消耗额度。"}
    finally:
        if own:
            await client.aclose()
