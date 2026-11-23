"""Generate a bounded static artwork fragment using an ordinary model API key.

Fragments are untrusted even after filtering. Display them only in an iframe
with an empty sandbox and a restrictive CSP; never insert them in the app DOM.
"""

import asyncio
import base64
import html
from html.parser import HTMLParser
import json
import re
import time

import httpx

from .benchmark import MAX_IMAGE_BYTES, _BodyLimit, _read_body, extract_content


MAX_HTML_BYTES = 128 * 1024
MAX_TEXT_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PROMPT_BYTES = 32_000
GENERATION_TIMEOUT = 240
REASONING_EFFORTS = frozenset({"default", "none", "minimal", "low", "medium", "high", "xhigh", "max"})

# The caller must put this policy on the surrounding srcdoc document, before
# the fragment. An empty iframe sandbox is an additional independent boundary.
ARTWORK_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "script-src 'none'; frame-src 'none'; connect-src 'none'; "
    "form-action 'none'; base-uri 'none'; object-src 'none'"
)

HTML_TAGS = set("div span p h1 h2 h3 h4 h5 h6 br hr section article header footer main aside figure figcaption pre code blockquote ul ol li dl dt dd strong b em i u s small sub sup time details summary table thead tbody tfoot tr th td colgroup col".split())
SVG_NAMES = {
    name.lower(): name for name in (
        "svg g defs symbol use path rect circle ellipse line polyline polygon "
        "text tspan textPath title desc linearGradient radialGradient stop "
        "clipPath mask pattern filter feGaussianBlur feOffset feBlend "
        "feColorMatrix feMerge feMergeNode feFlood feComposite "
        "animate animateTransform set"
    ).split()
}
ALLOWED_TAGS = HTML_TAGS | SVG_NAMES.keys() | {"img", "style"}
VOID_TAGS = {"br", "hr", "img", "col"}
SOURCE_VOID_TAGS = VOID_TAGS | {"area", "base", "embed", "input", "link", "meta", "param", "source", "track", "wbr"}
DOCUMENT_TAGS = {"html", "head", "body"}
LOCAL_REF = re.compile(r"#[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}\Z")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TIMING = re.compile(r"(?:-?\d+(?:\.\d+)?(?:ms|s|min|h)?|indefinite)\Z", re.I)

