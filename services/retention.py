#!/usr/bin/env python3
"""
NestCam recording retention management.

This script prunes old recording files to keep:
- the total size of the recordings tree at or below a configured cap, and
- the filesystem containing the recordings tree above a configured free-space floor.

It is intended to be run manually or from systemd.
"""

import argparse
import logging
import shutil
from pathlib import Path


DEFAULT_ROOT = Path("/var/lib/nestcam/recordings")
DEFAULT_EXTS = {"h264", "mp4"}


def gb_to_bytes(x: float) -> int:
    return int(x * (1024 ** 3))


def parse_exts(raw: str) -> set[str]:
    exts = {e.strip().lower().lstrip(".") for e in raw.split(",") if e.strip()}
    return exts or set(DEFAULT_EXTS)


def list_recording_files(root: Path, exts: set[str]) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower().lstrip(".") not in exts:
            continue
        files.append(p)

    def sort_key(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except FileNotFoundError:
            return float("inf")

    files.sort(key=sort_key)
    return files


def dir_size_bytes(root: Path, exts: set[str]) -> int:
    total = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower().lstrip(".") not in exts:
            continue
        try:
            total += p.stat().st_size
        except FileNotFoundError:
            pass
    return total


def delete_empty_dirs(root: Path) -> None:
    dirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in dirs:
        try:
            next(d.iterdir())
        except StopIteration:
            try:
                d.rmdir()
                logging.info("Removed empty directory: %s", d)
            except OSError:
                pass


def free_bytes_for_path(path: Path) -> int:
    return shutil.disk_usage(path).free


def current_state(root: Path, exts: set[str]) -> tuple[int, int]:
    rec_bytes = dir_size_bytes(root, exts)
    free_bytes = free_bytes_for_path(root)
    return rec_bytes, free_bytes


def needs_pruning(rec_bytes: int, free_bytes: int, max_bytes: int, min_free_bytes: int) -> bool:
    return rec_bytes > max_bytes or free_bytes < min_free_bytes


def prune_recordings(
    root: Path,
    exts: set[str],
    max_bytes: int,
    min_free_bytes: int,
    dry_run: bool = False,
) -> int:
    rec_bytes, free_bytes = current_state(root, exts)

    logging.info("recordings_root=%s", root)
    logging.info("recordings_size=%.2f GB (cap %.2f GB)", rec_bytes / 1024 ** 3, max_bytes / 1024 ** 3)
    logging.info("free_space=%.2f GB (min %.2f GB)", free_bytes / 1024 ** 3, min_free_bytes / 1024 ** 3)

    if not needs_pruning(rec_bytes, free_bytes, max_bytes, min_free_bytes):
        logging.info("No pruning needed")
        return 0

    files = list_recording_files(root, exts)
    deleted = 0

    while files and needs_pruning(rec_bytes, free_bytes, max_bytes, min_free_bytes):
        victim = files.pop(0)

        try:
            size = victim.stat().st_size
        except FileNotFoundError:
            continue

        logging.info("Delete %s (%.1f MB)", victim, size / 1024 ** 2)

        if not dry_run:
            try:
                victim.unlink()
            except FileNotFoundError:
                continue

        deleted += 1
        rec_bytes = max(0, rec_bytes - size)
        free_bytes = free_bytes_for_path(root)

        if deleted % 10 == 0:
            files = list_recording_files(root, exts)

    if not dry_run:
        delete_empty_dirs(root)

    rec_bytes, free_bytes = current_state(root, exts)
    logging.info(
        "Done. deleted=%d recordings_size=%.2f GB free=%.2f GB",
        deleted,
        rec_bytes / 1024 ** 3,
        free_bytes / 1024 ** 3,
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Prune old NestCam recordings")
    ap.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="Root recordings directory",
    )
    ap.add_argument(
        "--max-gb",
        type=float,
        default=4.0,
        help="Maximum total size of managed recordings",
    )
    ap.add_argument(
        "--min-free-gb",
        type=float,
        default=2.0,
        help="Minimum free filesystem space to maintain",
    )
    ap.add_argument(
        "--exts",
        default="h264,mp4",
        help="Comma-separated file extensions to manage",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Log planned deletions without removing files",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    root = Path(args.root)
    exts = parse_exts(args.exts)
    max_bytes = gb_to_bytes(args.max_gb)
    min_free_bytes = gb_to_bytes(args.min_free_gb)

    if args.max_gb < 0 or args.min_free_gb < 0:
        logging.error("max-gb and min-free-gb must be non-negative")
        return 2

    if not root.exists():
        logging.info("recordings root does not exist: %s", root)
        return 0

    if not root.is_dir():
        logging.error("recordings root is not a directory: %s", root)
        return 2

    return prune_recordings(
        root=root,
        exts=exts,
        max_bytes=max_bytes,
        min_free_bytes=min_free_bytes,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
