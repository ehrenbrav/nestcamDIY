#!/usr/bin/env python3
import argparse
import os
import shutil
import time
from pathlib import Path

def bytes_gb(x: float) -> int:
    return int(x * (1024**3))

def dir_size_bytes(root: Path, exts: set[str]) -> int:
    total = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower().lstrip(".") in exts:
            try:
                total += p.stat().st_size
            except FileNotFoundError:
                pass
    return total

def list_recording_files(root: Path, exts: set[str]) -> list[Path]:
    files = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower().lstrip(".") in exts:
            files.append(p)
    # oldest first by mtime
    files.sort(key=lambda p: p.stat().st_mtime)
    return files

def delete_empty_dirs(root: Path) -> None:
    # deepest first
    for d in sorted([p for p in root.rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
        try:
            next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
            except OSError:
                pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/recordings"))
    ap.add_argument("--max-gb", type=float, default=4.0, help="Max total size of recordings directory")
    ap.add_argument("--min-free-gb", type=float, default=2.0, help="Min free space to keep on filesystem")
    ap.add_argument("--exts", default="h264,mp4", help="Comma-separated extensions to manage")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    exts = {e.strip().lower() for e in args.exts.split(",") if e.strip()}
    max_bytes = bytes_gb(args.max_gb)
    min_free_bytes = bytes_gb(args.min_free_gb)

    if not root.exists():
        print(f"[retention] root does not exist: {root}")
        return 0

    # Use filesystem containing the recordings root
    usage = shutil.disk_usage(str(root))
    free_bytes = usage.free
    rec_bytes = dir_size_bytes(root, exts)

    print(f"[retention] recordings_root={root}")
    print(f"[retention] recordings_size={rec_bytes/1024**3:.2f} GB (cap {args.max_gb:.2f} GB)")
    print(f"[retention] free_space={free_bytes/1024**3:.2f} GB (min {args.min_free_gb:.2f} GB)")

    files = list_recording_files(root, exts)

    deleted = 0
    while files and (rec_bytes > max_bytes or free_bytes < min_free_bytes):
        victim = files.pop(0)
        try:
            size = victim.stat().st_size
        except FileNotFoundError:
            continue

        print(f"[retention] delete {victim} ({size/1024**2:.1f} MB)")
        if not args.dry_run:
            try:
                victim.unlink()
            except FileNotFoundError:
                pass

        deleted += 1
        rec_bytes = max(0, rec_bytes - size)
        usage = shutil.disk_usage(str(root))
        free_bytes = usage.free

        # refresh file list occasionally in case of concurrent writes
        if deleted % 10 == 0:
            files = list_recording_files(root, exts)

    if not args.dry_run:
        delete_empty_dirs(root)

    usage = shutil.disk_usage(str(root))
    print(f"[retention] done. deleted={deleted} recordings_size={rec_bytes/1024**3:.2f} GB free={usage.free/1024**3:.2f} GB")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

