"""Library management: import archives (deduplicated by SHA-256), extract
them into an organised tree, and register existing folders in place."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import archives, packs
from .config import Settings
from .db import StateDB, now
from .safepaths import confine, safe_name

ARCHIVE_SUFFIXES = (".zip", ".unitypackage")


def _link_or_copy(src: Path, dst: Path) -> str:
    """Store an immutable copy of the original. Hard link when on the same
    filesystem (zero extra space), else a streamed copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return "exists"
    try:
        os.link(src, dst)
        mode = "hardlink"
    except OSError:
        tmp = dst.with_name(dst.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        mode = "copy"
    try:
        os.chmod(dst, 0o444)
    except OSError:
        pass
    return mode


def import_archive(
    settings: Settings,
    db: StateDB,
    path: Path,
    *,
    pack: str | None = None,
    origin: str = "manual",
    verify: bool = True,
    progress=None,
    log=print,
) -> dict:
    """Register one archive. Idempotent: the same bytes are never stored twice."""
    path = Path(path).expanduser().resolve()
    if not path.is_file() or not path.name.lower().endswith(ARCHIVE_SUFFIXES):
        raise ValueError(f"not an archive (.zip/.unitypackage): {path}")
    log(f"hashing {path.name}")
    digest = archives.sha256_file(path, progress)
    existing = db.one("SELECT * FROM archives WHERE sha256=?", (digest,))
    if existing:
        return {"status": "already_imported", "archive": existing}
    if pack:
        row = packs.get_pack(db, pack)
        if row is None:
            raise KeyError(f"unknown pack {pack!r}")
        slug, score = row["slug"], 999
    else:
        slug, score = packs.match_pack(db, path.name)
        if not slug:
            slug = packs.ensure_pack(db, path.name, source=f"archive:{path.name}")
            score = 1 if slug else 0
    ok, msg = archives.verify_archive(path) if verify else (True, "not verified")
    kind = packs.guess_kind(path.name)
    version = packs.guess_version(path.name)
    dest_dir = settings.archives_dir / (slug or "_unmatched")
    stored = dest_dir / safe_name(path.name, 200)
    if stored.exists() and archives.sha256_file(stored) != digest:
        stored = dest_dir / f"{digest[:12]}_{safe_name(path.name, 180)}"
    mode = _link_or_copy(path, stored)
    archive_id = db.x(
        "INSERT INTO archives(sha256, filename, path, size, pack_slug, kind, version, origin, verified, imported_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (digest, path.name, str(stored), path.stat().st_size, slug, kind, version, origin,
         "ok" if ok else f"failed: {msg}", now()),
    )
    if slug:
        real_id = packs.pack_id_from_filename(path.name)
        if real_id:
            db.x("UPDATE packs SET pack_id=?, pack_id_confirmed=1 WHERE slug=? AND pack_id_confirmed=0", (real_id, slug))
        db.x("UPDATE packs SET status='downloaded' WHERE slug=? AND status='owned_not_downloaded'", (slug,))
    return {
        "status": "imported" if ok else "imported_but_corrupt",
        "archive_id": archive_id,
        "pack": slug,
        "match_score": score,
        "kind": kind,
        "version": version,
        "sha256": digest,
        "verify": msg,
        "stored_as": mode,
        "warning": None if slug else "filename did not match an owned pack; pass pack= to assign it",
    }


def import_inbox(settings: Settings, db: StateDB, log=print) -> list[dict]:
    out = []
    for p in sorted(settings.inbox_dir.rglob("*")):
        if p.is_file() and p.name.lower().endswith(ARCHIVE_SUFFIXES):
            try:
                out.append({"file": p.name, **import_archive(settings, db, p, log=log)})
            except Exception as exc:  # noqa: BLE001 - report per file, keep going
                out.append({"file": p.name, "status": "error", "error": str(exc)})
    return out


def _register_tree(db: StateDB, slug: str, kind: str, path: Path, archive_id: int | None, external: bool) -> None:
    db.x(
        "INSERT OR REPLACE INTO trees(pack_slug, kind, path, archive_id, external, added_at) VALUES(?,?,?,?,?,?)",
        (slug, kind, str(path), archive_id, 1 if external else 0, now()),
    )


