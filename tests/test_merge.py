import sqlite3
import zipfile
from datetime import datetime

import pytest

from lrc_catalog_merge import backups, catalog

SCHEMA = """
create table Adobe_variablesTable (id_local integer primary key, id_global unique not null,
    name, type, value not null default '');
create table AgLibraryRootFolder (id_local integer primary key, id_global unique not null,
    absolutePath unique not null default '', name not null default '', relativePathFromCatalog);
create table AgLibraryFolder (id_local integer primary key, id_global unique not null,
    parentId integer, pathFromRoot not null default '', rootFolder integer not null default 0,
    visibility integer);
create table AgLibraryFile (id_local integer primary key, id_global unique not null,
    baseName not null default '', extension not null default '', folder integer not null default 0,
    idx_filename not null default '', lc_idx_filename not null default '');
create table Adobe_images (id_local integer primary key, id_global unique not null,
    copyName, developSettingsIDCache, masterImage integer, originalRootEntity integer,
    rating, rootFile integer not null default 0, touchTime);
create table Adobe_imageDevelopSettings (id_local integer primary key, image integer, text,
    beforeSettingsIDCache);
create table AgLibraryKeyword (id_local integer primary key, id_global unique not null,
    genealogy not null default '', lc_name, name, parent integer);
create unique index kw on AgLibraryKeyword (parent, lc_name);
create table AgLibraryKeywordImage (id_local integer primary key, image integer not null default 0,
    tag integer not null default 0);
create table AgLibraryFace (id_local integer primary key, cluster integer, image integer);
create table AgLibraryFaceData (id_local integer primary key, data, face integer);
"""


def make_catalog(path, uid, photos, keyword="cat", counter=1000, genealogy=True):
    """photos: list of (filename, rating). Ids start at ``uid`` to differ per catalog."""
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany(
        "insert into Adobe_variablesTable (id_local, id_global, name, value) values (?, ?, ?, ?)",
        [(1, f"v1-{uid}", "Adobe_entityIDCounter", float(counter)),
         (2, f"v2-{uid}", "Adobe_DBVersion", "1400000")],
    )
    i = uid
    db.execute("insert into AgLibraryRootFolder values (?, ?, 'Y:/trip/', 'trip', null)", (i, f"r{i}"))
    db.execute("insert into AgLibraryFolder values (?, ?, null, '', ?, null)", (i + 1, f"f{i}", i))
    db.execute("insert into AgLibraryKeyword values (?, ?, ?, null, null, null)",
               (i + 2, f"k{i}", f"/{len(str(i + 2))}{i + 2}"))
    # Child before root on purpose; genealogy is left for the merge to fix.
    db.execute("insert into AgLibraryKeyword values (?, ?, '', ?, ?, ?)",
               (i + 3, f"k{i + 3}", keyword, keyword, i + 2))
    if genealogy:
        db.execute("update AgLibraryKeyword set genealogy = ? where id_local = ?",
                   (f"/{len(str(i + 2))}{i + 2}/{len(str(i + 3))}{i + 3}", i + 3))
    next_id = i + 4
    for name, rating in photos:
        file_id, image_id, dev_id, kwi_id = next_id, next_id + 1, next_id + 2, next_id + 3
        next_id += 4
        db.execute("insert into AgLibraryFile values (?, ?, ?, 'ARW', ?, ?, ?)",
                   (file_id, f"file{file_id}", name, i + 1, f"{name}.ARW", f"{name}.arw".lower()))
        db.execute("insert into Adobe_images values (?, ?, null, ?, null, null, ?, ?, 1.0)",
                   (image_id, f"img{image_id}", float(dev_id), rating, file_id))
        db.execute("insert into Adobe_imageDevelopSettings values (?, ?, 'edit', null)",
                   (dev_id, image_id))
        db.execute("insert into AgLibraryKeywordImage values (?, ?, ?)", (kwi_id, image_id, i + 3))
    if photos:  # a face on the last photo, with non-UTF-8 TEXT data like Lightroom's
        db.execute("insert into AgLibraryFace values (?, null, ?)", (next_id, next_id - 3))
        db.execute("insert into AgLibraryFaceData values (?, cast(? as text), ?)",
                   (next_id + 1, b"\xff\xfe", next_id))
    db.commit()
    db.close()


def photos(path):
    db = sqlite3.connect(path)
    try:
        return dict(db.execute(
            "select f.baseName, i.rating from Adobe_images i join AgLibraryFile f on f.id_local = i.rootFile"
        ))
    finally:
        db.close()


