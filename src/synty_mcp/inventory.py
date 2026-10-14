"""Adapter over synty-inventory.

synty-inventory owns scanning, naming rules, semantic enrichment, measured
bounds, kits and recipes. This module only:

* builds a ``PackRef`` for each extracted tree this server manages,
* calls ``catalog.scan_and_write`` / ``catalog.rebuild_index``,
* resolves, per catalog asset, the concrete files needed outside Unity
  (source model + node, textures in slot order, preview) into ``asset_files``,
* keeps an in-memory, mtime-invalidated view of the catalogs.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from synty_inventory import catalog as si_catalog
from synty_inventory.discover import PackRef

from . import unity
from .config import Settings
from .db import StateDB, dumps, loads, now

TREE_PRIORITY = ("unity", "source", "godot")


class CatalogCache:
    """All catalogs in memory; reloaded when ``index.json`` changes."""

    def __init__(self, catalogs_dir: Path):
        self.dir = catalogs_dir
        self._lock = threading.Lock()
        self._stamp: float | None = None
        self.docs: dict[str, dict] = {}
        self.by_key: dict[tuple[str, str], dict] = {}
        self.by_id: dict[str, list[tuple[str, str]]] = {}

    def _current_stamp(self) -> float | None:
        idx = self.dir / "index.json"
        try:
            return idx.stat().st_mtime_ns
        except OSError:
            return None

    def refresh(self, force: bool = False) -> None:
        stamp = self._current_stamp()
        if not force and stamp == self._stamp:
            return
        with self._lock:
            docs = {d["pack_id"]: d for d in si_catalog.load_all_catalogs(self.dir)}
            by_key: dict[tuple[str, str], dict] = {}
            by_id: dict[str, list[tuple[str, str]]] = {}
            for pid, doc in docs.items():
                for a in doc.get("assets") or []:
                    aid = a.get("id")
                    if not aid:
                        continue
                    by_key[(pid, aid)] = a
                    by_id.setdefault(aid.lower(), []).append((pid, aid))
            self.docs, self.by_key, self.by_id, self._stamp = docs, by_key, by_id, stamp

    def get(self, pack_id: str, asset_id: str) -> dict | None:
        self.refresh()
        return self.by_key.get((pack_id, asset_id))

    def resolve(self, ref: str) -> list[tuple[str, dict]]:
        """``PACK/ASSET`` or bare ``ASSET`` (case-insensitive) -> [(pack_id, asset)]."""
        self.refresh()
        ref = ref.strip()
        if "/" in ref:
            pid, aid = ref.split("/", 1)
            return [(p, self.by_key[(p, i)]) for p, i in self.by_id.get(aid.lower(), []) if p.lower() == pid.lower()]
        return [(p, self.by_key[(p, i)]) for p, i in self.by_id.get(ref.lower(), [])]

    def iter_assets(self, pack_id: str | None = None):
        self.refresh()
        for (pid, _aid), a in self.by_key.items():
            if pack_id and pid != pack_id:
                continue
            yield pid, a


def trees_for(db: StateDB, slug: str) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for r in db.q("SELECT kind, path FROM trees WHERE pack_slug=? ORDER BY added_at", (slug,)):
        p = Path(r["path"])
        if p.is_dir():
            out.setdefault(r["kind"], []).append(p)
    return out


def threejs_root(settings: Settings) -> Path:
    """Conversion cache for the default preset doubles as synty-inventory's
    ``threejs_v2`` tree, so its offline GLB measurement fills ``bounds``."""
    return settings.converted_dir / "threejs_web"


def write_glb_manifest(settings: Settings, pack_id: str) -> Path:
    root = threejs_root(settings) / pack_id
    root.mkdir(parents=True, exist_ok=True)
    models = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.glb"))
    path = root / "manifest.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"models": models}, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def scan_pack(settings: Settings, db: StateDB, slug: str, *, rebuild_index: bool = True, log=print) -> dict:
    pack = db.one("SELECT * FROM packs WHERE slug=?", (slug,))
    if pack is None:
        raise KeyError(f"unknown pack {slug}")
    trees = trees_for(db, slug)
    if not trees:
        return {"pack": slug, "status": "no_extracted_files", "assets": 0}
    primary_kind = next((k for k in TREE_PRIORITY if k in trees), next(iter(trees)))
    primary = trees[primary_kind][0]
    pack_id = pack["pack_id"]
    ref = PackRef(pack_id=pack_id, engine="Unity", extracted_path=primary)
    write_glb_manifest(settings, pack_id)
    log(f"synty-inventory scan {pack_id} from {primary_kind} tree {primary}")
    doc, errors = si_catalog.scan_and_write(
        ref, settings.catalogs_dir, threejs_v2=threejs_root(settings), include_shared=False
    )
    if rebuild_index:
        si_catalog.rebuild_index(settings.catalogs_dir)
    resolved = resolve_asset_files(settings, db, slug, pack_id, doc, trees, primary_kind, primary)
    count = len(doc.get("assets") or [])
    db.x(
        "UPDATE packs SET status='indexed', asset_count=?, indexed_at=? WHERE slug=?",
        (count, now(), slug),
    )
    return {
        "pack": slug,
        "pack_id": pack_id,
        "status": "indexed",
        "assets": count,
        "catalog_source": primary_kind,
        "trees": {k: [str(p) for p in v] for k, v in trees.items()},
        "resolved_models": resolved["models"],
        "resolved_textures": resolved["textures"],
        "validation_errors": errors[:20],
    }


def resolve_asset_files(
    settings: Settings,
    db: StateDB,
    slug: str,
    pack_id: str,
    doc: dict,
    trees: dict[str, list[Path]],
    primary_kind: str,
    primary: Path,
) -> dict:
    guids: dict[str, Path] = {}
    for t in trees.get("unity", []):
        guids.update(unity.index_guids(t))
    with db.tx() as con:
        con.execute("DELETE FROM guids WHERE pack_slug=?", (slug,))
        con.executemany(
            "INSERT OR REPLACE INTO guids(pack_slug, guid, path) VALUES(?,?,?)",
            [(slug, g, str(p)) for g, p in guids.items()],
        )

    # Source-file models / textures / material lists across every tree.
    models_by_stem: dict[str, Path] = {}
    textures: dict[str, Path] = {}
    material_list: dict[str, list[dict]] = {}
    material_meshes: dict[str, dict[str, list[dict]]] = {}
    for kind in ("source", "unity", "godot", "other"):
        for t in trees.get(kind, []):
            for p in t.rglob("*"):
                suf = p.suffix.lower()
                if suf in unity.MODEL_EXTS and p.is_file():
                    models_by_stem.setdefault(p.stem.lower(), p)
            textures.update({k: v for k, v in unity.texture_index(t).items() if k not in textures})
            for ml in t.rglob("MaterialList*.txt"):
                for k, v in unity.parse_material_list_meshes(ml).items():
                    material_meshes.setdefault(k, v)
                for k, v in unity.parse_material_list(ml).items():
                    material_list.setdefault(k, v)
    atlas = unity.default_atlas(textures)
    preview_dirs = [settings.library_dir / slug / "unity_previews"]

    rows = []
    n_models = n_tex = 0
    for a in doc.get("assets") or []:
        aid = a["id"]
        files = a.get("files") or {}
        mesh_path: Path | None = None
        node: str | None = None
        prefab_path: Path | None = None
        slots: list[str | None] = []
        by_material: dict[str, str] = {}
        if files.get("unity_prefab"):
            prefab_path = primary / files["unity_prefab"]
            if prefab_path.is_file() and guids:
                mesh_path, node = unity.prefab_model(prefab_path, guids)
                for mat in unity.prefab_material_paths(prefab_path, guids):
                    tex = unity.material_albedo(mat, guids)
                    slots.append(str(tex) if tex else None)
                    if tex:
                        by_material[mat.stem] = str(tex)
        if mesh_path is None and files.get("unity_mesh"):
            cand = primary / files["unity_mesh"]
            if cand.is_file():
                mesh_path = cand
        if mesh_path is None:
            mesh_path = models_by_stem.get(aid.lower())
        if mesh_path is not None:
            if mesh_path.stem.lower() == aid.lower():
                node = None  # the file *is* this asset: no filtering needed
            elif node is None:
                node = aid  # bundle model: keep only this asset's object
        ml = unity.lookup_material_list(material_list, aid, node or "")
        prefab_slots = bool(slots)
        resolved_slots = unity.resolve_material_slots(ml, textures) if ml else {}
        if ml:
            for name, path in resolved_slots["by_material"].items():
                by_material.setdefault(name, path)
            if not slots:
                slots = resolved_slots["slots"]
        meshes = material_meshes.get(aid) or material_meshes.get(node or "") or next(
            (v for k, v in material_meshes.items() if unity.norm_name(k) == unity.norm_name(aid)), {})
        by_mesh = {name: unity.resolve_material_slots(mesh_slots, textures) for name, mesh_slots in meshes.items()} if not prefab_slots else {}
        preview = next((d / f"{aid}.png" for d in preview_dirs if (d / f"{aid}.png").is_file()), None)
        tex_info = {
            "slots": slots,
            "by_material": by_material,
            "fallback": str(atlas) if atlas else None,
            "slot_states": resolved_slots.get("slot_states", []) if not prefab_slots else [],
            "material_states": resolved_slots.get("material_states", {}),
            "expected_textures": resolved_slots.get("expected_textures", []),
            "by_mesh": by_mesh,
            "search_dirs": sorted({str(p.parent) for p in textures.values()})[:20],
        }
        if mesh_path is not None:
            n_models += 1
        if slots or by_material:
            n_tex += 1
        rows.append(
            (
                pack_id, aid, slug, a.get("kind"), a.get("type"),
                str(mesh_path) if mesh_path else None, node,
                str(prefab_path) if prefab_path and prefab_path.is_file() else None,
                dumps(tex_info), str(preview) if preview else None,
                dumps({"catalog_source": primary_kind}),
            )
        )
    # A model file that other assets pick nodes out of is a *bundle* (e.g.
    # Characters.fbx): cataloged by synty-inventory, but never a scene pick.
    bundled = {r[5] for r in rows if r[5] and r[6]}
    rows = [r[:10] + (dumps({"catalog_source": primary_kind, "bundle": bool(r[5] in bundled and not r[6])}),)
            for r in rows]
    with db.tx() as con:
        con.execute("DELETE FROM asset_files WHERE pack_id=?", (pack_id,))
        con.executemany(
            "INSERT INTO asset_files(pack_id, asset_id, pack_slug, kind, type, mesh_path, mesh_node, prefab_path,"
            " textures, preview_path, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    return {"models": n_models, "textures": n_tex}


def asset_files(db: StateDB, pack_id: str, asset_id: str) -> dict | None:
    r = db.one("SELECT * FROM asset_files WHERE pack_id=? AND asset_id=?", (pack_id, asset_id))
    if r:
        r["textures"] = loads(r["textures"], {})
        r["extra"] = loads(r["extra"], {})
    return r


def refresh_after_conversion(settings: Settings, db: StateDB, pack_id: str) -> None:
    """Re-run the synty-inventory scan for one pack so newly converted GLBs
    feed its measured ``bounds`` / footprint. Cheap: measurement is cached."""
    write_glb_manifest(settings, pack_id)
    row = db.one("SELECT slug FROM packs WHERE pack_id=?", (pack_id,))
    if row:
        scan_pack(settings, db, row["slug"], log=lambda *_: None)
