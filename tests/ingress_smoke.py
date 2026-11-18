"""Isolated real-nginx ingress check: python tests/ingress_smoke.py IMAGE.

Uses an already-built Helper image, nginx:alpine, a local fake upstream, and
temporary containers/network/config. It never contacts a real sub2api instance.
"""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


SHELL = b'<!doctype html><html><head><script type="module" src="/assets/index-fixture.js"></script></head><body><div id="app">Original SPA shell</div></body></html>'
ASSET = b"globalThis.upstreamAsset=true;"
NGINX_IMAGE = "nginx:alpine@sha256:fed7e14be83cbd2661a75cdc5b872d936cf155d46c327084d30b8df19ae88d72"


def fixture():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def reply(self, value, mime="application/json"):
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Fixture-Route", "original")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.headers.get("Upgrade", "").lower() == "websocket":
                accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                self.end_headers()
                frame = self.rfile.read(2)
                size = frame[1] & 127
                mask, value = self.rfile.read(4), self.rfile.read(size)
                value = bytes(char ^ mask[index % 4] for index, char in enumerate(value))
                self.wfile.write(bytes([0x81, len(value)]) + value)
                self.wfile.flush()
                self.close_connection = True
            elif self.path.startswith("/api/v1/admin/settings"):
                self.reply({"code": 0, "data": {"session_binding_enabled": False, "custom_menu_items": []}})
            elif self.path.startswith("/api/v1/"):
                self.reply({"code": 0, "data": {"items": [], "total": 0}, "method": "GET", "uri": self.path, "host": self.headers.get("Host")})
            elif self.path.startswith("/assets/"):
                self.reply(ASSET, "application/javascript")
            elif self.path.startswith("/not-a-spa"):
                self.reply(b"<html><body>Original non-SPA HTML</body></html>", "text/html")
            elif self.path == "/health":
                self.reply({"status": "ok"})
            else:
                self.reply(SHELL, "text/html; charset=utf-8")

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/responses?stream=1":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"data: first\n\n")
                self.wfile.flush()
                time.sleep(1)
                self.wfile.write(b"data: second\n\n")
                self.wfile.flush()
                self.close_connection = True
            else:
                self.reply({"method": "POST", "uri": self.path, "host": self.headers.get("Host"), "body": raw.decode(), "admin_key": self.headers.get("x-api-key")})

    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


