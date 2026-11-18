"""A fixed-upstream reverse proxy for the native sub2api administration UI.

Browser credentials are forwarded only as supplied by the browser. The separate
helper administrator client and its API key are never used here. Forwarded IP
headers are intentionally discarded: the upstream sees the helper's address.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import quote, urlsplit, urlunsplit
import zlib

import anyio
import httpx
from starlette.responses import Response, StreamingResponse
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect

from .upstream import UpstreamError


MAX_HTML_BYTES = 4 * 1024 * 1024
HOP_HEADERS = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
               b"te", b"trailer", b"transfer-encoding", b"upgrade"}
WS_HEADERS = {b"sec-websocket-key", b"sec-websocket-accept", b"sec-websocket-version",
              b"sec-websocket-protocol", b"sec-websocket-extensions"}
VALIDATORS = {b"content-length", b"content-encoding", b"etag", b"last-modified",
              b"content-md5", b"digest", b"content-digest", b"accept-ranges"}
IP_HEADERS = {b"forwarded", b"x-real-ip", b"x-client-ip", b"client-ip",
              b"true-client-ip", b"cf-connecting-ip", b"fastly-client-ip"}


def filtered_headers(raw_headers, *, request=False, websocket=False):
    nominated = set()
    for name, value in raw_headers:
        if name.lower() == b"connection":
            nominated.update(item.strip().lower() for item in value.split(b",") if item.strip())
    blocked = HOP_HEADERS | nominated | (WS_HEADERS if websocket else set())
    result = []
    for name, value in raw_headers:
        lower = name.lower()
        if lower in blocked:
            continue
        if request and (lower == b"host" or lower in IP_HEADERS or lower.startswith(b"x-forwarded") or lower.startswith(b"x-helper")):
            continue
        result.append((lower, value))
    return result


def _trusted_base(provider):
    value = provider()
    if not isinstance(value, str):
        raise ValueError("Native upstream is not configured")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Native upstream must be a fixed HTTP(S) service URL")
    # Evaluating port also rejects malformed ports without echoing the URL.
    parsed.port
    return str(httpx.URL(value)).rstrip("/")


def _target(base, request, path):
    supplied = "/" + path.lstrip("/")
    raw_path = request.scope.get("raw_path")
    if raw_path and request.scope.get("path") == supplied:
        incoming = raw_path.decode("ascii")
    else:
        incoming = quote(supplied, safe="/:@!$&'()*+,;=-._~")
    return httpx.URL(base + incoming).copy_with(query=request.scope.get("query_string", b""))


def _origin(parsed):
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


def _public_origin(provider, request):
    configured = provider() if provider else ""
    if configured:
        parsed = urlsplit(str(httpx.URL(_trusted_base(lambda: configured))))
    else:
        parsed = urlsplit(str(request.url))
        if parsed.scheme in {"ws", "wss"}:
            parsed = parsed._replace(scheme="https" if parsed.scheme == "wss" else "http")
        parsed = urlsplit(str(httpx.URL(urlunsplit(parsed))))
    return parsed._replace(path="", query="", fragment="")


def rewrite_location(location, base, request, public_url=None):
    try:
        value, upstream = urlsplit(location), urlsplit(base)
    except ValueError:
        return location
    if not value.netloc:
        return location
    if not value.scheme:
        value = value._replace(scheme=upstream.scheme)
    try:
        if _origin(value) != _origin(upstream):
            return location
    except ValueError:
        return location
    public = urlsplit(public_url or str(request.url))
    path = value.path
    prefix = upstream.path.rstrip("/")
    if prefix and (path == prefix or path.startswith(prefix + "/")):
        path = path[len(prefix):] or "/"
    return urlunsplit((public.scheme, public.netloc, path, value.query, value.fragment))


class _Nonce(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.value = None
        self.closing = {}
        self.lines = [0] + [match.end() for match in re.finditer("\n", html)]

    def handle_starttag(self, tag, attrs):
        if self.value is None and tag == "script":
            nonce = dict(attrs).get("nonce")
            if nonce:
                self.value = nonce

    def handle_endtag(self, tag):
        if tag in {"head", "body"} and tag not in self.closing:
            line, column = self.getpos()
            self.closing[tag] = self.lines[line - 1] + column


def _decode_html(raw, encoding):
    value = raw
    for coding in reversed([part.strip().lower() for part in encoding.split(",") if part.strip()]):
        if coding == "identity":
            continue
        if coding not in {"gzip", "deflate"}:
            return None  # Preserve an unsupported encoding unchanged.
        modes = [16 + zlib.MAX_WBITS] if coding == "gzip" else [zlib.MAX_WBITS, -zlib.MAX_WBITS]
        decoded = None
        for mode in modes:
            try:
                decoder = zlib.decompressobj(mode)
                candidate = decoder.decompress(value, MAX_HTML_BYTES + 1)
                if len(candidate) > MAX_HTML_BYTES or not decoder.eof or decoder.unused_data:
                    return None
                decoded = candidate
                break
            except zlib.error:
                continue
        if decoded is None:
            return None
        value = decoded
    return value if len(value) <= MAX_HTML_BYTES else None


def inject_html(raw, content_type, encoding, headers):
    decoded = _decode_html(raw, encoding)
    if decoded is None:
        return None
    charset = re.search(r"charset\s*=\s*[\"']?([^\s;\"']+)", content_type, re.I)
    charset = charset.group(1) if charset else "utf-8"
    try:
        html = decoded.decode(charset)
    except (LookupError, UnicodeError):
        return None
    if not html or "data-sub2api-helper-native=" in html:
        return None
    parser = _Nonce(html)
    parser.feed(html)
    nonce = parser.value
    if not nonce:
        for name, value in headers:
            if name.lower() == b"content-security-policy":
                match = re.search(r"'nonce-([^']+)'", value.decode("latin-1"))
                if match:
                    nonce = match.group(1)
                    break
    nonce_attr = ' nonce="' + escape(nonce, quote=True) + '"' if nonce else ""
    additions = ('<link rel="stylesheet" href="/_helper/native.css" data-sub2api-helper-native="style"' + nonce_attr + '/>'
                 '<script defer="defer" src="/_helper/native.js" data-sub2api-helper-native="script"' + nonce_attr + '></script>')
    index = parser.closing.get("head", parser.closing.get("body", 0))
    try:
        return (html[:index] + additions + html[index:]).encode(charset)
    except (LookupError, UnicodeError):
        return None


async def _raw_chunks(response):
    # Mock transports may return an already buffered response. Real streaming
    # transports remain unconsumed and always use the raw iterator below.
    if response.is_stream_consumed:
        yield response.content
        return
    async for chunk in response.aiter_raw():
        yield chunk


async def _close_response(response):
    # Response cleanup must survive cancellation of a downstream ASGI request.
    with anyio.CancelScope(shield=True):
        await response.aclose()


async def _stream(response, iterator, prefix=()):
    try:
        for chunk in prefix:
            yield chunk
        async for chunk in iterator:
            yield chunk
    except httpx.HTTPError:
        raise RuntimeError("Upstream stream interrupted") from None
    finally:
        await _close_response(response)


class _Streaming(StreamingResponse):
    def __init__(self, response, iterator, prefix=()):
        self.upstream = response
        super().__init__(_stream(response, iterator, prefix), status_code=response.status_code)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await _close_response(self.upstream)


class _NoRedirectConnect(connect):
    def process_redirect(self, exc):
        return exc


class NativeProxy:
    def __init__(self, base_url_provider, client=None, public_url_provider=None):
        self.base_url_provider = base_url_provider
        self.public_url_provider = public_url_provider
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(None, connect=10, write=30, pool=10),
        )

    async def forward(self, request, path):
        request.state.helper_proxied = True
        try:
            base = _trusted_base(self.base_url_provider)
            public = _public_origin(self.public_url_provider, request)
            headers = filtered_headers(request.headers.raw, request=True)
            headers.append((b"x-forwarded-proto", public.scheme.encode("ascii")))
            if "text/html" in request.headers.get("accept", "").lower():
                # An old upstream 304 could reuse an unaugmented cached index.
                headers = [(name, value) for name, value in headers if name not in {b"accept-encoding", b"if-none-match", b"if-modified-since"}]
                headers.append((b"accept-encoding", b"identity"))
            outgoing = httpx.Request(request.method, _target(base, request, path), headers=headers, content=request.stream())
            upstream = await self.client.send(outgoing, stream=True, auth=None, follow_redirects=False)
        except (httpx.HTTPError, ValueError, UnicodeError):
            return Response("Upstream service unavailable", status_code=502, media_type="text/plain")
        try:
            return await self._response(request, upstream, base, public)
        except BaseException:
            await _close_response(upstream)
            raise

    async def _response(self, request, upstream, base, public):
        raw_headers = filtered_headers(upstream.headers.raw)
        raw_headers = [(name, rewrite_location(value.decode("latin-1"), base, request, urlunsplit(public)).encode("latin-1") if name == b"location" else value) for name, value in raw_headers]
        content_type = upstream.headers.get("content-type", "")
        buffered = upstream.is_stream_consumed
        encoding = upstream.headers.get("content-encoding", "")
        buffered_decoded = buffered and bool(encoding) and all(coding.strip().lower() in {"gzip", "deflate", "identity"} for coding in encoding.split(","))
        if buffered_decoded:
            raw_headers = [(name, value) for name, value in raw_headers if name not in VALIDATORS]
            raw_headers.append((b"content-length", str(len(upstream.content)).encode()))
        if request.method == "HEAD" or upstream.status_code in {204, 205, 304}:
            await _close_response(upstream)
            result = Response(b"", status_code=upstream.status_code)
            result.raw_headers = raw_headers
            return result
        iterator = _raw_chunks(upstream)
        rewrite = upstream.is_success and upstream.status_code != 206 and content_type.split(";", 1)[0].strip().lower() in {"text/html", "application/xhtml+xml"}
        if rewrite:
            chunks, length = [], 0
            try:
                async for chunk in iterator:
                    if length + len(chunk) > MAX_HTML_BYTES:
                        result = _Streaming(upstream, iterator, [*chunks, chunk])
                        result.raw_headers = raw_headers
                        return result
                    chunks.append(chunk)
                    length += len(chunk)
                raw = b"".join(chunks)
                # Already-buffered httpx responses have already been decoded.
                rewritten = inject_html(raw, content_type, "" if buffered_decoded else encoding, raw_headers)
            except httpx.HTTPError:
                await _close_response(upstream)
                return Response("Upstream HTML could not be read", status_code=502, media_type="text/plain")
            await _close_response(upstream)
            result = Response(rewritten if rewritten is not None else raw, status_code=upstream.status_code)
            if rewritten is not None:
                raw_headers = [(name, value) for name, value in raw_headers if name not in VALIDATORS]
                raw_headers.append((b"content-length", str(len(rewritten)).encode()))
            result.raw_headers = raw_headers
            return result
        result = _Streaming(upstream, iterator)
        result.raw_headers = raw_headers
        return result

    async def profile(self, token, user_agent):
        try:
            base = _trusted_base(self.base_url_provider)
            outgoing = httpx.Request("GET", base + "/api/v1/user/profile",
                                     headers={"Authorization": "Bearer " + token, "User-Agent": user_agent, "Accept": "application/json"},
                                     extensions={"timeout": httpx.Timeout(20, connect=10).as_dict()})
            response = await self.client.send(outgoing, auth=None, follow_redirects=False)
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise UpstreamError("无法验证 sub2api 管理员登录身份", 502) from None
        try:
            if not response.is_success:
                raise UpstreamError("sub2api 登录身份验证失败", response.status_code if response.status_code in {401, 403} else 502)
            payload = response.json()
            if not isinstance(payload, dict) or type(payload.get("code")) is not int or payload["code"] != 0 or not isinstance(payload.get("data"), dict):
                raise ValueError("Invalid profile envelope")
            return payload["data"]
        except (ValueError, UnicodeError):
            raise UpstreamError("sub2api 用户身份响应不兼容", 502) from None
        finally:
            await _close_response(response)

    async def websocket(self, websocket, path):
        websocket.state.helper_proxied = True
        try:
            base = _trusted_base(self.base_url_provider)
            target = str(_target(base, websocket, path))
            target = ("wss" if target.startswith("https:") else "ws") + target[target.index(":"):]
            headers = filtered_headers(websocket.headers.raw, request=True, websocket=True)
            public = _public_origin(self.public_url_provider, websocket)
            upstream_origin = urlsplit(base)
            updated_headers = []
            for name, value in headers:
                if name == b"origin":
                    allowed = False
                    with suppress(ValueError, UnicodeError):
                        origin = urlsplit(value.decode("latin-1"))
                        if (origin.scheme in {"http", "https"} and not origin.username and not origin.password
                                and origin.path in {"", "/"} and not origin.query and not origin.fragment and _origin(origin) == _origin(public)):
                            allowed = True
                    if not allowed:
                        await websocket.close(code=1008)
                        return
                    value = urlunsplit((upstream_origin.scheme, upstream_origin.netloc, "", "", "")).encode("latin-1")
                updated_headers.append((name, value))
            updated_headers.append((b"x-forwarded-proto", public.scheme.encode("ascii")))
            async with _NoRedirectConnect(
                target, additional_headers=[(name.decode("latin-1"), value.decode("latin-1")) for name, value in updated_headers],
                subprotocols=websocket.scope.get("subprotocols") or None,
                user_agent_header=None, proxy=None, max_size=None, open_timeout=10, close_timeout=5,
            ) as upstream:
                cookies = [(b"set-cookie", value.encode("latin-1")) for name, value in upstream.response.headers.raw_items() if name.lower() == "set-cookie"]
                await websocket.accept(subprotocol=upstream.subprotocol, headers=cookies)

                async def browser_to_upstream():
                    while True:
                        event = await websocket.receive()
                        if event["type"] == "websocket.disconnect":
                            await upstream.close(code=1000)
                            return
                        if event.get("text") is not None:
                            await upstream.send(event["text"])
                        elif event.get("bytes") is not None:
                            await upstream.send(event["bytes"])

                async def upstream_to_browser():
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)

                tasks = [asyncio.create_task(browser_to_upstream()), asyncio.create_task(upstream_to_browser())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close(code=1000 if upstream.close_code in {None, 1000, 1001} else 1011)
        except (Exception, WebSocketDisconnect):
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close(code=1011)

    async def close(self):
        if self._owns_client:
            await self.client.aclose()
