"""Small deterministic smoke benchmark; not an IQ measurement or contamination detector."""
import base64
import binascii
import json
import re
import time
import zlib
from urllib.parse import unquote, urlsplit

import httpx

QUESTIONS = (
    ("计算 17 × 23。只输出整数答案。", "391"),
    ("字符串 SUB2API 中有多少个字母（数字不计）？只输出整数。", "6"),
    ("所有蓝球都是圆的。某个物体是蓝球。该物体是否一定是圆的？只输出 YES 或 NO。", "YES"),
    ("将列表 [9, 2, 11, -1] 从小到大排序，只输出 JSON 数组，不加解释。", "[-1,2,9,11]"),
    ("有 3 个盒子，每盒 4 个球，拿走 5 个球，还剩多少？只输出整数。", "7"),
)

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_INLINE_BYTES = 8 * 1024 * 1024
MAX_MEDIA = 6
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ROUND_BYTES = 16 * 1024 * 1024
MAX_IMAGE_SIDE = 8192
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
IMAGE_MIMES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
MARKDOWN_IMAGE = re.compile(r"!\[([^\]\r\n]{0,200})\]\(")
BARE_SOURCE = re.compile(r"data:(?:image/[^\s<>\"']+|[a-z0-9.+-]+/[a-z0-9.+-]+;base64,[^\s<>\"']+)|https?://[^\s<>\"']+", re.I)


def _png_dimensions(raw):
    index, dimensions, pixels = 8, None, False
    while index + 12 <= len(raw):
        length = int.from_bytes(raw[index:index + 4], "big")
        end = index + 12 + length
        if end > len(raw):
            return None
        kind = raw[index + 4:index + 8]
        if zlib.crc32(raw[index + 4:end - 4]) != int.from_bytes(raw[end - 4:end], "big"):
            return None
        if kind == b"IHDR":
            if index != 8 or length != 13:
                return None
            dimensions = (int.from_bytes(raw[index + 8:index + 12], "big"), int.from_bytes(raw[index + 12:index + 16], "big"))
        elif dimensions is None:
            return None
        elif kind == b"IDAT":
            pixels = True
        elif kind == b"IEND":
            return dimensions if length == 0 and end == len(raw) and pixels else None
        index = end
    return None


