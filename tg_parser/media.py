from __future__ import annotations

DOWNLOADABLE_MEDIA = frozenset(
    {
        "photo",
        "video",
        "video_note",
        "gif",
        "voice",
        "audio",
        "sticker",
        "document",
    }
)


def parse_media_selection(value: str) -> frozenset[str] | None:
    normalized = value.strip().lower()
    if normalized == "all":
        return None
    if normalized == "none":
        return frozenset()
    selected = frozenset(item.strip() for item in normalized.split(",") if item.strip())
    unknown = selected - DOWNLOADABLE_MEDIA
    if unknown:
        raise ValueError("Неизвестные типы media: " + ", ".join(sorted(unknown)))
    if not selected:
        raise ValueError("Список media пуст")
    return selected
