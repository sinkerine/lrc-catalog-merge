"""Find, unpack, and write Lightroom Classic catalog backups.

Lightroom writes each backup as ``<backup dir>/YYYY-MM-DD HHMM/<Catalog>.zip``.
The zip holds ``<Catalog>.lrcat`` and, since Lightroom Classic 13,
``<Catalog>.lrcat-data/*``. Backups made on Windows store the directory
separator as a backslash, which is preserved when writing a new backup.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

STAMP_FORMAT = "%Y-%m-%d %H%M"
STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{4}")


@dataclass(frozen=True)
class Backup:
    root: Path  # the backup directory, e.g. .../backups/Lightroom
    stamp: datetime
    archive: Path

    @property
    def catalog_name(self) -> str:
        return self.archive.stem


def _stamp(name: str) -> datetime | None:
    if not STAMP.fullmatch(name):
        return None
    try:
        return datetime.strptime(name, STAMP_FORMAT)
    except ValueError:
        return None


def backups(root: Path) -> list[Backup]:
    found = []
    for entry in root.iterdir():
        stamp = _stamp(entry.name)
        if stamp is None or not entry.is_dir():
            continue
        archives = sorted(entry.glob("*.zip"))
        if len(archives) == 1:
            found.append(Backup(root, stamp, archives[0]))
    return sorted(found, key=lambda b: b.stamp)


def latest(root: Path) -> Backup:
    found = backups(root)
    if not found:
        raise FileNotFoundError(f"no 'YYYY-MM-DD HHMM/<catalog>.zip' backups in {root}")
    return found[-1]


def _split(name: str) -> list[str]:
    return [part for part in re.split(r"[\\/]", name) if part]


def separator(archive: Path) -> str:
    with zipfile.ZipFile(archive) as zf:
        return "\\" if any("\\" in n for n in zf.namelist()) else "/"


def extract(backup: Backup, dest: Path) -> tuple[Path, Path | None]:
    """Unpack a backup; return the .lrcat path and the .lrcat-data dir, if any."""
    dest.mkdir(parents=True)
    catalog = data = None
    with zipfile.ZipFile(backup.archive) as zf:
        for info in zf.infolist():
            parts = _split(info.filename)
            if not parts or info.is_dir() or any(p in {".", ".."} for p in parts):
                continue
            target = dest.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
            if len(parts) == 1 and parts[0].endswith(".lrcat"):
                catalog = target
            elif len(parts) > 1 and parts[0].endswith(".lrcat-data"):
                data = dest / parts[0]
    if catalog is None:
        raise FileNotFoundError(f"no .lrcat in {backup.archive}")
    return catalog, data


def next_stamp(roots: list[Path], now: datetime) -> datetime:
    """A stamp at ``now`` (minute precision) that is newer than every existing backup."""
    stamp = now.replace(second=0, microsecond=0)
    newest = max((b.stamp for root in roots if root.is_dir() for b in backups(root)), default=None)
    if newest is not None and stamp <= newest:
        stamp = newest + timedelta(minutes=1)
    while any((root / stamp.strftime(STAMP_FORMAT)).exists() for root in roots):
        stamp += timedelta(minutes=1)
    return stamp


def write(
    catalog: Path, data: Path | None, name: str, sep: str, root: Path, stamp: datetime
) -> Path:
    """Write ``<root>/<stamp>/<name>.zip`` the way Lightroom lays out a backup."""
    folder = root / stamp.strftime(STAMP_FORMAT)
    folder.mkdir(parents=True, exist_ok=False)
    archive = folder / f"{name}.zip"
    partial = folder / f".{name}.zip.partial"
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.write(catalog, f"{name}.lrcat")
            if data is not None:
                for path in sorted(p for p in data.rglob("*") if p.is_file()):
                    if path.name == "LOCK" or path.name.startswith("LOG"):
                        continue  # RocksDB runtime files; Lightroom's backups omit them
                    relative = sep.join(path.relative_to(data).parts)
                    zf.write(path, f"{name}.lrcat-data{sep}{relative}")
        os.replace(partial, archive)
    except BaseException:
        partial.unlink(missing_ok=True)
        if not any(folder.iterdir()):
            folder.rmdir()
        raise
    return archive