def _image_info(raw):
    """Read raster signatures and dimensions without decoding image pixels."""
    mime, size = None, None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime, size = "image/png", _png_dimensions(raw)
    elif len(raw) >= 26 and raw[:6] in {b"GIF87a", b"GIF89a"} and raw.endswith(b";") and b"," in raw[13:-1]:
        mime, size = "image/gif", (int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little"))
    elif len(raw) >= 25 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" and int.from_bytes(raw[4:8], "little") + 8 == len(raw):
        mime = "image/webp"
        if raw[12:16] == b"VP8X" and len(raw) >= 30:
            size = (1 + int.from_bytes(raw[24:27], "little"), 1 + int.from_bytes(raw[27:30], "little"))
        elif raw[12:16] == b"VP8L" and raw[20] == 0x2F:
            bits = int.from_bytes(raw[21:25], "little")
            size = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        elif raw[12:16] == b"VP8 " and len(raw) >= 30 and raw[23:26] == b"\x9d\x01\x2a":
            size = (int.from_bytes(raw[26:28], "little") & 0x3FFF, int.from_bytes(raw[28:30], "little") & 0x3FFF)
    elif len(raw) >= 12 and raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"):
        mime, index = "image/jpeg", 2
        while index + 4 <= len(raw) and raw[index] == 0xFF:
            while index < len(raw) and raw[index] == 0xFF:
                index += 1
            if index >= len(raw):
                break
            marker, index = raw[index], index + 1
            if marker in {0xD8, 0xD9}:
                continue
            if marker == 0xDA or index + 2 > len(raw):
                break
            length = int.from_bytes(raw[index:index + 2], "big")
            if length < 2 or index + length > len(raw):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 8:
                size = (int.from_bytes(raw[index + 5:index + 7], "big"), int.from_bytes(raw[index + 3:index + 5], "big"))
                break
            index += length
    if size and all(0 < side <= MAX_IMAGE_SIDE for side in size) and size[0] * size[1] <= MAX_IMAGE_PIXELS:
        return mime
    return None


class _Media:
    def __init__(self, inline_budget):
        self.items, self.seen, self.omitted = [], set(), 0
        self.inline_budget = inline_budget

    def add(self, source, *, mime=None, alt=None, raw=None, encoded=False):
        try:
            mime = IMAGE_MIMES.get(mime, mime) if mime else None
            if mime and mime not in IMAGE_MIMES.values():
                raise ValueError("Unsupported image format")
            if raw is not None or encoded or (isinstance(source, str) and source.lower().startswith("data:")):
                if raw is None:
                    if not isinstance(source, str):
                        raise ValueError("Invalid image content")
                    if not encoded:
                        match = re.fullmatch(r"data:(image/[a-z]+);base64,(.*)", source, re.I | re.S)
                        if not match:
                            raise ValueError("Unsupported data URI")
                        mime, source = match.group(1).lower(), match.group(2)
                        mime = "image/jpeg" if mime == "image/jpg" else mime
                        if mime not in IMAGE_MIMES.values():
                            raise ValueError("Unsupported image format")
                    if len(source) > (MAX_IMAGE_BYTES * 4 // 3) + 4096:
                        raise ValueError("Image is too large")
                    source = re.sub(r"\s+", "", source)
                    raw = base64.b64decode(source, validate=True)
                if len(raw) > MAX_IMAGE_BYTES:
                    raise ValueError("Image is too large")
                detected = _image_info(raw)
                if not detected or (mime and mime != detected):
                    raise ValueError("Image signature or dimensions are invalid")
                mime = detected
                source = "data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii")
                cost = len(raw)
            else:
                if not isinstance(source, str) or not source or len(source) > 4096 or "\\" in source or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in source):
                    raise ValueError("Invalid image URL")
                parsed = urlsplit(source)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                    raise ValueError("Image URL must be credential-free HTTPS")
                parsed.port
                suffix = unquote(parsed.path).rsplit(".", 1)[-1].lower()
                if suffix in {"svg", "svgz"}:
                    raise ValueError("SVG previews are unsupported")
                mime = mime or IMAGE_MIMES.get(suffix)
                source, cost = str(httpx.URL(source)), 0
            if source in self.seen:
                return
            if len(self.items) >= MAX_MEDIA or cost > self.inline_budget[0]:
                raise ValueError("Media budget exceeded")
            item = {"src": source}
            if mime:
                item["mime"] = mime
            if isinstance(alt, str) and alt:
                item["alt"] = alt[:200]
            self.items.append(item)
            self.seen.add(source)
            self.inline_budget[0] -= cost
        except (ValueError, TypeError, binascii.Error, UnicodeError):
            self.omitted += 1

    def text(self, value):
        """Extract Markdown and bare image sources without rendering HTML."""
        output, cursor = [], 0
        while match := MARKDOWN_IMAGE.search(value, cursor):
            output.append(value[cursor:match.start()])
            start, index, depth = match.end(), match.end(), 1
            while index < len(value) and depth:
                if value[index] == "\\":
                    index += 2
                    continue
                depth += (value[index] == "(") - (value[index] == ")")
                index += 1
            if depth:
                output.append(value[match.start():])
                cursor = len(value)
                break
            inside = value[start:index - 1].strip()
            source = inside[1:inside.find(">")].strip() if inside.startswith("<") and ">" in inside else (inside.split(maxsplit=1)[0] if inside else "")
            self.add(source, alt=match.group(1))
            cursor = index
        output.append(value[cursor:])
        value = "".join(output)

        def bare(match):
            source = match.group(0)
            if source.lower().startswith("data:"):
                self.add(source)
                return ""
            source = source.rstrip(".,;!)]}")
            try:
                suffix = unquote(urlsplit(source).path).rsplit(".", 1)[-1].lower()
            except ValueError:
                return match.group(0)
            if suffix in IMAGE_MIMES:
                self.add(source)
                return ""
            return match.group(0)
        return BARE_SOURCE.sub(bare, value).strip()


