from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from helpers import chat_record, message_record

from tg_parser.db import ArchiveDatabase
from tg_parser.exporter import export_messages


class ExporterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "archive.sqlite3"
        self.database = ArchiveDatabase(self.database_path)
        await self.database.open()
        await self.database.upsert_chat(chat_record())
        await self.database.save_batch(
            [message_record(text='=HYPERLINK("https://example.invalid")')],
            [],
            [],
        )

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self.temp.cleanup()

    async def test_export_cannot_overwrite_database(self) -> None:
        with self.assertRaises(ValueError):
            await export_messages(
                self.database,
                self.database_path,
                "jsonl",
            )
        self.assertEqual(
            await self.database.fetch_scalar("SELECT COUNT(*) FROM messages"),
            1,
        )

    async def test_csv_is_formula_safe_by_default(self) -> None:
        output = self.root / "messages.csv"
        count = await export_messages(self.database, output, "csv")
        self.assertEqual(count, 1)
        with output.open("r", encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertTrue(row["text"].startswith("'="))

    async def test_export_replaces_existing_file_after_success(self) -> None:
        output = self.root / "messages.jsonl"
        output.write_text("old", encoding="utf-8")
        count = await export_messages(self.database, output, "jsonl")
        self.assertEqual(count, 1)
        self.assertNotEqual(output.read_text(encoding="utf-8"), "old")
        self.assertFalse(any(self.root.glob(".messages.jsonl.*.tmp")))


if __name__ == "__main__":
    unittest.main()
