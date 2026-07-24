from __future__ import annotations

import unittest

from tg_parser.media import DOWNLOADABLE_MEDIA, parse_media_selection


class MediaSelectionTests(unittest.TestCase):
    def test_all_and_none(self) -> None:
        self.assertIsNone(parse_media_selection("all"))
        self.assertEqual(parse_media_selection("none"), frozenset())

    def test_list_is_normalized(self) -> None:
        self.assertEqual(
            parse_media_selection(" PHOTO,document,photo "),
            frozenset({"photo", "document"}),
        )

    def test_unknown_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "phoot"):
            parse_media_selection("photo,phoot")

    def test_declared_types_are_nonempty(self) -> None:
        self.assertIn("document", DOWNLOADABLE_MEDIA)


if __name__ == "__main__":
    unittest.main()