def extract_content(payload, api_format, inline_budget=None):
    media = _Media(inline_budget if inline_budget is not None else [MAX_INLINE_BYTES])
    texts = []

    def parts(content):
        if isinstance(content, str):
            texts.append(content)
            return
        if content is None:
            return
        if not isinstance(content, list):
            raise ValueError("Invalid message content")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("Invalid content part")
            kind = part.get("type")
            if kind in {"text", "output_text"}:
                if not isinstance(part.get("text"), str):
                    raise ValueError("Invalid text content")
                texts.append(part["text"])
            elif kind in {"image_url", "output_image", "input_image"}:
                source = part.get("image_url", part.get("url"))
                if isinstance(source, dict):
                    source = source.get("url")
                if source is not None:
                    media.add(source, mime=part.get("mime_type"), alt=part.get("alt"))
                else:
                    media.add(part.get("b64_json", part.get("result")), mime=part.get("output_format", part.get("mime_type")), encoded=True, alt=part.get("alt"))

    if api_format == "chat":
        message = payload["choices"][0]["message"]
        if not isinstance(message, dict) or ("content" not in message and "images" not in message):
            raise ValueError("Invalid chat message")
        parts(message.get("content"))
        if "images" in message:
            parts(message["images"])
    else:
        items = payload["output"]
        if not isinstance(items, list):
            raise ValueError("Invalid responses output")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid responses output item")
            if item.get("type") == "image_generation_call":
                media.add(item.get("result"), mime=item.get("output_format"), encoded=True)
            elif item.get("type") not in {"reasoning", "function_call"}:
                if item.get("type") in {"output_image", "image_url"}:
                    parts([item])
                else:
                    parts(item.get("content", []))
    answer = media.text("".join(texts))
    return answer, media.items, media.omitted


def extract_answer(payload, api_format):
    return extract_content(payload, api_format)[0]


def normalize(answer):
    return re.sub(r"\s+", "", answer).strip("`").upper()


class _BodyLimit(ValueError):
    def __init__(self, round_limit=False):
        self.round_limit = round_limit
        super().__init__("Model response exceeds the byte limit")


async def _read_body(response, limit, budget):
    chunks, length = [], 0

    async def iterator():
        if response.is_stream_consumed:
            yield response.content
        else:
            async for chunk in response.aiter_bytes(chunk_size=65536):
                yield chunk

    async for chunk in iterator():
        length += len(chunk)
        budget[0] -= len(chunk)
        if budget[0] < 0:
            raise _BodyLimit(round_limit=True)
        if length > limit:
            raise _BodyLimit()
        chunks.append(chunk)
    return b"".join(chunks)


async def run_benchmark(config, request, client=None):
    own = client is None
    client = client or httpx.AsyncClient(timeout=45, follow_redirects=False)
    results = []
    inline_budget, body_budget = [MAX_INLINE_BYTES], [MAX_ROUND_BYTES]
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
            media, media_omitted = [], 0
            response = None
            try:
                outgoing = httpx.Request("POST",
                    config.sub2api_url.rstrip("/") + path,
                    headers={"Authorization": "Bearer " + request.api_key, "Accept": "application/json", "Accept-Encoding": "identity"}, json=body,
                )
                response = await client.send(outgoing, stream=True, auth=None, follow_redirects=False)
                response.raise_for_status()
                if response.headers.get("content-encoding", "").strip().lower() not in {"", "identity"}:
                    # HTTPX can allocate a decompressed bomb before yielding a
                    # bounded chunk. We request identity and reject other codecs.
                    raise ValueError("Compressed model responses are unsupported")
                mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if mime.startswith("image/"):
                    collector = _Media(inline_budget)
                    try:
                        raw = await _read_body(response, MAX_IMAGE_BYTES, body_budget)
                        collector.add(None, raw=raw, mime=mime)
                    except _BodyLimit as exc:
                        if exc.round_limit:
                            raise
                        collector.omitted += 1
                    media, media_omitted = collector.items, collector.omitted
                else:
                    raw = await _read_body(response, MAX_RESPONSE_BYTES, body_budget)
                    answer, media, media_omitted = extract_content(json.loads(raw), request.api_format, inline_budget)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError):
                error = "模型请求失败或响应不兼容，请检查模型、普通 API Key 及网关格式"
            finally:
                if response is not None:
                    await response.aclose()
            scored = error is None and bool(answer.strip())
            results.append({"question": question, "expected": expected, "answer": answer[:4000],
                            "media": media, "media_omitted": media_omitted, "scored": scored,
                            "passed": scored and normalize(answer) == normalize(expected),
                            "latency_ms": round((time.monotonic() - started) * 1000), "error": error})
            if error:
                break
        return {"model": request.model, "score": sum(r["passed"] for r in results),
                "total": len(QUESTIONS), "completed": len(results), "results": results,
                "notice": "固定题集仅供基本能力自检，不代表模型身份、智商或真实性。请求会消耗额度。"}
    finally:
        if own:
            await client.aclose()