def main():
    root = Path(__file__).resolve().parents[1]
    image = sys.argv[1] if len(sys.argv) > 1 else "sub2api-helper:" + (root / "helper/VERSION").read_text().strip()
    suffix = uuid.uuid4().hex[:10]
    network, upstream, helper, nginx = ["helper-ingress-" + name + "-" + suffix for name in ("net", "upstream", "app", "nginx")]
    containers = []

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()

    def address(name):
        return json.loads(docker("inspect", name))[0]["NetworkSettings"]["Networks"][network]["IPAddress"]

    try:
        with tempfile.TemporaryDirectory(prefix="helper-ingress-") as directory:
            temp = Path(directory)
            docker("network", "create", network)
            docker("run", "-d", "--name", upstream, "--user", "0", "--network", network, "--network-alias", "upstream",
                   "-v", str(Path(__file__).resolve()) + ":/fixture.py:ro", image, "python", "/fixture.py", "--fixture")
            containers.append(upstream)

            def start_helper(enabled):
                docker("run", "-d", "--name", helper, "--network", network,
                       "-e", "SUB2API_URL=http://upstream:8000", "-e", "SUB2API_ADMIN_KEY=fixture-admin-key",
                       "-e", "SUB2API_PUBLIC_URL=http://ingress.invalid", "-e", "HELPER_PUBLIC_URL=http://ingress.invalid",
                       "-e", "HELPER_NATIVE_UI=" + str(enabled).lower(), "-e", "HELPER_LEGACY_ROUTES=false",
                       "-e", "NO_PROXY=upstream,localhost,127.0.0.1", "-e", "no_proxy=upstream,localhost,127.0.0.1", image)
                if helper not in containers:
                    containers.append(helper)
                for _ in range(75):
                    result = subprocess.run(["docker", "exec", helper, "python", "-c",
                        "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/_helper/healthz',timeout=1).read()"], capture_output=True)
                    if result.returncode == 0:
                        return
                    time.sleep(.2)
                raise AssertionError("Helper did not become healthy: " + docker("logs", helper))

            start_helper(False)
            template = (root / "deploy/nginx/sub2api-helper.conf").read_text()
            (temp / "nginx.conf").write_text("events {}\nhttp { include /etc/nginx/mime.types; include /fixture/ingress.conf; }\n")

            def configuration(enabled):
                config = template.replace("http://127.0.0.1:8080", "http://" + address(upstream) + ":8000")
                config = config.replace("http://127.0.0.1:18080", "http://" + address(helper) + ":8080")
                config = config.replace("set $helper_injection_enabled 0;", "set $helper_injection_enabled " + str(int(enabled)) + ";")
                (temp / "ingress.conf").write_text(config)

            configuration(False)
            docker("run", "-d", "--name", nginx, "--network", network, "-p", "127.0.0.1::80",
                   "-v", str(temp) + ":/fixture:ro", NGINX_IMAGE, "nginx", "-c", "/fixture/nginx.conf", "-g", "daemon off;")
            containers.append(nginx)
            port = int(json.loads(docker("inspect", nginx))[0]["NetworkSettings"]["Ports"]["80/tcp"][0]["HostPort"])
            base, host = "http://127.0.0.1:" + str(port), "127.0.0.1:" + str(port)

            def request(path, *, method="GET", data=None, accept="text/html", headers=None):
                value = urllib.request.Request(base + path, method=method, data=data, headers={"Accept": accept, **(headers or {})})
                return urllib.request.urlopen(value, timeout=15)

            for _ in range(50):
                try:
                    with request("/login") as response:
                        assert response.read() == SHELL
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise AssertionError("nginx did not become ready")

            def documents(injected):
                for path in ("/", "/login?next=%2Fadmin%2Faccounts", "/admin/accounts", "/admin/users", "/dashboard"):
                    with request(path) as response:
                        body = response.read()
                    assert b'Original SPA shell' in body
                    assert (b'/_helper/native.js' in body) == injected, (path, injected, body)
                with request("/assets/index-fixture.js") as response:
                    assert response.read() == ASSET, "Original assets were changed"

            def business():
                for path in ("/api/v1/admin/users?fixture=1", "/api/v1/auth/status"):
                    with request(path) as response:
                        body = json.load(response)
                    assert body["host"] == host and body["uri"] == path
                for path in ("/api/v1/auth/login", "/api/v1/auth/logout", "/responses?fixture=1", "/chat/completions", "/api/v3/contents/generations/tasks"):
                    with request(path, method="POST", data=b'{"fixture":true}') as response:
                        body = json.load(response)
                    assert body == {"method": "POST", "uri": path, "host": host, "body": '{"fixture":true}', "admin_key": None}, body
                started = time.monotonic()
                with request("/responses?stream=1", method="POST", data=b"{}", accept="text/event-stream") as response:
                    assert response.readline() == b"data: first\n"
                    assert time.monotonic() - started < .8, "SSE was buffered"
                    assert b"data: second" in response.read()
                with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                    key = base64.b64encode(b"fixture-16-bytes").decode()
                    connection.sendall((f"GET /responses HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode())
                    stream = connection.makefile("rb")
                    assert b"101" in stream.readline()
                    while stream.readline() != b"\r\n":
                        pass
                    connection.sendall(b"\x81\x84abcd" + bytes(value ^ b"abcd"[index % 4] for index, value in enumerate(b"echo")))
                    assert stream.read(6) == b"\x81\x04echo", "WebSocket was not forwarded directly"

            documents(False)
            business()
            configuration(True)
            docker("exec", nginx, "nginx", "-c", "/fixture/nginx.conf", "-t")
            docker("exec", nginx, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload")
            time.sleep(.3)
            documents(False)  # Helper native mode disabled: 404 -> original.
            docker("rm", "-fv", helper)
            start_helper(True)
            configuration(True)
            docker("exec", nginx, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload")
            time.sleep(.3)
            documents(True)
            for headers in ({"Sec-Fetch-Dest": "script"}, {"Sec-Fetch-Dest": "image"}, {"Sec-WebSocket-Key": "not-a-navigation"}):
                with request("/login", headers=headers) as response:
                    assert response.read() == SHELL, "Non-document request reached Helper"
            for accept in ("text/html;q=0", "text/html;q=0.0,application/xhtml+xml;q=1"):
                with request("/login", accept=accept) as response:
                    assert response.read() == SHELL, "Disabled HTML media range reached Helper"
            with request("/login", accept="application/xhtml+xml") as response:
                assert b"/_helper/native.js" in response.read()
            with request("/not-a-spa") as response:
                assert b"/_helper/native.js" not in response.read()
            business()
            # A running Helper is bypassed immediately after rollback reload.
            configuration(False)
            docker("exec", nginx, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload")
            time.sleep(.3)
            documents(False)
            # Failure with beta still selected must not affect original traffic.
            configuration(True)
            docker("exec", nginx, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload")
            docker("stop", "-t", "2", helper)
            time.sleep(.3)
            documents(False)
            business()
            # nginx also starts/reloads without a live Helper dependency.
            docker("exec", nginx, "nginx", "-c", "/fixture/nginx.conf", "-t")
            docker("exec", nginx, "nginx", "-c", "/fixture/nginx.conf", "-s", "reload")
            print("Ingress smoke passed: original routes independent; optional SPA injection; disabled/offline fail-open; direct auth/admin/model POST/SSE/WebSocket; rollback and assets.")
    except Exception:
        for name in containers:
            result = subprocess.run(["docker", "logs", "--tail", "35", name], text=True, capture_output=True)
            print(name + ":\n" + result.stdout + result.stderr, file=sys.stderr)
        raise
    finally:
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-fv", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


if __name__ == "__main__":
    fixture() if "--fixture" in sys.argv else main()