CSS_PROPERTIES = set((
    "color opacity display visibility overflow overflow-x overflow-y position top right bottom left inset z-index float clear "
    "width height min-width min-height max-width max-height aspect-ratio box-sizing margin margin-top margin-right margin-bottom margin-left "
    "padding padding-top padding-right padding-bottom padding-left background background-color background-image background-size background-position "
    "background-repeat background-origin background-clip background-attachment background-blend-mode border border-width border-style border-color "
    "border-top border-right border-bottom border-left border-radius border-top-left-radius border-top-right-radius border-bottom-left-radius "
    "border-bottom-right-radius border-collapse border-spacing outline outline-width outline-style outline-color outline-offset box-shadow "
    "font font-family font-size font-style font-weight font-variant font-stretch line-height letter-spacing word-spacing text-align text-decoration "
    "text-decoration-color text-decoration-line text-decoration-style text-transform text-shadow text-indent text-overflow white-space word-break "
    "overflow-wrap vertical-align list-style list-style-type list-style-position table-layout content "
    "flex flex-basis flex-direction flex-flow flex-grow flex-shrink flex-wrap align-content align-items align-self justify-content justify-items "
    "justify-self place-content place-items place-self order gap row-gap column-gap grid grid-area grid-template grid-template-areas "
    "grid-template-columns grid-template-rows grid-auto-columns grid-auto-rows grid-auto-flow grid-column grid-column-start grid-column-end "
    "grid-row grid-row-start grid-row-end transform transform-origin transform-style perspective perspective-origin backface-visibility "
    "animation animation-name animation-duration animation-delay animation-timing-function animation-iteration-count animation-direction "
    "animation-fill-mode animation-play-state transition transition-property transition-duration transition-delay transition-timing-function "
    "filter backdrop-filter isolation mix-blend-mode object-fit object-position fill fill-opacity fill-rule stroke stroke-width stroke-opacity "
    "stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin clip-path mask stop-color stop-opacity"
).split())
CSS_FUNCTIONS = set((
    "rgb rgba hsl hsla hwb lab lch oklab oklch calc min max clamp var "
    "linear-gradient radial-gradient conic-gradient repeating-linear-gradient repeating-radial-gradient repeating-conic-gradient "
    "translate translatex translatey translatez translate3d scale scalex scaley scalez scale3d rotate rotatex rotatey rotatez rotate3d "
    "skew skewx skewy matrix matrix3d perspective cubic-bezier steps blur brightness contrast drop-shadow grayscale hue-rotate invert opacity saturate sepia"
).split())
SVG_ATTRIBUTES = {
    name.lower(): name for name in (
        "viewBox preserveAspectRatio x y x1 x2 y1 y2 cx cy r rx ry width height d points transform "
        "fill fill-opacity fill-rule stroke stroke-width stroke-opacity stroke-dasharray stroke-dashoffset stroke-linecap stroke-linejoin stroke-miterlimit "
        "opacity vector-effect paint-order pathLength text-anchor dominant-baseline alignment-baseline font-family font-size font-style font-weight "
        "letter-spacing word-spacing textLength lengthAdjust dx dy rotate gradientUnits gradientTransform spreadMethod offset stop-color stop-opacity "
        "clip-path clip-rule clipPathUnits mask maskUnits maskContentUnits patternUnits patternContentUnits patternTransform filter filterUnits "
        "primitiveUnits in in2 result stdDeviation mode type values operator k1 k2 k3 k4 flood-color flood-opacity color-interpolation-filters "
        "attributeName attributeType from to by dur begin end min max repeatCount repeatDur restart calcMode keyTimes keySplines additive accumulate"
    ).split()
}
ANIMATED_ATTRIBUTES = {
    "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "width", "height", "d", "points", "transform",
    "opacity", "fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray", "stroke-dashoffset", "stop-color", "stop-opacity",
}
ANIMATION_TAGS = {"animate", "animatetransform", "set"}
ANIMATION_FIELDS = {"attributename", "attributetype", "from", "to", "by", "values", "dur", "begin", "end", "min", "max", "repeatcount", "repeatdur", "restart", "calcmode", "keytimes", "keysplines", "additive", "accumulate", "type", "fill"}


class GalleryGenerationError(RuntimeError):
    """A safe public error, without upstream bodies, URLs, or credentials."""


FAILURE_MESSAGES = {
    "key_lookup": "无法读取所选分组的有效模型 Key，请检查绑定、Key 状态与权限；本次未调用模型",
    "authentication": "模型网关拒绝鉴权或访问权限（HTTP 401/403），请检查所选分组的普通 API Key",
    "unavailable": "模型网关暂不可用（HTTP 503），请检查所选分组是否有可调度账号",
    "rate_limited": "模型网关限流或额度受限（HTTP 429），本次不会自动重试",
    "request_rejected": "模型网关拒绝请求，请检查模型名称、接口格式及参数",
    "request_invalid": "模型请求配置无法构造有效请求，本次未调用模型；请检查所选普通 API Key",
    "gateway": "模型网关返回错误或重定向，本次不会自动重试",
    "timeout": "模型生成等待超时，结果未确认，本次不会自动重试；可在分组配置中调整生成超时或降低思考强度",
    "connection": "无法连接模型网关或连接已中断，结果未确认，本次不会自动重试",
    "response_format": "模型网关已返回响应，但格式不兼容；请检查接口格式（Chat / Responses）",
    "response_too_large": "模型响应超过安全大小限制，未保存作品，本次不会自动重试",
    "artwork_invalid": "模型已返回内容，但作品超出安全限制或无法安全展示，本次不会自动重试",
    "empty_output": "模型未返回可展示的正文或内嵌图片，可能只返回了思考内容或耗尽输出额度；本次不会自动重试",
}


