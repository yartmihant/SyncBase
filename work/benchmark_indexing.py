"""Offline benchmark: python work/benchmark_indexing.py [--files 2000]."""

import argparse
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from syncbase.project import SyncProject


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=2000)
    parser.add_argument("--kib", type=int, default=128)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="syncbase-benchmark-") as directory:
        vault = Path(directory)
        root = vault / "Project"
        root.mkdir()
        (root / ".syncignore").write_text("", encoding="utf-8")
        content = b"x" * (args.kib * 1024)
        for i in range(args.files):
            folder = root / str(i // 100)
            folder.mkdir(exist_ok=True)
            (folder / f"{i}.bin").write_bytes(content)

        def scan(label):
            project = SyncProject(vault, "Project", "unused-offline-token")
            start = time.perf_counter()
            with contextlib.redirect_stdout(io.StringIO()):
                project.local_scan()
                project.set_cache()
            elapsed = time.perf_counter() - start
            print(f"{label}: {elapsed:.3f}s; cache: {(root / '.sync_cache').stat().st_size} bytes")

        print(f"{args.files} files, {args.files * args.kib / 1024:.1f} MiB")
        scan("First scan")
        scan("Unchanged scan")
        for i in range(min(10, args.files)):
            (root / "0" / f"{i}.bin").write_bytes(content + b"changed")
        scan("10 changed files")


if __name__ == "__main__":
    main()
