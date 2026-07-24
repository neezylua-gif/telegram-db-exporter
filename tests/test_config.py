from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tg_parser.config import Settings, StorageSettings


TG_NAMES = {
    "TG_API_ID",
    "TG_API_HASH",
    "TG_PHONE",
    "TG_OUTPUT_DIR",
    "TG_SESSION",
    "TG_MEDIA_WORKERS",
    "TG_DB_BATCH_SIZE",
    "TG_MAX_MEDIA_SIZE_MB",
    "TG_FLOOD_SLEEP_THRESHOLD",
}


class ConfigTests(unittest.TestCase):
    def clean_environment(self) -> dict[str, str]:
        return {key: value for key, value in os.environ.items() if key not in TG_NAMES}

    def test_export_storage_does_not_require_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.clean_environment()
            environment["TG_OUTPUT_DIR"] = temp
            with patch.dict(os.environ, environment, clear=True):
                settings = StorageSettings.from_env(None)
            self.assertEqual(settings.database_path, Path(temp) / "archive.sqlite3")

    def test_invalid_integer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.clean_environment()
            environment.update({
                "TG_API_ID": "123",
                "TG_API_HASH": "hash",
                "TG_OUTPUT_DIR": temp,
                "TG_MEDIA_WORKERS": "many",
            })
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(ValueError, "TG_MEDIA_WORKERS"):
                    Settings.from_env(None)

    def test_session_directory_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            environment = self.clean_environment()
            environment.update({
                "TG_API_ID": "123",
                "TG_API_HASH": "hash",
                "TG_OUTPUT_DIR": temp,
            })
            with patch.dict(os.environ, environment, clear=True):
                settings = Settings.from_env(None)
            self.assertTrue(settings.session.parent.is_dir())


if __name__ == "__main__":
    unittest.main()
