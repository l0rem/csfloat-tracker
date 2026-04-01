from __future__ import annotations

import unittest

from csfloat_monitor.proxy import normalize_proxy_url, redact_proxy_url


class ProxyUtilsTests(unittest.TestCase):
    def test_normalizes_host_port_user_pass(self) -> None:
        raw = "ultra.marsproxies.com:44443:mr10074TW8r:MXGNU4y6gd_country-de"
        normalized = normalize_proxy_url(raw)
        self.assertEqual(
            "http://mr10074TW8r:MXGNU4y6gd_country-de@ultra.marsproxies.com:44443",
            normalized,
        )

    def test_normalizes_host_port(self) -> None:
        self.assertEqual("http://127.0.0.1:8080", normalize_proxy_url("127.0.0.1:8080"))

    def test_accepts_full_proxy_url(self) -> None:
        raw = "http://user:pass@host:1234"
        self.assertEqual(raw, normalize_proxy_url(raw))

    def test_redacts_proxy_url(self) -> None:
        redacted = redact_proxy_url("http://user:pass@host:1234")
        self.assertEqual("http://***:***@host:1234", redacted)

    def test_invalid_format_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_proxy_url("invalid-format")
