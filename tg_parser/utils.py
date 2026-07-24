from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

URL_RE = re.compile(r"https?://[^\s<>\]\[(){}\"']+", re.IGNORECASE)
BAD_FILENAME_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
WHITESPACE_RE = re.compile(r"\s+")
SAFE_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,10}$", re.IGNORECASE)
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def parse_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"Неверная дата {value!r}. Используйте YYYY-MM-DD или ISO 8601."
        ) from exc

    if len(normalized) == 10 and end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def sanitize_component(value: str | int | None, max_length: int = 100) -> str:
    if max_length < 1:
        raise ValueError("max_length должен быть положительным")
    text = "unknown" if value is None else str(value)
    text = unicodedata.normalize("NFKC", text)
    text = BAD_FILENAME_RE.sub("_", text)
    text = WHITESPACE_RE.sub(" ", text).strip(" ._")
    if text.upper() in WINDOWS_RESERVED_NAMES:
        text = f"_{text}"
    return (text or "unknown")[:max_length].rstrip(" .") or "unknown"


def safe_extension(filename: str | None) -> str:
    if not filename:
        return ""
    suffix = Path(filename).suffix.lower()
    return suffix if SAFE_EXTENSION_RE.fullmatch(suffix) else ""


def normalize_chat_reference(value: str) -> str | int:
    ref = value.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
            break
    ref = ref.split("?", 1)[0].strip("/")
    if ref.startswith("+") or ref.startswith("joinchat/"):
        return value.strip()
    ref = ref.removeprefix("@")
    if re.fullmatch(r"-?\d+", ref):
        return int(ref)
    return ref


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"__bytes_base64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, set):
        return sorted(value, key=str)
    return repr(value)


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def tl_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        result = value.to_dict()
        return result if isinstance(result, dict) else {"value": result}
    if isinstance(value, dict):
        return value
    return {"value": repr(value)}


def _normalize_url(url: str) -> str:
    return url.rstrip(".,;:!?'\"")


def extract_urls(text: str | None, entities: Iterable[Any] | None = None) -> list[str]:
    found: list[str] = []
    if text:
        found.extend(URL_RE.findall(text))
    for entity in entities or ():
        explicit_url = getattr(entity, "url", None)
        if explicit_url:
            found.append(str(explicit_url))

    result: list[str] = []
    seen: set[str] = set()
    for url in found:
        normalized = _normalize_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
