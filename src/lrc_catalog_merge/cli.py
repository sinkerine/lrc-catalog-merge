"""Merge the latest Lightroom Classic catalog backup of several backup dirs."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import backups, catalog, lrcat_data


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lrc-catalog-merge",
        description=(
            "Merge the newest catalog backup found in each SOURCE backup directory and write "
            "the result as a new, newer backup. The first source is the base: where both "
            "catalogs have the same photo, the base's edits and metadata are kept."
        ),
    )
    parser.add_argument(
        "sources", nargs="+", type=Path, metavar="SOURCE",
        help="Lightroom backup directory holding 'YYYY-MM-DD HHMM/<catalog>.zip' folders",
    )
    parser.add_argument(
        "-o", "--output", action="append", type=Path, metavar="DIR",
        help="backup directory to write the merged catalog to (repeatable; default: every SOURCE)",
    )
    parser.add_argument(
        "--no-lrcat-data", action="store_true",
        help="keep only the base catalog's .lrcat-data (AI masks) instead of merging them",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="merge in a temporary directory and report, but write nothing",
    )
    parser.add_argument(
        "--work-dir", type=Path,
        help="directory for temporary files (default: system temp dir)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sources = [s.resolve() for s in args.sources]
    if len(set(sources)) != len(sources):
        print("error: a source directory is listed twice", file=sys.stderr)
        return 2
    outputs = [o.resolve() for o in (args.output or sources)]

    latest = [backups.latest(s) for s in sources]
    base = latest[0]
    for backup in latest:
        print(f"{'base  ' if backup is base else 'source'} {backup.archive}")

    with tempfile.TemporaryDirectory(prefix="lrc-catalog-merge-", dir=args.work_dir) as tmp:
        work = Path(tmp)
        unpacked = [backups.extract(b, work / str(i)) for i, b in enumerate(latest)]
        target, target_data = unpacked[0]
        base_dangling = catalog.check_references(str(target))

        for (source, _), backup in zip(unpacked[1:], latest[1:]):
            stats = catalog.merge_catalog(str(target), str(source))
            stats.source = str(backup.archive)
            print(stats.summary())

        source_data = [d for _, d in unpacked[1:] if d is not None]
        if not args.no_lrcat_data and source_data:
            if target_data is None:
                target_data = target.with_name(target.name + "-data")
                shutil.copytree(source_data[0], target_data)
                source_data = source_data[1:]
            added = lrcat_data.merge(target_data, source_data)
            print(f".lrcat-data: {added} AI mask entries added")

        problems = catalog.integrity_check(str(target))
        if problems != "ok":
            print(f"error: merged catalog failed integrity check: {problems}", file=sys.stderr)
            return 1
        for ref, count in sorted(catalog.check_references(str(target)).items()):
            new = count - base_dangling.get(ref, 0)
            if new > 0:
                print(f"warning: merge added {new} dangling references in {ref}")
        print(f"merged catalog: {catalog.image_count(str(target))} photos")

        if args.dry_run:
            print("dry run: nothing written")
            return 0

        stamp = backups.next_stamp(outputs, datetime.now())
        sep = backups.separator(base.archive)
        for output in outputs:
            archive = backups.write(target, target_data, base.catalog_name, sep, output, stamp)
            print(f"wrote {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
