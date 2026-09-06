"""Installed compatibility module for the former standalone client."""

from syncbase.client import (
    CHUNK_SIZE,
    PROGRESS_BAR_FILESIZE,
    YandexDiskClient,
    _fmt_size,
    _fmt_speed,
)

__all__ = [
    "CHUNK_SIZE",
    "PROGRESS_BAR_FILESIZE",
    "YandexDiskClient",
]