class GenerationFailure(GalleryGenerationError):
    """Only a fixed code, never upstream exception text, reaches durable history."""

    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in FAILURE_MESSAGES else "gateway"
        super().__init__(FAILURE_MESSAGES[self.code])


def public_generation_error(error):
    if isinstance(error, GenerationFailure):
        return FAILURE_MESSAGES.get(error.code, FAILURE_MESSAGES["gateway"])
    if isinstance(error, UnsupportedReasoningEffort):
        return str(UnsupportedReasoningEffort())
    return None


class UnsupportedReasoningEffort(GalleryGenerationError):
    """A fixed error for a rejected explicit effort request, never auto-retried."""

    def __init__(self):
        super().__init__("带思考力度设置的请求被模型或网关拒绝，请改用默认或检查接口，本次不会自动重试")


def _safe_css_value(value):
    value = re.sub(r"/\*.*?\*/", "", value, flags=re.S).strip()
    if not value or len(value) > 8192 or CONTROL.search(value) or any(char in value for char in "\\<>@{};"):
        return None
    # url() is useful for local SVG gradients, filters and clipping paths only.
    remaining = value
    for match in list(re.finditer(r"\burl\s*\(([^)]*)\)", value, re.I)):
        target = match.group(1).strip().strip("\"'")
        if not LOCAL_REF.fullmatch(target):
            return None
        remaining = remaining.replace(match.group(0), "", 1)
    for match in re.finditer(r"([A-Za-z_-][A-Za-z0-9_-]*)\s*\(", remaining):
        if match.group(1).lower() not in CSS_FUNCTIONS:
            return None
    # Reject unfinished functions/strings rather than relying on browser error
    # recovery to decide which subsequent declarations become part of a value.
    quote, depth = None, 0
    for char in value:
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
    return value if quote is None and depth == 0 else None


def _declarations(source):
    declarations = []
    for declaration in source.split(";"):
        name, separator, value = declaration.partition(":")
        name = name.strip().lower()
        if not separator or (name not in CSS_PROPERTIES and not re.fullmatch(r"--[a-z][a-z0-9-]{0,63}", name)):
            continue
        safe = _safe_css_value(value)
        if safe is not None:
            declarations.append(name + ":" + safe)
    return ";".join(declarations)


def _css_block_end(source, start):
    depth, quote = 1, None
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
            if depth > 8:
                return None
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _stylesheet(source, depth=0, keyframes=False):
    if depth > 4 or CONTROL.search(source) or any(char in source for char in "\\<>"):
        return ""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    result, cursor = [], 0
    while cursor < len(source):
        opening = source.find("{", cursor)
        if opening < 0:
            break
        header = source[cursor:opening].strip()
        # Discard @import and other terminated at-statements before a rule.
        header = header.rsplit(";", 1)[-1].strip()
        closing = _css_block_end(source, opening + 1)
        if closing is None:
            break
        body = source[opening + 1:closing]
        if keyframes:
            valid = re.fullmatch(r"(?:from|to|\d+(?:\.\d+)?%)(?:\s*,\s*(?:from|to|\d+(?:\.\d+)?%))*", header, re.I)
            safe = _declarations(body) if valid else ""
        elif re.fullmatch(r"@(?:-webkit-)?keyframes\s+[A-Za-z_][A-Za-z0-9_-]{0,127}", header, re.I):
            safe = _stylesheet(body, depth + 1, keyframes=True)
        elif re.fullmatch(r"@media\s+[a-z0-9\s():.,/_-]{1,256}", header, re.I):
            safe = _stylesheet(body, depth + 1)
        elif header and not header.startswith("@") and re.fullmatch(r"[A-Za-z0-9_#.\s,:>*+~\[\]=\"'()|^-]{1,512}", header):
            safe = _declarations(body)
        else:
            safe = ""
        if safe:
            result.append(header + "{" + safe + "}")
        cursor = closing + 1
    return "".join(result)


