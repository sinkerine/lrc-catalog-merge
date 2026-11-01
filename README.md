# lrc-catalog-merge

Merge the newest Lightroom Classic catalog backup from several backup
directories, and write the result back as a new backup with a newer
timestamp.

```sh
nix run . -- /nas/storage/software/backups/Lightroom /nas/storage/software/backups/Lightroom-asako
```

This picks the newest `YYYY-MM-DD HHMM/Lightroom Catalog.zip` in each
directory, merges them, and writes `<now>/Lightroom Catalog.zip` to every
source directory. The timestamp is always newer than any existing backup in
those directories, so Lightroom's "newest backup" is the merged one.

Or put the directories in a config file and run `lrc-catalog-merge` with no
arguments (see [Config](#config)).

Options:

- `-c FILE`: YAML config (default: `~/.config/lrc-catalog-merge/config.yaml`
  if it exists).
- `-o DIR` (repeatable): write only to these backup directories. The
  config's `exclude_folders` for them still apply.
- `-n` / `--dry-run`: merge in a temp dir and print the report without writing.
- `--no-lrcat-data`: keep only the base catalog's `.lrcat-data` (AI masks).
- `--work-dir DIR`: where to unpack (needs ~2x the catalogs' size).

To use the result, unzip it over (or next to) the catalog on the Lightroom
machine, like restoring any Lightroom backup.

## Config

```yaml
sources:            # merged in order; the first is the base
  - /nas/storage/software/backups/Lightroom
  - /nas/storage/software/backups/Lightroom-asako
outputs:            # default: every source
  - path: /nas/storage/software/backups/Lightroom
    exclude_folders:
      - C:/Users/sinkerine/Pictures/camera/
  - path: /nas/storage/software/backups/Lightroom-asako
```

Folders under an output's `exclude_folders` are removed from the catalog
written to that output, with all their subfolders and photos, and those
photos' edits, keywords, collection memberships, faces and stacks.
Paths are Lightroom folder paths as shown in the Folders panel. They match
case-insensitively, `/` and `\` are treated as the same, and they only
match on whole folder names. Keywords and collections stay. Entries that
match nothing are reported.

This suits folders that exist on only one machine. Nothing is lost on the
next run: the other source still has those photos, so the full merge
brings them back before the exclusion is applied again.

With Home Manager, use the flake's module:

```nix
{
  imports = [ lrc-catalog-merge.homeManagerModules.default ];
  programs.lrc-catalog-merge = {
    enable = true;
    settings = { sources = [ ... ]; outputs = [ ... ]; };
  };
}
```

## Merge rules

- The **first** source is the base. Later sources are merged into it in order.
- Photos are matched by root folder path + folder + file name (+ virtual copy
  name). A photo already in the base keeps the base's develop settings,
  metadata, and history. Its keywords and collection memberships from the
  other catalog are added. The report says how many such duplicates were
  edited more recently in the other catalog.
- Root folders (case-insensitive), folders, keywords (by parent + name),
  collections (by parent + name + type), imports, and EXIF/IPTC lookup values
  are merged by identity, not duplicated.
- Merging the same catalog again adds nothing.
- Not copied, because they only mean something in the catalog that made them:
  catalog settings (`Adobe_variablesTable`), publish services, Lightroom
  cloud sync state, work queues, and caches Lightroom rebuilds. The report
  lists any non-empty table it skips.
- `.lrcat-data` (AI masks, Remove) is a RocksDB store keyed by content digest.
  The merged store is the union of all keys, written with RocksDB's `ldb`
  while keeping Lightroom's own OPTIONS file.

Root folder paths are copied as-is. A folder that exists on only one
person's machine (e.g. `C:/Users/...`) shows as missing on the other until
it is relinked in Lightroom.

Previews and smart previews (`*.lrdata`) are not part of a backup, so
Lightroom regenerates them.

## How it works

Lightroom gives every row in the catalog an `id_local` from one counter
(`Adobe_entityIDCounter`), shared across all tables. The source's ids are
shifted past the base's counter, and every reference column is remapped the
same way, except for references to entities matched in the base. See
`src/lrc_catalog_merge/catalog.py` for the per-table rules. Tested against
catalog schema version 1400000 (Lightroom Classic 14/15). The tool refuses to
merge catalogs whose schema versions differ, or whose source has data in a
table it does not know.

## Development

```sh
nix develop
PYTHONPATH=src pytest
```