def test_merge_adds_new_photos_and_keeps_target_duplicates(tmp_path):
    target, source = tmp_path / "t.lrcat", tmp_path / "s.lrcat"
    make_catalog(target, 10, [("a", 1), ("b", 2)])
    make_catalog(source, 10, [("b", 5), ("c", 3)], keyword="dog", genealogy=False)

    stats = catalog.merge_catalog(str(target), str(source))

    assert photos(target) == {"a": 1, "b": 2, "c": 3}
    assert stats.matched["AgLibraryRootFolder"] == 1
    assert stats.inserted["Adobe_images"] == 1
    assert catalog.integrity_check(str(target)) == "ok"
    assert catalog.check_references(str(target)) == {}

    db = sqlite3.connect(target)
    # The new photo's develop settings cache points at its own settings row.
    assert db.execute(
        "select count(*) from Adobe_images i join Adobe_imageDevelopSettings d on d.image = i.id_local "
        "where cast(i.developSettingsIDCache as integer) != d.id_local"
    ).fetchone() == (0,)
    # Keywords merge under the shared root and get a fresh genealogy.
    assert sorted(db.execute(
        "select k.name, k.genealogy like p.genealogy || '/%' from AgLibraryKeyword k "
        "join AgLibraryKeyword p on p.id_local = k.parent"
    )) == [("cat", 1), ("dog", 1)]
    # The duplicate photo also picks up the source's keyword.
    assert db.execute(
        "select count(*) from AgLibraryKeywordImage ki join Adobe_images i on i.id_local = ki.image "
        "join AgLibraryFile f on f.id_local = i.rootFile where f.baseName = 'b'"
    ).fetchone() == (2,)
    # Non-UTF-8 TEXT survives as TEXT.
    assert db.execute("select typeof(data), count(*) from AgLibraryFaceData group by 1").fetchall() == [
        ("text", 2)
    ]


def test_merge_is_idempotent(tmp_path):
    target, source = tmp_path / "t.lrcat", tmp_path / "s.lrcat"
    make_catalog(target, 10, [("a", 1)])
    make_catalog(source, 10, [("c", 3)])
    catalog.merge_catalog(str(target), str(source))
    again = catalog.merge_catalog(str(target), str(source))
    assert again.inserted["Adobe_images"] == 0
    assert again.inserted["AgLibraryKeywordImage"] == 0
    assert len(photos(target)) == 2


def test_merge_rejects_schema_mismatch(tmp_path):
    target, source = tmp_path / "t.lrcat", tmp_path / "s.lrcat"
    make_catalog(target, 10, [])
    make_catalog(source, 10, [])
    db = sqlite3.connect(source)
    db.execute("update Adobe_variablesTable set value = '1300000' where name = 'Adobe_DBVersion'")
    db.commit()
    db.close()
    with pytest.raises(catalog.MergeError):
        catalog.merge_catalog(str(target), str(source))


def test_latest_backup_and_next_stamp(tmp_path):
    for stamp in ("2026-09-09 0028", "2026-10-12 0825", "not-a-backup"):
        (tmp_path / stamp).mkdir()
        (tmp_path / stamp / "Lightroom Catalog.zip").touch()
    assert backups.latest(tmp_path).stamp == datetime(2026, 10, 12, 8, 25)
    assert backups.next_stamp([tmp_path], datetime(2026, 11, 1, 12, 0, 30)) == datetime(2026, 11, 1, 12, 0)
    # A clock behind the newest backup still yields a newer stamp.
    assert backups.next_stamp([tmp_path], datetime(2026, 1, 1)) == datetime(2026, 10, 12, 8, 26)


def test_write_keeps_backslash_layout(tmp_path):
    catalog_file = tmp_path / "x.lrcat"
    catalog_file.write_bytes(b"db")
    data = tmp_path / "x.lrcat-data"
    data.mkdir()
    (data / "CURRENT").write_text("MANIFEST-1\n")
    (data / "LOCK").touch()
    archive = backups.write(catalog_file, data, "Lightroom Catalog", "\\", tmp_path / "out",
                            datetime(2026, 11, 1, 12, 0))
    assert archive == tmp_path / "out" / "2026-11-01 1200" / "Lightroom Catalog.zip"
    assert zipfile.ZipFile(archive).namelist() == [
        "Lightroom Catalog.lrcat", "Lightroom Catalog.lrcat-data\\CURRENT",
    ]
    extracted, extracted_data = backups.extract(backups.latest(tmp_path / "out"), tmp_path / "x")
    assert extracted.read_bytes() == b"db"
    assert (extracted_data / "CURRENT").read_text() == "MANIFEST-1\n"
