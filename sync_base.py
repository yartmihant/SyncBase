#!/usr/bin/env python3
"""Backward-compatible source-tree launcher for SyncBase.

The application code lives in ``src/syncbase``.  Keeping this small launcher
preserves the historical ``python sync_base.py ...`` command without
maintaining a second implementation of the synchronizer.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from syncbase.cli import main  # noqa: E402


if __name__ == "__main__":
    main()
