"""Small fixed-host GitHub client; credentials are read only from the environment."""
from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str = "GitHub API request failed"):
        self.status = status
        super().__init__(f"{message} (HTTP {status})")


class GitHub:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GH_TOKEN", "")
        if not self.token or "\n" in self.token or "\r" in self.token:
            raise GitHubError(0, "GH_TOKEN is required for the workflow")
        self.opener = build_opener(NoRedirect())

    def request(self, method: str, path: str, data=None):
        if path.startswith("/"):
            path = path[1:]
        if not path or path.startswith("/") or "://" in path or ".." in urlsplit(path).path.split("/"):
            raise ValueError("GitHub requests must use a fixed API-relative path")
        request = Request("https://api.github.com/" + path, method=method,
                          data=None if data is None else json.dumps(data).encode(),
                          headers={"Authorization": "Bearer " + self.token,
                                   "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "sub2api-helper-release-sync"})
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else None
        except HTTPError as exc:
            raise GitHubError(exc.code) from None
        except (URLError, OSError, ValueError):
            raise GitHubError(0, "GitHub connection or JSON response failed") from None

    def paginate(self, path: str):
        parsed = urlsplit(path)
        params = dict(parse_qsl(parsed.query))
        result = []
        for page in range(1, 1001):
            query = urlencode({**params, "per_page": 100, "page": page})
            endpoint = urlunsplit(("", "", parsed.path, query, ""))
            batch = self.request("GET", endpoint)
            if not isinstance(batch, list):
                raise GitHubError(0, "Expected a complete GitHub list response")
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise GitHubError(0, "GitHub pagination exceeded the safety limit; refusing a truncated list")
