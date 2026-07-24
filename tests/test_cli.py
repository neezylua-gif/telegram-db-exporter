from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tg_parser.db import ArchiveDatabase

from helpers import chat_record, message_record


class CliTests(unittest.TestCase):
    def test_export_command_works_without_importing_telethon(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"

            async def seed() -> None:
                async with ArchiveDatabase(data / "archive.sqlite3") as database:
                    await database.upsert_chat(chat_record())
                    await database.save_batch([message_record()], [], [])

            asyncio.run(seed())
            output = root / "messages.jsonl"
            environment = os.environ.copy()
            environment["TG_OUTPUT_DIR"] = str(data)
            result = subprocess.run(
                [
                    sys.executable,
                    "run.py",
                    "export",
                    "--format",
                    "jsonl",
                    "--output",
                    str(output),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertIn('"message_id": 1', output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
