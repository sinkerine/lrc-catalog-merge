"""Merge the latest Lightroom Classic catalog backup of several backup dirs."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import backups, catalog, config, lrcat_data


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
        "sources", nargs="*", type=Path, metavar="SOURCE",
        help="Lightroom backup directory holding 'YYYY-MM-DD HHMM/<catalog>.zip' folders "
        "(default: the config's sources)",
    )
    parser.add_argument(
        "-c", "--config", type=Path,
        help=f"YAML config (default: {config.default_path()} if it exists)",
    )
    parser.add_argument(
        "-o", "--output", action="append", type=Path, metavar="DIR",
        help="backup directory to write the merged catalog to (repeatable; default: the "
        "config's outputs, else every SOURCE). The config's exclude_folders still apply.",
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


def resolve(args: argparse.Namespace) -> tuple[list[Path], list[config.Output]]:
    settings = config.Config()
    path = args.config or config.default_path()
    if args.config or path.is_file():
        settings = config.load(path)

    sources = [s.resolve() for s in (args.sources or settings.sources)]
    if not sources:
        raise config.ConfigError("no sources: pass SOURCE directories or set sources in the config")
    if len(set(sources)) != len(sources):
        raise config.ConfigError("a source directory is listed twice")

    excludes = {o.path.resolve(): o.exclude_folders for o in settings.outputs}
    if args.output:
        paths = [o.resolve() for o in args.output]
    elif settings.outputs:
        paths = [o.path.resolve() for o in settings.outputs]
    else:
        paths = sources
    if len(set(paths)) != len(paths):
        raise config.ConfigError("an output directory is listed twice")
    return sources, [config.Output(p, excludes.get(p, ())) for p in paths]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sources, outputs = resolve(args)
    except (config.ConfigError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

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

        # One catalog per distinct exclusion list; outputs without one share the merge.
        catalogs: dict[tuple[str, ...], Path] = {(): target}
        for output in outputs:
            if output.exclude_folders in catalogs:
                continue
            excluded = work / f"excluded-{len(catalogs)}" / target.name
            excluded.parent.mkdir()
            shutil.copy2(target, excluded)
            stats = catalog.exclude_folders(str(excluded), list(output.exclude_folders))
            print(f"for {output.path}:")
            print(stats.summary())
            unmatched = [f for f in output.exclude_folders if f not in stats.folders]
            for folder in unmatched:
                print(f"  warning: exclude_folders entry matched no folder: {folder}")
            problems = catalog.integrity_check(str(excluded))
            if problems != "ok":
                print(f"error: catalog for {output.path} failed integrity check: {problems}",
                      file=sys.stderr)
                return 1
            for ref, count in sorted(catalog.check_references(str(excluded)).items()):
                new = count - base_dangling.get(ref, 0)
                if new > 0:
                    print(f"  warning: exclusion left {new} dangling references in {ref}")
            print(f"  catalog: {catalog.image_count(str(excluded))} photos")
            catalogs[output.exclude_folders] = excluded

        if args.dry_run:
            print("dry run: nothing written")
            return 0

        stamp = backups.next_stamp([o.path for o in outputs], datetime.now())
        sep = backups.separator(base.archive)
        for output in outputs:
            archive = backups.write(
                catalogs[output.exclude_folders], target_data, base.catalog_name, sep,
                output.path, stamp,
            )
            print(f"wrote {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