def _inline_image(source):
    if not isinstance(source, str) or not source.lower().startswith("data:image/"):
        return None
    _, images, _ = extract_content({"choices": [{"message": {"content": [{"type": "image_url", "image_url": source}]}}]}, "chat")
    return images[0] if images and images[0]["src"].startswith("data:") else None


class _ArtworkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.stack, self.blocked = [], [], []
        self.style, self.nodes = None, 0

    def handle_starttag(self, tag, attributes):
        self.nodes += 1
        if self.nodes > 8000:
            raise ValueError("Too many artwork elements")
        if self.blocked:
            if tag not in SOURCE_VOID_TAGS:
                self.blocked.append(tag)
            return
        if tag in DOCUMENT_TAGS:
            return
        if tag not in ALLOWED_TAGS:
            if tag not in SOURCE_VOID_TAGS:
                self.blocked.append(tag)
            return
        if tag == "style":
            self.style = []
            return
        attrs = dict(attributes)
        if tag in ANIMATION_TAGS:
            target = attrs.get("attributename", "")
            if target not in ANIMATED_ATTRIBUTES or (tag == "animatetransform" and target != "transform"):
                self.blocked.append(tag)
                return
        safe_attrs = []
        for name, value in attributes:
            if value is None or len(value) > MAX_HTML_BYTES or CONTROL.search(value) or name.startswith("on"):
                continue
            rendered_name, safe = name, None
            if name in {"id", "class", "title", "role", "aria-label", "aria-hidden"}:
                safe = value[:1024]
            elif name == "style":
                safe = _declarations(value)
            elif tag == "img" and name == "src":
                image = _inline_image(value)
                safe = image["src"] if image else None
            elif tag == "img" and name == "alt":
                safe = value[:1024]
            elif name in {"width", "height", "colspan", "rowspan"} and tag not in SVG_NAMES:
                safe = value if re.fullmatch(r"\d{1,5}(?:\.\d+)?%?", value) else None
            elif tag in SVG_NAMES and name in {"href", "xlink:href"}:
                safe = value if LOCAL_REF.fullmatch(value) else None
            elif tag == "svg" and name == "xmlns":
                safe = value if value == "http://www.w3.org/2000/svg" else None
            elif tag == "svg" and name == "xmlns:xlink":
                safe = value if value == "http://www.w3.org/1999/xlink" else None
            elif tag in SVG_NAMES and name in SVG_ATTRIBUTES:
                rendered_name = SVG_ATTRIBUTES[name]
                if tag in ANIMATION_TAGS and name in ANIMATION_FIELDS:
                    if name in {"begin", "end", "dur", "min", "max", "repeatdur"}:
                        safe = value if TIMING.fullmatch(value) else None
                    elif name == "attributename":
                        safe = value
                    elif name == "values":
                        values = [_safe_css_value(part) for part in value.split(";")]
                        safe = ";".join(values) if values and all(part is not None for part in values) else None
                    else:
                        safe = _safe_css_value(value)
                else:
                    safe = _safe_css_value(value)
            if safe is not None and safe != "":
                safe_attrs.append(rendered_name + '="' + html.escape(safe, quote=True) + '"')
        if tag == "img" and not any(value.startswith('src="') for value in safe_attrs):
            return
        name = SVG_NAMES.get(tag, tag)
        self.output.append("<" + name + (" " + " ".join(safe_attrs) if safe_attrs else "") + ">")
        if tag not in VOID_TAGS:
            if len(self.stack) >= 128:
                raise ValueError("Artwork nesting is too deep")
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in SOURCE_VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag in self.blocked:
                del self.blocked[len(self.blocked) - 1 - self.blocked[::-1].index(tag):]
            return
        if tag == "style" and self.style is not None:
            safe = _stylesheet("".join(self.style))
            if safe:
                self.output.append("<style>" + safe + "</style>")
            self.style = None
            return
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            for opened in reversed(self.stack[index:]):
                self.output.append("</" + SVG_NAMES.get(opened, opened) + ">")
            del self.stack[index:]

    def handle_data(self, data):
        if self.blocked:
            return
        if self.style is not None:
            self.style.append(data)
        else:
            self.output.append(html.escape(data))

    def fragment(self):
        for tag in reversed(self.stack):
            self.output.append("</" + SVG_NAMES.get(tag, tag) + ">")
        return "".join(self.output).strip()


