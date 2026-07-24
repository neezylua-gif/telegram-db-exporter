"""Минимальные заглушки позволяют тестировать чистые функции без сетевых зависимостей."""

import importlib.util
import sys
import types


if importlib.util.find_spec("telethon") is None:
    telethon = types.ModuleType("telethon")
    errors = types.ModuleType("telethon.errors")

    class TelegramClient:  # pragma: no cover - только для импорта в offline CI
        pass

    class FloodWaitError(Exception):
        seconds = 0

    class RPCError(Exception):
        pass

    utils = types.SimpleNamespace(
        get_display_name=lambda entity: " ".join(
            part
            for part in (
                getattr(entity, "first_name", None),
                getattr(entity, "last_name", None),
            )
            if part
        ),
        get_peer_id=lambda entity: getattr(entity, "id", 0),
    )
    telethon.TelegramClient = TelegramClient
    telethon.utils = utils
    errors.FloodWaitError = FloodWaitError
    errors.RPCError = RPCError
    sys.modules["telethon"] = telethon
    sys.modules["telethon.errors"] = errors
