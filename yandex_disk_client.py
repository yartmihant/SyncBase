"""Compatibility import for the former standalone YandexDiskClient module.

New code should import :class:`syncbase.client.YandexDiskClient`.  This module
keeps legacy scripts and third-party callers working while using exactly the
same implementation as the SyncBase package.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from syncbase.client import (  # noqa: E402,F401
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
