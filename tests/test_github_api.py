"""Release transport checks without network calls or credentials."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    from github_api import GitHub, GitHubError, NoRedirect
finally:
    sys.path.pop(0)


class GitHubClientTests(unittest.TestCase):
    def test_missing_or_invalid_token_has_no_value_in_error(self):
        with patch.dict("os.environ", {}, clear=True):
            for token in (None, "secret\nheader", "secret\rheader"):
                with self.subTest(token=token), self.assertRaises(GitHubError) as caught:
                    GitHub(token)
                self.assertNotIn("secret", str(caught.exception))

    def test_authorized_fixed_host_json_request(self):
        api = GitHub("test-fixture-secret")
        response = Mock()
        response.read.return_value = b'{"id":42}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        api.opener = Mock()
        api.opener.open.return_value = response
        self.assertEqual(api.request("POST", "/repos/example/helper/releases", {"tag_name": "v1.2.3"}), {"id": 42})
        request = api.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/example/helper/releases")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-fixture-secret")
        self.assertEqual(request.data, b'{"tag_name": "v1.2.3"}')

    def test_redirects_are_never_followed_and_errors_never_echo_body(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "Moved", {}, "https://other.example/steal"))
        for status in (302, 403, 500):
            api = GitHub("test-fixture-secret")
            api.opener = Mock()
            api.opener.open.side_effect = HTTPError("https://api.github.com/test", status, "bad", {}, io.BytesIO(b'test-fixture-secret'))
            with self.subTest(status=status), self.assertRaises(GitHubError) as caught:
                api.request("GET", "repos/example/helper")
            self.assertEqual(caught.exception.status, status)
            self.assertNotIn("test-fixture-secret", str(caught.exception))
            api.opener.open.assert_called_once()

    def test_external_and_parent_paths_rejected_before_transport(self):
        api = GitHub("test-fixture-secret")
        api.opener = Mock()
        for path in ("https://other.example/path", "//other.example/path", "../tokens"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                api.request("GET", path)
        api.opener.open.assert_not_called()

    def test_pagination_rejects_incomplete_shapes(self):
        api = GitHub("test-fixture-secret")
        api.request = Mock(return_value={"message": "not a list"})
        with self.assertRaises(GitHubError):
            api.paginate("repos/example/helper/releases")
