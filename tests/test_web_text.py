import unittest
from unittest.mock import patch

from skill_gather.web_text import WebTextError, _PublicRedirectHandler, _assert_public_network_url


class PublicWebSourceTests(unittest.TestCase):
    def test_rejects_loopback_and_private_literal_urls(self):
        for url in (
            "http://127.0.0.1:8766/",
            "http://10.0.0.5/metadata",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data/",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(WebTextError, "私有网络"):
                _assert_public_network_url(url)

    def test_rejects_hostname_that_resolves_to_private_network(self):
        with patch(
            "skill_gather.web_text.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.1.25", 0))],
        ):
            with self.assertRaisesRegex(WebTextError, "私有网络"):
                _assert_public_network_url("https://untrusted.example/docs")

    def test_rejects_malformed_host_or_port_with_domain_error(self):
        for url in ("http://[::1", "https://example.com:70000/docs"):
            with self.subTest(url=url), self.assertRaisesRegex(WebTextError, "URL 格式无效"):
                _assert_public_network_url(url)

    def test_allows_hostname_with_global_resolution(self):
        with patch(
            "skill_gather.web_text.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            _assert_public_network_url("https://example.com/docs")

    def test_redirect_handler_rejects_private_target_before_following(self):
        handler = _PublicRedirectHandler()
        with self.assertRaisesRegex(WebTextError, "私有网络"):
            handler.redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "http://127.0.0.1:8766/private",
            )


if __name__ == "__main__":
    unittest.main()
