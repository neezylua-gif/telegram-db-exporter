from __future__ import annotations

import unittest

from tg_parser.utils import extract_urls, safe_extension, sanitize_component


class UtilsTests(unittest.TestCase):
    def test_reserved_windows_name_is_prefixed(self) -> None:
        self.assertEqual(sanitize_component("CON"), "_CON")

    def test_unsafe_extension_is_rejected(self) -> None:
        self.assertEqual(safe_extension("payload.very-long-extension"), "")
        self.assertEqual(safe_extension("document.PDF"), ".pdf")

    def test_urls_are_deduplicated_without_losing_fragments(self) -> None:
        urls = extract_urls(
            "https://example.com/a#one https://example.com/a#one. "
            "https://example.com/a#two"
        )
        self.assertEqual(
            urls,
            ["https://example.com/a#one", "https://example.com/a#two"],
        )


if __name__ == "__main__":
    unittest.main()
