"""Merge ``.lrcat-data`` directories.

Lightroom Classic 13+ keeps AI mask and Remove bitmaps in a RocksDB store
next to the catalog. Its keys are content digests, so two stores merge by
taking the union of their keys. The ``ldb`` tool from RocksDB does the
reading and writing; it reuses the store's own OPTIONS file so blob
settings and the table format version stay as Lightroom wrote them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _ldb() -> str:
    path = os.environ.get("LRC_LDB") or shutil.which("ldb")
    if not path:
        raise RuntimeError("RocksDB 'ldb' tool not found; set LRC_LDB or add rocksdb tools to PATH")
    return path


def _options_files(db: Path) -> list[Path]:
    return sorted(db.glob("OPTIONS-*"))


def keys(db: Path) -> set[str]:
    result = subprocess.run(
        [_ldb(), f"--db={db}", "--key_hex", "scan", "--no_value"],
        check=True, capture_output=True, text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.startswith("0x")}


def merge(target: Path, sources: list[Path]) -> int:
    """Add every key missing from ``target`` from ``sources``; return the count."""
    original_options = {p.name: p.read_bytes() for p in _options_files(target)}
    present = keys(target)
    added = 0
    for source in sources:
        dump = subprocess.run(
            [_ldb(), f"--db={source}", "--hex", "dump"],
            check=True, capture_output=True, text=True,
        ).stdout
        missing = [
            line for line in dump.splitlines()
            if " ==> " in line and line.split(" ==> ", 1)[0] not in present
        ]
        if not missing:
            continue
        subprocess.run(
            [_ldb(), f"--db={target}", "--hex", "load"],
            input="\n".join(missing) + "\n", check=True, capture_output=True, text=True,
        )
        present.update(line.split(" ==> ", 1)[0] for line in missing)
        added += len(missing)

    if added:
        subprocess.run([_ldb(), f"--db={target}", "compact"], check=True, capture_output=True)
        _restore_options(target, original_options)
    return added


def _restore_options(db: Path, original: dict[str, bytes]) -> None:
    """Keep the OPTIONS file Lightroom wrote instead of the one ``ldb`` wrote.

    ``ldb`` may be a newer RocksDB than Lightroom's and would otherwise leave
    an OPTIONS file naming options the older library does not know.
    """
    if not original:
        return
    newest_original = original[max(original)]
    written = _options_files(db)
    for path in written:
        path.unlink()
    name = written[-1].name if written else max(original)
    (db / name).write_bytes(newest_original)
