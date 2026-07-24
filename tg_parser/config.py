from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _read_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} должен быть целым числом, получено {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} должен быть в диапазоне {minimum}..{maximum}, получено {value}"
        )
    return value


def _load_environment(env_file: str | Path | None) -> None:
    if env_file is None:
        return
    path = Path(env_file).expanduser()
    # Отсутствующий .env допустим: переменные могут быть заданы окружением.
    load_dotenv(dotenv_path=path, override=False)


def _make_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(stat.S_IRWXU)


@dataclass(slots=True, frozen=True)
class StorageSettings:
    output_dir: Path

    @property
    def database_path(self) -> Path:
        return self.output_dir / "archive.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.output_dir / "media"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> StorageSettings:
        _load_environment(env_file)
        output_raw = os.getenv("TG_OUTPUT_DIR", "parser_data").strip()
        if not output_raw:
            raise ValueError("TG_OUTPUT_DIR не может быть пустым")
        settings = cls(output_dir=Path(output_raw).expanduser())
        settings.prepare_directories()
        return settings

    def prepare_directories(self) -> None:
        _make_private_directory(self.output_dir)
        _make_private_directory(self.media_dir)


@dataclass(slots=True, frozen=True)
class Settings:
    api_id: int
    api_hash: str
    phone: str | None
    session: Path
    output_dir: Path
    media_workers: int
    db_batch_size: int
    max_media_size_mb: int
    flood_sleep_threshold: int

    @property
    def database_path(self) -> Path:
        return self.output_dir / "archive.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.output_dir / "media"

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        _load_environment(env_file)

        api_id_raw = os.getenv("TG_API_ID", "").strip()
        api_hash = os.getenv("TG_API_HASH", "").strip()
        phone = os.getenv("TG_PHONE", "").strip() or None

        if not api_id_raw.isdecimal() or int(api_id_raw) <= 0:
            raise ValueError("TG_API_ID должен быть положительным целым числом")
        if not api_hash:
            raise ValueError("TG_API_HASH не заполнен")

        output_raw = os.getenv("TG_OUTPUT_DIR", "parser_data").strip()
        session_raw = os.getenv("TG_SESSION", "telegram_parser").strip()
        if not output_raw:
            raise ValueError("TG_OUTPUT_DIR не может быть пустым")
        if not session_raw:
            raise ValueError("TG_SESSION не может быть пустым")

        output_dir = Path(output_raw).expanduser()
        session = Path(session_raw).expanduser()
        if not session.is_absolute():
            session = output_dir / "sessions" / session

        settings = cls(
            api_id=int(api_id_raw),
            api_hash=api_hash,
            phone=phone,
            session=session,
            output_dir=output_dir,
            media_workers=_read_int(
                "TG_MEDIA_WORKERS", 3, minimum=1, maximum=16
            ),
            db_batch_size=_read_int(
                "TG_DB_BATCH_SIZE", 500, minimum=10, maximum=10_000
            ),
            max_media_size_mb=_read_int(
                "TG_MAX_MEDIA_SIZE_MB", 100, minimum=0, maximum=1_000_000
            ),
            flood_sleep_threshold=_read_int(
                "TG_FLOOD_SLEEP_THRESHOLD", 120, minimum=0, maximum=86_400
            ),
        )
        settings.prepare_directories()
        return settings

    def prepare_directories(self) -> None:
        _make_private_directory(self.output_dir)
        _make_private_directory(self.media_dir)
        _make_private_directory(self.session.parent)

    def secure_session_files(self) -> None:
        if os.name != "posix":
            return
        for candidate in self.session.parent.glob(self.session.name + "*"):
            if candidate.is_file():
                candidate.chmod(stat.S_IRUSR | stat.S_IWUSR)