def _artwork_source(text):
    source = text.strip().lstrip("\ufeff")
    fences = re.finditer(r"```[ \t]*(?:html|svg|xml)?[ \t]*\r?\n(.*?)\r?\n```", source, re.I | re.S)
    for match in fences:
        candidate = match.group(1).strip()
        if _looks_like_html(candidate):
            return candidate
    return source if _looks_like_html(source) else None


def _looks_like_html(source):
    source = re.sub(r"\A\s*(?:(?:<!--.*?-->|<!doctype\s+html[^>]*>)\s*)*", "", source, flags=re.I | re.S)
    match = re.match(r"<([A-Za-z][A-Za-z0-9]*)\b", source)
    return bool(match and match.group(1).lower() in ALLOWED_TAGS | DOCUMENT_TAGS)


def sanitize_artwork(text):
    """Return a static fragment, or None when the output is ordinary text.

    Limits apply before and after parsing. Invalid/oversized artwork raises
    ValueError; it must not fall back to being rendered as unsanitized HTML.
    """
    if not isinstance(text, str):
        raise ValueError("Artwork must be text")
    source = _artwork_source(text)
    if source is None:
        return None
    if len(source.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("Artwork exceeds the byte limit")
    parser = _ArtworkParser()
    parser.feed(source)
    parser.close()
    content = parser.fragment()
    if not content or len(content.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("Artwork is empty or exceeds the byte limit")
    return content


def _raw_text(payload, api_format):
    texts = []

    def parts(content):
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"text", "output_text"}:
                    if not isinstance(part.get("text"), str):
                        raise ValueError("Invalid model text")
                    texts.append(part["text"])
        elif content is not None:
            raise ValueError("Invalid model content")

    if api_format == "chat":
        parts(payload["choices"][0]["message"].get("content"))
    else:
        for item in payload["output"]:
            if item.get("type") not in {"reasoning", "function_call", "image_generation_call"}:
                parts(item.get("content"))
    return "".join(texts)


def _result(payload, api_format):
    try:
        text = _raw_text(payload, api_format)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise GenerationFailure("response_format") from None
    try:
        fragment = sanitize_artwork(text)
        if fragment is not None:
            result = {"kind": "html", "content": fragment, "mime": "text/html"}
            title = _artwork_title(fragment)
            if title:
                result["title"] = title
            return result
    except (ValueError, TypeError, RecursionError):
        raise GenerationFailure("artwork_invalid") from None
    try:
        cleaned_text, media, _ = extract_content(payload, api_format, [MAX_IMAGE_BYTES])
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise GenerationFailure("response_format") from None
    for image in media:
        if image["src"].startswith("data:"):
            return {"kind": "image", "content": image["src"], "mime": image["mime"]}
    if not cleaned_text.strip():
        raise GenerationFailure("empty_output")
    return {"kind": "text", "content": cleaned_text.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore"), "mime": "text/plain"}


def _artwork_title(fragment):
    class TitleParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.capture, self.parts = None, []
            self.title, self.heading = "", ""

        def handle_starttag(self, tag, attrs):
            if self.capture is None and (tag == "title" or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}):
                self.capture, self.parts = tag, []

        def handle_data(self, data):
            if self.capture:
                self.parts.append(data)

        def handle_endtag(self, tag):
            if tag == self.capture:
                value = re.sub(r"\s+", " ", CONTROL.sub("", "".join(self.parts))).strip()[:100]
                if tag == "title" and not self.title:
                    self.title = value
                elif tag != "title" and not self.heading:
                    self.heading = value
                self.capture = None

    parser = TitleParser()
    parser.feed(fragment)
    parser.close()
    return parser.title or parser.heading


async def generate_artwork(config, request, client=None):
    """Generate one artwork. No retries, redirects, tools, or external fetches.

    request supplies model, api_format (chat/responses), api_key and prompt.
    Optional reasoning_effort uses the standard gateway field for that protocol.
    The provider may map, ignore or reject explicit values. Default omits it.
    Neither the management key nor the helper session is used for model calls.
    """
    prompt, model, api_format, key = request.prompt, request.model, request.api_format, request.api_key
    effort = getattr(request, "reasoning_effort", "default")
    if (not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES
            or not isinstance(model, str) or not model.strip() or len(model) > 256
            or not isinstance(api_format, str) or api_format not in {"chat", "responses"} or not isinstance(key, str) or not key.strip()):
        raise GalleryGenerationError("作品提示词或模型配置无效")
    if not isinstance(effort, str) or effort not in REASONING_EFFORTS:
        raise GalleryGenerationError("思考力度配置无效")
    timeout = getattr(request, "timeout_seconds", None)
    if timeout is None:
        timeout = getattr(config, "gallery_timeout_seconds", GENERATION_TIMEOUT)
    if type(timeout) is not int or not 30 <= timeout <= 900:
        raise GalleryGenerationError("作品生成等待时限配置无效")
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    started, response = time.monotonic(), None
    body = {"model": model, "stream": False}
    if api_format == "chat":
        path = "/v1/chat/completions"
        body.update(messages=[{"role": "user", "content": prompt}], max_tokens=8192)
        if effort != "default":
            body["reasoning_effort"] = effort
    else:
        path = "/v1/responses"
        body.update(input=prompt, max_output_tokens=8192)
        if effort != "default":
            body["reasoning"] = {"effort": effort}
    try:
        async with asyncio.timeout(timeout):
            try:
                outgoing = httpx.Request("POST", config.sub2api_url.rstrip("/") + path,
                    headers={"Authorization": "Bearer " + key, "Accept": "application/json", "Accept-Encoding": "identity"},
                    json=body, extensions={"timeout": {"connect": 10, "read": timeout, "write": 15, "pool": 10}})
            except (ValueError, httpx.InvalidURL):
                raise GenerationFailure("request_invalid") from None
            response = await client.send(outgoing, stream=True, auth=None, follow_redirects=False)
            if effort != "default" and response.status_code in {400, 422}:
                raise UnsupportedReasoningEffort()
            response.raise_for_status()
            if response.headers.get("content-encoding", "").strip().lower() not in {"", "identity"}:
                raise GenerationFailure("response_format")
            mime = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
            limit = MAX_IMAGE_BYTES if mime.startswith("image/") else MAX_RESPONSE_BYTES
            raw = await _read_body(response, limit, [limit])
            if mime.startswith("image/"):
                image = _inline_image("data:" + mime + ";base64," + base64.b64encode(raw).decode("ascii"))
                if not image:
                    raise GenerationFailure("artwork_invalid")
                result = {"kind": "image", "content": image["src"], "mime": image["mime"]}
            else:
                result = _result(json.loads(raw), api_format)
            result["latency_ms"] = round((time.monotonic() - started) * 1000)
            return result
    except (httpx.TimeoutException, TimeoutError):
        raise GenerationFailure("timeout") from None
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        code = {401: "authentication", 403: "authentication", 429: "rate_limited", 503: "unavailable"}.get(status)
        if code is None:
            code = "request_rejected" if status in {400, 404, 405, 422} else "gateway"
        raise GenerationFailure(code) from None
    except httpx.HTTPError:
        raise GenerationFailure("connection") from None
    except _BodyLimit:
        raise GenerationFailure("response_too_large") from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise GenerationFailure("response_format") from None
    finally:
        if response is not None:
            await response.aclose()
        if own:
            await client.aclose()
