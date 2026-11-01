"""Merge Lightroom Classic .lrcat SQLite catalogs.

Lightroom allocates every ``id_local`` from one catalog-wide counter
(``Adobe_entityIDCounter``), so ids are unique across tables. A source
catalog is merged by shifting all of its ids past the target's counter,
except for entities that already exist in the target (same root folder,
folder, file, keyword, collection, ...), which are mapped onto the
target's row instead. Rows that belong to an entity that was mapped
rather than inserted (e.g. the develop settings of a duplicate photo)
are skipped, so the target's version wins.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field

# Columns that hold an id_local of another row, per table.
REFS: dict[str, tuple[str, ...]] = {
    "Adobe_AdditionalMetadata": ("image",),
    "Adobe_faceProperties": ("face",),
    "Adobe_imageDevelopBeforeSettings": ("developSettings",),
    "Adobe_imageDevelopSettings": ("image",),
    "Adobe_imageProofSettings": ("image",),
    "Adobe_imageProperties": ("image",),
    "Adobe_images": ("masterImage", "originalRootEntity", "rootFile"),
    "Adobe_libraryImageDevelop3DLUTColorTable": (),
    "Adobe_libraryImageDevelopHistoryStep": ("image",),
    "Adobe_libraryImageDevelopSnapshot": ("image",),
    "Adobe_libraryImageFaceProcessHistory": ("image",),
    "AgDevelopAdditionalMetadata": ("image",),
    "AgFolderContent": ("containingFolder",),
    "AgHarvestedDNGMetadata": ("image",),
    "AgHarvestedExifMetadata": ("image", "cameraModelRef", "cameraSNRef", "lensRef"),
    "AgHarvestedIptcMetadata": (
        "image", "cityRef", "countryRef", "creatorRef", "isoCountryCodeRef",
        "jobIdentifierRef", "locationRef", "stateRef",
    ),
    "AgInternedExifCameraModel": (),
    "AgInternedExifCameraSN": (),
    "AgInternedExifLens": (),
    "AgInternedIptcCity": (),
    "AgInternedIptcCountry": (),
    "AgInternedIptcCreator": (),
    "AgInternedIptcIsoCountryCode": (),
    "AgInternedIptcJobIdentifier": (),
    "AgInternedIptcLocation": (),
    "AgInternedIptcState": (),
    "AgLibraryCollection": ("parent",),
    "AgLibraryCollectionChangeCounter": ("collection",),
    "AgLibraryCollectionContent": ("collection",),
    "AgLibraryCollectionCoverImage": ("collection", "collectionImage"),
    "AgLibraryCollectionImage": ("collection", "image"),
    "AgLibraryCollectionImageChangeCounter": ("collectionImage", "collection", "image"),
    "AgLibraryCollectionLabel": ("collection",),
    "AgLibraryCollectionStack": ("collection",),
    "AgLibraryCollectionStackImage": ("collection", "image", "stack"),
    "AgLibraryFace": ("cluster", "image"),
    "AgLibraryFaceCluster": ("keyFace",),
    "AgLibraryFaceData": ("face",),
    "AgLibraryFile": ("folder",),
    "AgLibraryFileAssetMetadata": ("fileId",),
    "AgLibraryFolder": ("parentId", "rootFolder"),
    "AgLibraryFolderFavorite": ("folder",),
    "AgLibraryFolderLabel": ("folder",),
    "AgLibraryFolderStack": (),
    "AgLibraryFolderStackImage": ("image", "stack"),
    "AgLibraryIPTC": ("image",),
    "AgLibraryImageAttributes": ("image",),
    "AgLibraryImageChangeCounter": ("image",),
    "AgLibraryImageSearchData": ("image",),
    "AgLibraryImport": (),
    "AgLibraryImportImage": ("image", "import"),
    "AgLibraryKeyword": ("parent",),
    "AgLibraryKeywordFace": ("face", "tag"),
    "AgLibraryKeywordImage": ("image", "tag"),
    "AgLibraryKeywordSynonym": ("keyword",),
    "AgLibraryRootFolder": (),
    "AgMetadataSearchIndex": ("image",),
    "AgOutputImageAsset": ("collection", "image"),
    "AgPhotoProperty": ("photo", "propertySpec"),
    "AgPhotoPropertyArrayElement": ("photo", "propertySpec"),
    "AgPhotoPropertySpec": (),
    "AgSearchablePhotoProperty": ("photo", "propertySpec"),
    "AgSearchablePhotoPropertyArrayElement": ("photo", "propertySpec"),
    "AgSourceColorProfileConstants": ("image",),
    "AgVideoInfo": ("image",),
}

# Untyped cache columns holding an id_local as REAL or as text like "1234.0"
# (or "none"). They keep their storage type when remapped.
TEXT_REFS: dict[str, tuple[str, ...]] = {
    "Adobe_images": ("developSettingsIDCache",),
    "Adobe_imageDevelopSettings": ("beforeSettingsIDCache",),
}

# Entities matched against the target by a natural key instead of being
# inserted blindly. Keys are compared after the row's refs are mapped to
# target ids, so parents must be processed before their children.
DEDUPE: dict[str, tuple[str, ...]] = {
    "AgLibraryRootFolder": ("absolutePath",),
    "AgLibraryFolder": ("rootFolder", "pathFromRoot"),
    "AgLibraryFile": ("folder", "lc_idx_filename"),
    "Adobe_images": ("rootFile", "copyName"),
    "AgLibraryKeyword": ("parent", "lc_name"),
    "AgLibraryCollection": ("parent", "name", "creationId"),
    "AgLibraryImport": ("importDate", "name"),
    "AgPhotoPropertySpec": ("sourcePlugin", "key", "pluginVersion"),
    "Adobe_libraryImageDevelop3DLUTColorTable": ("LUTHash",),
    **{t: ("value",) for t in REFS if t.startswith("AgInterned")},
}

# Rows inserted only when the entity in this column was inserted too;
# otherwise the target already has its own copy of them.
OWNER: dict[str, str] = {
    "Adobe_AdditionalMetadata": "image",
    "Adobe_faceProperties": "face",
    "Adobe_imageDevelopBeforeSettings": "developSettings",
    "Adobe_imageDevelopSettings": "image",
    "Adobe_imageProofSettings": "image",
    "Adobe_imageProperties": "image",
    "Adobe_libraryImageDevelopHistoryStep": "image",
    "Adobe_libraryImageDevelopSnapshot": "image",
    "Adobe_libraryImageFaceProcessHistory": "image",
    "AgDevelopAdditionalMetadata": "image",
    "AgFolderContent": "containingFolder",
    "AgHarvestedDNGMetadata": "image",
    "AgHarvestedExifMetadata": "image",
    "AgHarvestedIptcMetadata": "image",
    "AgLibraryCollectionChangeCounter": "collection",
    "AgLibraryCollectionContent": "collection",
    "AgLibraryCollectionCoverImage": "collection",
    "AgLibraryCollectionImageChangeCounter": "collectionImage",
    "AgLibraryCollectionLabel": "collection",
    "AgLibraryCollectionStack": "collection",
    "AgLibraryCollectionStackImage": "collection",
    "AgLibraryFace": "image",
    "AgLibraryFaceData": "face",
    "AgLibraryFileAssetMetadata": "fileId",
    "AgLibraryFolderFavorite": "folder",
    "AgLibraryFolderLabel": "folder",
    "AgLibraryFolderStackImage": "image",
    "AgLibraryIPTC": "image",
    "AgLibraryImageAttributes": "image",
    "AgLibraryImageChangeCounter": "image",
    "AgLibraryImageSearchData": "image",
    "AgLibraryImportImage": "image",
    "AgLibraryKeywordFace": "face",
    "AgLibraryKeywordSynonym": "keyword",
    "AgMetadataSearchIndex": "image",
    "AgOutputImageAsset": "image",
    "AgPhotoProperty": "photo",
    "AgPhotoPropertyArrayElement": "photo",
    "AgSearchablePhotoProperty": "photo",
    "AgSearchablePhotoPropertyArrayElement": "photo",
    "AgSourceColorProfileConstants": "image",
    "AgVideoInfo": "image",
}

# Link rows kept whenever both ends exist, unless the target already has them.
MEMBERSHIP: dict[str, tuple[str, ...]] = {
    "AgLibraryKeywordImage": ("image", "tag"),
    "AgLibraryCollectionImage": ("collection", "image"),
}

# Rows with no owner column, inserted only when an inserted row of another
# table (copied earlier in ORDER) refers to them.
USED_BY: dict[str, tuple[str, str]] = {
    "AgLibraryFaceCluster": ("AgLibraryFace", "cluster"),
    "AgLibraryFolderStack": ("AgLibraryFolderStackImage", "stack"),
}

# Refs to rows copied later in ORDER; they only need to exist in the source.
FORWARD_REFS: frozenset[tuple[str, str]] = frozenset({
    ("Adobe_images", "originalRootEntity"),
    ("AgLibraryFace", "cluster"),
    ("AgLibraryFolderStackImage", "stack"),
    ("AgLibraryCollectionStackImage", "stack"),
})

# Copy order. Owners precede the rows they own. Folder stack images come
# before their stacks: the stack insert trigger then initialises
# AgLibraryFolderStackData from the images already present.
ORDER: tuple[str, ...] = (
    "AgLibraryRootFolder",
    "AgLibraryFolder",
    "AgLibraryFile",
    "Adobe_images",
    "AgLibraryKeyword",
    "AgLibraryCollection",
    "AgLibraryImport",
    "AgPhotoPropertySpec",
    "Adobe_libraryImageDevelop3DLUTColorTable",
    *sorted(t for t in REFS if t.startswith("AgInterned")),
    "Adobe_imageDevelopSettings",
    "Adobe_imageDevelopBeforeSettings",
    "AgLibraryFace",
    "Adobe_faceProperties",
    "AgLibraryFaceData",
    "AgLibraryFaceCluster",
    "AgLibraryKeywordFace",
    "AgLibraryKeywordSynonym",
    "AgLibraryCollectionImage",
    "AgLibraryCollectionImageChangeCounter",
    "AgLibraryCollectionCoverImage",
    "AgLibraryCollectionStackImage",
    "AgLibraryCollectionStack",
    "AgLibraryFolderStackImage",
    "AgLibraryFolderStack",
    *sorted(
        t for t in REFS
        if t not in DEDUPE and t not in {
            "Adobe_imageDevelopSettings", "Adobe_imageDevelopBeforeSettings",
            "AgLibraryFace", "Adobe_faceProperties", "AgLibraryFaceData",
            "AgLibraryFaceCluster", "AgLibraryKeywordFace", "AgLibraryKeywordSynonym",
            "AgLibraryCollectionImage", "AgLibraryCollectionImageChangeCounter",
            "AgLibraryCollectionCoverImage", "AgLibraryCollectionStackImage",
            "AgLibraryCollectionStack", "AgLibraryFolderStackImage", "AgLibraryFolderStack",
        }
    ),
)
assert sorted(ORDER) == sorted(REFS), "ORDER must list every table in REFS exactly once"

# Per-catalog state, caches, work queues, and cloud sync/publish data that
# only make sense in the catalog that produced them. Tables named here are
# never copied; non-empty ones are reported.
SKIP: frozenset[str] = frozenset({
    "Adobe_variables", "Adobe_variablesTable", "Adobe_namedIdentityPlate",
    "AgSpecialSourceContent", "AgLibraryBackups", "AgMRULists",
    "AgLibraryKeywordCooccurrence", "AgLibraryKeywordPopularity",
    "AgLibraryFolderStackData", "AgLibraryCollectionStackData",
    "AgDNGProxyInfo", "AgDNGProxyInfoUpdater", "AgHarvestedMetadataWorklist",
    "AgLibraryImageSaveXMP", "AgLibraryImageXMPUpdater", "AgPublishListenerWorklist",
    "AgTempImages", "AgLastCatalogExport", "AgLibraryUpdatedImages",
    "AgLibraryPublishedCollection", "AgLibraryPublishedCollectionContent",
    "AgLibraryPublishedCollectionImage", "AgLibraryPublishedCollectionLabel",
    "AgRemotePhoto", "AgPhotoComment",
    "AgLibraryImageSyncedAssetData", "AgLibraryCollectionSyncedAlbumData",
    "AgLibraryCollectionTrackedAssets", "LrMobileSyncChangeCounter",
    "MigrationSchemaVersion", "MigratedInfo", "MigratedImages",
    "MigratedCollections", "MigratedCollectionImages",
    "sqlite_stat1", "sqlite_stat4",
})
# Lightroom cloud sync tables (Ag*Oz*) are skipped by name pattern.
OZ = re.compile(r"Oz")

# Sync/publish tables whose rows are dropped silently: they are rebuilt by
# Lightroom or are meaningless outside the source catalog.
QUIET_SKIP: frozenset[str] = frozenset({
    "Adobe_variables", "Adobe_variablesTable", "AgSpecialSourceContent",
    "AgLibraryBackups", "AgMRULists", "AgLibraryKeywordCooccurrence",
    "AgLibraryKeywordPopularity", "AgLibraryFolderStackData",
    "AgLibraryCollectionStackData", "AgLibraryImageSyncedAssetData",
    "LrMobileSyncChangeCounter", "MigrationSchemaVersion", "sqlite_stat1", "sqlite_stat4",
})

GENEALOGY_TABLES = ("AgLibraryKeyword", "AgLibraryCollection")
NUMERIC_TEXT = re.compile(r"(\d+)(\.0*)?")


class MergeError(RuntimeError):
    pass


class RawText(bytes):
    """A TEXT value that is not valid UTF-8 (e.g. AgLibraryFaceData.data)."""


def _text_factory(value: bytes):
    try:
        return value.decode()
    except UnicodeDecodeError:
        return RawText(value)


@dataclass
class MergeStats:
    source: str
    inserted: Counter = field(default_factory=Counter)
    matched: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)
    dropped_tables: dict[str, int] = field(default_factory=dict)
    newer_duplicates: int = 0

    def summary(self) -> str:
        lines = [f"merged {self.source}:"]
        images = (self.inserted["Adobe_images"], self.matched["Adobe_images"])
        lines.append(f"  photos: {images[0]} added, {images[1]} already in target")
        if self.newer_duplicates:
            lines.append(
                f"  note: {self.newer_duplicates} duplicate photos were edited more recently "
                "in the source; the target's edits were kept"
            )
        for table in sorted(set(self.inserted) | set(self.matched) | set(self.skipped)):
            lines.append(
                f"  {table}: +{self.inserted[table]} matched={self.matched[table]} "
                f"skipped={self.skipped[table]}"
            )
        for table, count in sorted(self.dropped_tables.items()):
            lines.append(f"  not copied (catalog-specific): {table} ({count} rows)")
        return "\n".join(lines)


def _columns(db: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in db.execute(f'pragma {schema}.table_info("{table}")')]


def _tables(db: sqlite3.Connection, schema: str) -> set[str]:
    return {
        r[0] for r in db.execute(f"select name from {schema}.sqlite_master where type = 'table'")
    }


def _variable(db: sqlite3.Connection, schema: str, name: str):
    row = db.execute(
        f"select value from {schema}.Adobe_variablesTable where name = ?", (name,)
    ).fetchone()
    return row[0] if row else None


def _as_id(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, float):
        return int(value)
    if isinstance(value, int):
        return value
    match = NUMERIC_TEXT.fullmatch(str(value))
    return int(match.group(1)) if match else None


def _max_id(db: sqlite3.Connection, schema: str) -> int:
    best = 0
    for table in _tables(db, schema):
        if "id_local" in _columns(db, schema, table):
            value = db.execute(f'select max(id_local) from {schema}."{table}"').fetchone()[0]
            best = max(best, _as_id(value) or 0)
    return best


def _remap_loose(value, id_map: dict[int, int]):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        source_id = _as_id(value)
        if source_id in id_map:
            return type(value)(id_map[source_id])
        return value
    if isinstance(value, str):
        match = NUMERIC_TEXT.fullmatch(value)
        if match and int(match.group(1)) in id_map:
            return f"{id_map[int(match.group(1))]}{match.group(2) or ''}"
    return value


def _parents_first(rows: list, id_column: int, parent_column: int) -> list:
    parents = {_as_id(r[id_column]): _as_id(r[parent_column]) for r in rows}

    def depth(node: int | None) -> int:
        seen = set()
        while node in parents and node not in seen:
            seen.add(node)
            node = parents[node]
        return len(seen)

    return sorted(rows, key=lambda r: depth(_as_id(r[id_column])))


def _genealogy(parent_genealogy: str | None, id_local: int) -> str:
    digits = str(id_local)
    return f"{parent_genealogy or ''}/{len(digits)}{digits}"


def merge_catalog(target_path: str, source_path: str) -> MergeStats:
    """Merge the catalog at ``source_path`` into ``target_path`` in place."""
    stats = MergeStats(source=source_path)
    db = sqlite3.connect(target_path, isolation_level=None)
    db.text_factory = _text_factory
    try:
        db.execute("attach database ? as src", (f"file:{source_path}?mode=ro",))
    except sqlite3.OperationalError:
        db.execute("attach database ? as src", (source_path,))

    try:
        db.execute("begin immediate")
        _merge(db, stats)
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise
    finally:
        db.execute("detach database src")
        db.close()
    return stats


def _merge(db: sqlite3.Connection, stats: MergeStats) -> None:
    target_version = _variable(db, "main", "Adobe_DBVersion")
    source_version = _variable(db, "src", "Adobe_DBVersion")
    if target_version != source_version:
        raise MergeError(
            f"catalog schema versions differ ({target_version} vs {source_version}); "
            "open both catalogs in the same Lightroom version first"
        )

    target_tables, source_tables = _tables(db, "main"), _tables(db, "src")
    for table in sorted(source_tables):
        count = db.execute(f'select count(*) from src."{table}"').fetchone()[0]
        if table in REFS:
            if table not in target_tables:
                raise MergeError(f"target catalog has no table {table}")
            if set(_columns(db, "src", table)) - set(_columns(db, "main", table)):
                raise MergeError(f"source table {table} has columns the target lacks")
        elif table in SKIP or OZ.search(table):
            if count and table not in QUIET_SKIP:
                stats.dropped_tables[table] = count
        elif count:
            raise MergeError(f"unknown non-empty table {table}; refusing to merge")

    counter = _as_id(_variable(db, "main", "Adobe_entityIDCounter")) or 0
    offset = max(counter, _max_id(db, "main")) + 1

    # Every source id maps to source id + offset unless matched below.
    id_map: dict[int, int] = {}
    present: set[int] = set()  # source ids that exist in some copied table
    for table in REFS:
        if "id_local" in _columns(db, "src", table):
            for (value,) in db.execute(f'select id_local from src."{table}"'):
                source_id = _as_id(value)
                if source_id is not None:
                    present.add(source_id)
                    id_map[source_id] = source_id + offset
    inserted: set[int] = set()  # source ids inserted as new rows
    matched: set[int] = set()  # source ids mapped onto existing target rows
    used: dict[str, set[int]] = {table: set() for table in USED_BY}
    users = {user: (table, column) for table, (user, column) in USED_BY.items()}

    def remap(value):
        source_id = _as_id(value)
        return None if source_id is None else id_map.get(source_id)

    for table in ORDER:
        if table not in source_tables:
            continue
        columns = _columns(db, "src", table)
        refs = [c for c in REFS[table] if c in columns]
        text_refs = [c for c in TEXT_REFS.get(table, ()) if c in columns]
        has_id = "id_local" in columns
        has_uuid = "id_global" in columns
        owner = OWNER.get(table)

        existing_uuids: set = set()
        if has_uuid:
            existing_uuids = {r[0] for r in db.execute(f'select id_global from main."{table}"')}

        key_columns = DEDUPE.get(table) or MEMBERSHIP.get(table)
        existing_keys: dict[tuple, int | None] = {}
        if key_columns:
            select_id = "id_local" if has_id else "null"
            quoted = ", ".join(f'"{c}"' for c in key_columns)
            for row in db.execute(f'select {quoted}, {select_id} from main."{table}"'):
                existing_keys[_key(key_columns, row[:-1])] = _as_id(row[-1])

        order_by = ""
        if table in GENEALOGY_TABLES:
            order_by = "order by id_local"  # re-sorted parent-first below
        elif table == "Adobe_images":
            order_by = "order by masterImage is not null"
        elif table == "AgLibraryFolder":
            order_by = "order by length(pathFromRoot)"

        rows = db.execute(
            f'select {", ".join(f"{chr(34)}{c}{chr(34)}" for c in columns)} '
            f'from src."{table}" {order_by}'
        ).fetchall()
        insert_sql = (
            f'insert into main."{table}" ({", ".join(f"{chr(34)}{c}{chr(34)}" for c in columns)}) '
            f'values ({", ".join("?" for _ in columns)})'
        )
        index = {c: i for i, c in enumerate(columns)}
        if table in GENEALOGY_TABLES:
            rows = _parents_first(rows, index["id_local"], index["parent"])

        for source_row in rows:
            row = list(source_row)
            source_id = _as_id(row[index["id_local"]]) if has_id else None

            if owner and _as_id(row[index[owner]]) not in inserted:
                stats.skipped[table] += 1
                continue
            if table in USED_BY and source_id not in used[table]:
                stats.skipped[table] += 1
                continue

            unresolved = False
            for column in refs:
                value = row[index[column]]
                if _as_id(value) is None:
                    row[index[column]] = None
                    continue
                resolvable = present if (table, column) in FORWARD_REFS else inserted | matched
                if _as_id(value) not in resolvable:
                    unresolved = True
                    break
                row[index[column]] = remap(value)
            if unresolved:
                stats.skipped[table] += 1
                continue

            for column in text_refs:
                row[index[column]] = _remap_loose(row[index[column]], id_map)

            if key_columns:
                key = _key(key_columns, [row[index[c]] for c in key_columns])
                if key in existing_keys:
                    if source_id is not None and existing_keys[key] is not None:
                        id_map[source_id] = existing_keys[key]
                        matched.add(source_id)
                    if table == "Adobe_images":
                        _note_newer(db, stats, row[index["touchTime"]], existing_keys[key])
                    stats.matched[table] += 1
                    continue

            if has_id:
                row[index["id_local"]] = id_map[source_id]
            if has_uuid and row[index["id_global"]] in existing_uuids:
                row[index["id_global"]] = str(uuid.uuid4()).upper()
            if table in GENEALOGY_TABLES:
                parent = row[index["parent"]]
                parent_genealogy = None
                if parent is not None:
                    parent_genealogy = db.execute(
                        f'select genealogy from main."{table}" where id_local = ?', (parent,)
                    ).fetchone()[0]
                row[index["genealogy"]] = _genealogy(parent_genealogy, row[index["id_local"]])

            if any(isinstance(v, RawText) for v in row):
                # Bind as BLOB and cast back so the stored type stays TEXT.
                placeholders = ", ".join(
                    "cast(? as text)" if isinstance(v, RawText) else "?" for v in row
                )
                db.execute(insert_sql.rsplit("values", 1)[0] + f"values ({placeholders})", row)
            else:
                db.execute(insert_sql, row)
            stats.inserted[table] += 1
            if source_id is not None:
                inserted.add(source_id)
            if has_uuid:
                existing_uuids.add(row[index["id_global"]])
            if key_columns:
                existing_keys[_key(key_columns, [row[index[c]] for c in key_columns])] = (
                    row[index["id_local"]] if has_id else None
                )
            if table in users:
                used_table, column = users[table]
                used[used_table].add(_as_id(source_row[index[column]]))

    next_id = _max_id(db, "main") + 1
    db.execute(
        "update main.Adobe_variablesTable set value = ? where name = 'Adobe_entityIDCounter'",
        (float(max(counter, next_id)),),
    )


def _key(columns, values) -> tuple:
    """Normalise a natural key so equal entities compare equal."""
    key = []
    for column, value in zip(columns, values):
        if column == "absolutePath" and isinstance(value, str):
            value = value.casefold()  # Windows paths are case-insensitive
        elif column == "copyName" and value is None:
            value = ""
        elif isinstance(value, float) and value.is_integer():
            value = int(value)
        key.append(value)
    return tuple(key)


def _note_newer(db: sqlite3.Connection, stats: MergeStats, source_touch, target_id) -> None:
    row = db.execute("select touchTime from main.Adobe_images where id_local = ?", (target_id,))
    target_touch = row.fetchone()
    if source_touch and target_touch and target_touch[0] and source_touch > target_touch[0]:
        stats.newer_duplicates += 1


def check_references(path: str) -> dict[str, int]:
    """Count references in ``path`` that point at no existing row."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ids: set[int] = set()
        for table in _tables(db, "main"):
            if "id_local" in _columns(db, "main", table):
                ids.update(
                    _as_id(r[0]) for r in db.execute(f'select id_local from "{table}"')
                )
        dangling: dict[str, int] = {}
        for table, refs in REFS.items():
            columns = set(_columns(db, "main", table))
            for column in refs:
                if column not in columns:
                    continue
                count = sum(
                    1
                    for (value,) in db.execute(f'select "{column}" from "{table}"')
                    if _as_id(value) is not None and _as_id(value) not in ids
                )
                if count:
                    dangling[f"{table}.{column}"] = count
        return dangling
    finally:
        db.close()


def integrity_check(path: str) -> str:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return db.execute("pragma integrity_check").fetchone()[0]
    finally:
        db.close()


def image_count(path: str) -> int:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return db.execute("select count(*) from Adobe_images").fetchone()[0]
    finally:
        db.close()