def extract_archive(settings: Settings, db: StateDB, archive_id: int, *, force: bool = False,
                    progress=None, log=print) -> dict:
    arc = db.one("SELECT * FROM archives WHERE id=?", (archive_id,))
    if arc is None:
        raise KeyError(f"unknown archive {archive_id}")
    if not arc["pack_slug"]:
        raise ValueError("archive is not assigned to a pack; re-import with pack=")
    if (arc["verified"] or "").startswith("failed"):
        raise ValueError(f"archive failed integrity check: {arc['verified']}")
    if arc["extracted_at"] and arc["extracted_path"] and Path(arc["extracted_path"]).exists() and not force:
        return {"status": "already_extracted", "path": arc["extracted_path"]}
    slug = arc["pack_slug"]
    src = Path(arc["path"])
    pack_dir = settings.library_dir / slug
    lim = dict(max_bytes=settings.max_extract_bytes, max_files=settings.max_extract_files)
    result: dict = {"archive_id": archive_id, "pack": slug, "trees": []}
    unity_dest = pack_dir / "unity"
    previews = pack_dir / "unity_previews"

    def unpack_unity(pkg: Path) -> None:
        log(f"unpacking unitypackage {pkg.name}")
        rep = archives.extract_unitypackage(pkg, unity_dest, previews, progress=progress, **lim)
        _register_tree(db, slug, "unity", unity_dest, archive_id, False)
        result["trees"].append({"kind": "unity", **rep.as_dict()})

    if src.name.lower().endswith(".unitypackage"):
        unpack_unity(src)
        extracted = unity_dest
    else:
        dest = pack_dir / f"{arc['kind'] or 'files'}_{archive_id}"
        if force and dest.exists():
            archives.remove_tree(dest)
        tmp = settings.tmp_dir / f"arc_{archive_id}"
        archives.remove_tree(tmp)
        log(f"extracting {src.name}")
        rep = archives.extract_zip(src, dest, divert_dir=tmp, progress=progress, **lim)
        nested = sorted(rep.nested_unitypackages)
        # Several render-pipeline variants may ship together; one is enough
        # for meshes/textures. Prefer Built-in, then URP.
        nested.sort(key=lambda p: (0 if "built" in p.lower() else 1 if "urp" in p.lower() else 2, p))
        for pkg in nested[:1]:
            unpack_unity(Path(pkg))
        if len(nested) > 1:
            result["skipped_unitypackages"] = [Path(p).name for p in nested[1:]]
        archives.remove_tree(tmp)
        if rep.files:
            kind = archives.classify_tree(dest)
            _register_tree(db, slug, kind, dest, archive_id, False)
            result["trees"].append({"kind": kind, **rep.as_dict()})
        elif dest.exists() and not any(dest.iterdir()):
            dest.rmdir()
        extracted = dest if rep.files else unity_dest
    db.x("UPDATE archives SET extracted_path=?, extracted_at=? WHERE id=?", (str(extracted), now(), archive_id))
    db.x("UPDATE packs SET status='extracted' WHERE slug=? AND status IN ('owned_not_downloaded','downloaded')", (slug,))
    result["status"] = "extracted"
    return result


def register_folder(settings: Settings, db: StateDB, path: Path, pack: str | None = None) -> dict:
    """Use an already-extracted folder in place (read-only). It must be inside a
    configured ``source_roots`` entry so the server never reads arbitrary paths."""
    path = confine(path, settings.source_roots + [settings.library_dir], must_exist=True)
    if pack:
        row = packs.get_pack(db, pack)
        if row is None:
            raise KeyError(f"unknown pack {pack!r}")
        slug = row["slug"]
    else:
        names = [path.name, path.parent.name] + [p.name for p in path.glob("MaterialList*.txt")]
        slug = None
        best = 0
        for n in names:
            s, score = packs.match_pack(db, n)
            if s and score > best:
                slug, best = s, score
        if not slug:
            raise ValueError("could not infer the pack from the folder; pass pack=")
    kind = archives.classify_tree(path)
    _register_tree(db, slug, kind, path, None, True)
    db.x("UPDATE packs SET status='extracted' WHERE slug=? AND status IN ('owned_not_downloaded','downloaded')", (slug,))
    return {"status": "registered", "pack": slug, "kind": kind, "path": str(path), "read_only": True}
