"""Service facade: one method per MCP tool. Shared by the MCP server (stdio
and Streamable HTTP) and the dashboard's REST API, so both see the same
behaviour. Every method returns JSON-serialisable data."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from . import inventory, library, packs
from .config import Settings
from .convert import PRESETS, ConversionError, Converter, copy_file, plausible_max_m, resolve_preset
from .db import StateDB, dumps, loads
from .inventory import CatalogCache
from .jobs import JobManager
from .safepaths import PathViolation, confine, safe_name
from .search import Searcher, asset_class
from .store import SyntyStore
from .syntydex import SyntyDexProvider

LICENCE_NOTE = (
    "Synty Studios assets licensed to the library owner. Use only inside the owner's own products; "
    "do not redistribute source files, do not use for generative-AI datasets or 3D model generation. "
    "Respect the seat count of your licence."
)


class ServiceError(Exception):
    pass


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        self.db = StateDB(settings.state_db)
        self.owned_packs_source = settings.owned_packs_path()
        if self.owned_packs_source:  # no list = packs are discovered from the store and imported archives
            packs.seed_packs(self.db, self.owned_packs_source)
        self.cache = CatalogCache(settings.catalogs_dir)
        self.searcher = Searcher(self)
        self.converter = Converter(settings, self.db)
        self.jobs = JobManager(self.db, settings.workers)
        self.store = SyntyStore(settings, self.db)
        self.dex = SyntyDexProvider(settings, self)
        self.serving_http = False  # set by http_app: enables relative /files URLs

    # ------------------------------------------------------------------ utils
    def file_url(self, path: str | Path | None) -> str | None:
        """Authenticated download URL for a served file (HTTP mode only)."""
        if not path or not (self.settings.public_base_url or self.serving_http):
            return None
        base = (self.settings.public_base_url or "").rstrip("/")
        p = Path(path).resolve()
        for name, root in (("converted", self.settings.converted_dir), ("collections", self.settings.collections_dir),
                           ("previews", self.settings.previews_dir), ("library", self.settings.library_dir)):
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            return f"{base}/files/{name}/{quote(rel.as_posix())}"
        return None

    def _one_asset(self, ref: str) -> tuple[str, dict]:
        hits = self.cache.resolve(ref)
        if not hits:
            raise ServiceError(f"asset not found in the indexed catalogs: {ref!r} (search_assets first)")
        if len(hits) > 1:
            raise ServiceError("ambiguous id, present in several packs; use PACK/ID: "
                               + ", ".join(f"{p}/{a['id']}" for p, a in hits))
        return hits[0]

    def _wait(self, job: dict, wait: bool, timeout: float) -> dict:
        if not wait or job.get("status") in ("done", "failed"):
            return job
        return self.jobs.wait(job["job_id"], timeout)

    # ------------------------------------------------------------- library
    def list_owned_packs(self) -> dict:
        out = []
        for p in packs.pack_rows(self.db):
            arcs = self.db.q("SELECT id, filename, kind, version, size, verified, extracted_at FROM archives "
                             "WHERE pack_slug=? ORDER BY imported_at", (p["slug"],))
            trees = self.db.q("SELECT kind, path, external FROM trees WHERE pack_slug=?", (p["slug"],))
            store_items = self.db.q("SELECT link_ref, title, filename, kind, archive_id FROM store_items "
                                    "WHERE pack_slug=?", (p["slug"],))
            out.append({
                "pack": p["slug"],
                "name": f"{p['line']} {p['name']}",
                "line": p["line"],
                "pack_id": p["pack_id"],
                "pack_id_confirmed": bool(p["pack_id_confirmed"]),
                "content": p["content"],
                "status": p["status"],
                "indexed_assets": p["asset_count"],
                "archives": arcs,
                "trees": [{"kind": t["kind"], "read_only": bool(t["external"])} for t in trees],
                "store_downloads": store_items,
            })
        counts: dict[str, int] = {}
        for p in out:
            counts[p["status"]] = counts.get(p["status"], 0) + 1
        return {"owned": len(out), "by_status": counts, "packs": out,
                "pack_list": str(self.owned_packs_source) if self.owned_packs_source else
                "no owned_packs.yaml: packs are discovered from Synty Store downloads and imported archives",
                "note": "assets are only listed for packs with status 'indexed'"}

    def sync_owned_packs(self, wait: bool = True, timeout: float = 900) -> dict:
        imported = library.import_inbox(self.settings, self.db, log=lambda *_: None)
        store = self.store.status()

        def work(ctx):
            listed = self.store.list_purchases(log=ctx.log, progress=ctx.progress)
            by_pack: dict[str, list] = {}
            for f in listed:
                by_pack.setdefault(f["pack"] or "_unmatched", []).append(
                    {k: f[k] for k in ("link_ref", "kind", "variant", "size_bytes")})
            return {"purchased_packs": len(by_pack), "files": len(listed), "by_pack": by_pack}

        job = None
        if store["available"]:
            job = self._wait(self.jobs.submit("sync_store", {}, work, dedupe_key="sync_store"), wait, timeout)
        owned = self.list_owned_packs()
        return {
            "inbox_imports": imported,
            "store": store,
            "store_sync": job,
            "packs": [{k: p[k] for k in ("pack", "name", "status", "indexed_assets")} for p in owned["packs"]],
            "by_status": owned["by_status"],
        }

    def download_pack(self, pack: str, *, kinds: list[str] | None = None, extract: bool = True,
                      wait: bool = False, timeout: float = 30) -> dict:
        row = packs.get_pack(self.db, pack)
        if row is None:
            raise ServiceError(f"unknown pack {pack!r}")
        st = self.store.status()
        if not st["available"]:
            return {"status": "store_unavailable", **st, "pack": row["slug"],
                    "manual_import": f"put the pack's .zip/.unitypackage in {self.settings.inbox_dir} "
                                     f"then call sync_owned_packs"}
        items = self.db.q("SELECT * FROM store_items WHERE pack_slug=?", (row["slug"],))
        want = set(kinds or ["unity", "source"])
        # One file per kind: the newest pack version, then the newest engine version.
        best: dict[str, dict] = {}
        for it in items:
            if it["kind"] not in want:
                continue
            key = _version_key(it.get("variant") or it.get("filename") or "")
            if it["kind"] not in best or key > _version_key(best[it["kind"]].get("variant") or ""):
                best[it["kind"]] = it
        items = list(best.values())
        if not items:
            return {"status": "no_store_links", "pack": row["slug"],
                    "hint": "run sync_owned_packs; if the pack still has no files its title did not match"}
        pending = [i for i in items if not i["archive_id"]]
        if not pending:
            return {"status": "already_downloaded", "pack": row["slug"],
                    "archives": [i["archive_id"] for i in items]}

        def work(ctx):
            results = []
            for it in pending:
                ctx.log(f"downloading {it['title']}")
                f = self.store.download(it["link_ref"], self.settings.inbox_dir / "_store", progress=ctx.progress,
                                        log=ctx.log)
                res = library.import_archive(self.settings, self.db, f, pack=row["slug"], origin="store",
                                             log=ctx.log)
                arc_id = res.get("archive_id") or (res.get("archive") or {}).get("id")
                self.db.x("UPDATE store_items SET archive_id=? WHERE id=?", (arc_id, it["id"]))
                results.append(res)
            if extract:
                results.append(self._extract_and_scan(ctx, row["slug"], force=False))
            return {"pack": row["slug"], "steps": results}

        job = self.jobs.submit("download_pack", {"pack": row["slug"]}, work, dedupe_key=f"download:{row['slug']}")
        return self._wait(job, wait, timeout)

    def _extract_and_scan(self, ctx, slug: str, force: bool, archive_id: int | None = None) -> dict:
        q = "SELECT id FROM archives WHERE pack_slug=? AND verified='ok'"
        args: list = [slug]
        if archive_id:
            q += " AND id=?"
            args.append(archive_id)
        done = []
        for a in self.db.q(q, args):
            done.append(library.extract_archive(self.settings, self.db, a["id"], force=force,
                                                progress=ctx.progress, log=ctx.log))
        scan = inventory.scan_pack(self.settings, self.db, slug, log=ctx.log)
        self.cache.refresh(force=True)
        return {"extracted": done, "scan": scan}

    def extract_pack(self, pack: str, *, archive_id: int | None = None, force: bool = False, wait: bool = True,
                     timeout: float = 120) -> dict:
        row = packs.get_pack(self.db, pack)
        if row is None:
            raise ServiceError(f"unknown pack {pack!r}")
        if not self.db.one("SELECT id FROM archives WHERE pack_slug=?", (row["slug"],)):
            return {"status": "no_archives", "pack": row["slug"],
                    "hint": f"import the pack first (inbox: {self.settings.inbox_dir})"}
        job = self.jobs.submit("extract_pack", {"pack": row["slug"], "archive_id": archive_id, "force": force},
                               lambda ctx: self._extract_and_scan(ctx, row["slug"], force, archive_id),
                               dedupe_key=f"extract:{row['slug']}")
        return self._wait(job, wait, timeout)

    def import_archive(self, path: str, pack: str | None = None, extract: bool = True, wait: bool = True,
                       timeout: float = 300) -> dict:
        p = confine(path, [self.settings.inbox_dir, *self.settings.source_roots], must_exist=True)

        def work(ctx):
            res = library.import_archive(self.settings, self.db, p, pack=pack, log=ctx.log, progress=ctx.progress)
            slug = res.get("pack") or (res.get("archive") or {}).get("pack_slug")
            if extract and slug and res.get("status") in ("imported", "already_imported"):
                res["extract"] = self._extract_and_scan(ctx, slug, force=False)
            return res

        job = self.jobs.submit("import_archive", {"path": str(p)}, work, dedupe_key=f"import:{p}")
        return self._wait(job, wait, timeout)

    def scan_library(self, pack: str | None = None, register_path: str | None = None, wait: bool = True,
                     timeout: float = 300) -> dict:
        registered = None
        if register_path:
            try:
                registered = library.register_folder(self.settings, self.db, Path(register_path), pack)
            except PathViolation as exc:
                raise ServiceError(f"{exc}; add the folder to source_roots in the config first") from exc
        slugs = []
        if pack:
            row = packs.get_pack(self.db, pack)
            if row is None:
                raise ServiceError(f"unknown pack {pack!r}")
            slugs = [row["slug"]]
        else:
            slugs = [r["pack_slug"] for r in self.db.q("SELECT DISTINCT pack_slug FROM trees")]

        def work(ctx):
            out = []
            for i, s in enumerate(slugs):
                ctx.progress(i / max(1, len(slugs)), f"scanning {s}")
                out.append(inventory.scan_pack(self.settings, self.db, s, log=ctx.log))
            self.cache.refresh(force=True)
            return {"registered": registered, "scanned": out}

        job = self.jobs.submit("scan_library", {"packs": slugs}, work, dedupe_key=f"scan:{','.join(slugs)}")
        return self._wait(job, wait, timeout)

    def get_library_status(self) -> dict:
        self.cache.refresh()
        owned = self.list_owned_packs()

        def du(p: Path) -> int:
            if not p.exists():
                return 0
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

        conv = self.db.q("SELECT preset, status, COUNT(*) AS n FROM conversions GROUP BY preset, status")
        return {
            "packs_owned": owned["owned"],
            "packs_by_status": owned["by_status"],
            "indexed_assets": sum(1 for _ in self.cache.iter_assets()),
            "indexed_packs": sorted(self.cache.docs),
            "conversions": conv,
            "jobs_active": self.jobs.list("running") + self.jobs.list("queued"),
            "disk_bytes": {"archives": du(self.settings.archives_dir), "library": du(self.settings.library_dir),
                           "converted": du(self.settings.converted_dir)},
            "blender": self.settings.blender_executable(),
            "providers": {"synty_store": self.store.status(), "syntydex": self.dex.status()},
            "config": self.settings.public_dict(),
        }

    # -------------------------------------------------------------- search
    def search_assets(self, query: str, pack: str | None = None, asset_class: str | None = None,
                      available_only: bool = False, include_nonplaceable: bool = False, limit: int = 10) -> dict:
        pid = None
        if pack:
            row = packs.get_pack(self.db, pack)
            pid = row["pack_id"] if row else pack
        res = self.searcher.search(query, pack=pid, asset_class_filter=asset_class, available_only=available_only,
                                   include_nonplaceable=include_nonplaceable, limit=max(1, min(limit, 100)))
        if not res["results"]:
            from . import lexicon

            res["owned_unindexed_candidates"] = self.searcher.unindexed_pack_hints(lexicon.analyse_phrase(query))
            res["note"] = "no indexed asset matches; nothing is invented. See owned_unindexed_candidates."
        return res

    def get_asset_details(self, ref: str, include_footprint: bool = False) -> dict:
        pid, a = self._one_asset(ref)
        rec = {k: v for k, v in a.items() if k not in ("_auto", "paths") and (include_footprint or k != "footprint")}
        files = inventory.asset_files(self.db, pid, a["id"]) or {}
        conv = self.db.q("SELECT preset, output_path, info, created_at FROM conversions WHERE pack_id=? AND asset_id=? "
                         "AND status='ok'", (pid, a["id"]))
        conversions = []
        for c in conv:
            info = loads(c["info"], {})
            conversions.append({"preset": c["preset"], "path": c["output_path"], "url": self.file_url(c["output_path"]),
                                "glb": info.get("glb"), "warnings": info.get("warnings")})
        return {
            **self.searcher.row(pid, a),
            "record": rec,
            "files": {
                "source_model": files.get("mesh_path"),
                "node_in_model": files.get("mesh_node"),
                "unity_prefab": files.get("prefab_path"),
                "preview": files.get("preview_path"),
                "textures": (files.get("textures") or {}).get("slots"),
            },
            "geometry": next((c["glb"] for c in conversions if c.get("glb")), None),
            "conversions": conversions,
            "presets": sorted(PRESETS),
        }

    def find_similar_assets(self, ref: str, limit: int = 10) -> dict:
        return self.searcher.similar(ref, limit=limit)

    def suggest_assets_for_scene(self, request: str, available_only: bool = False, alternatives: int = 4) -> dict:
        return self.searcher.suggest_for_scene(request, per_item_alternatives=alternatives,
                                               available_only=available_only)

    def list_pack_assets(self, pack: str, asset_class: str | None = None, query: str | None = None,
                         offset: int = 0, limit: int = 100) -> dict:
        row = packs.get_pack(self.db, pack)
        if row is None:
            raise ServiceError(f"unknown pack {pack!r}")
        if row["status"] != "indexed":
            return {"pack": row["slug"], "pack_id": row["pack_id"], "status": row["status"], "assets": [],
                    "note": "pack is owned but not indexed yet: download/extract/scan it first"}
        res = self.searcher.list_pack(row["pack_id"], asset_class_filter=asset_class, offset=offset,
                                      limit=max(1, min(limit, 500)), query=query)
        return {"pack": row["slug"], **res}

    def get_asset_preview(self, ref: str, generate: bool = False) -> dict:
        pid, a = self._one_asset(ref)
        files = inventory.asset_files(self.db, pid, a["id"]) or {}
        rendered = self.settings.previews_dir / safe_name(pid) / f"{safe_name(a['id'])}.png"
        path = None
        source = None
        if files.get("preview_path") and Path(files["preview_path"]).is_file():
            path, source = files["preview_path"], "unity_package_thumbnail"
        elif rendered.is_file():
            path, source = str(rendered), "rendered"
        elif generate:
            glb = self.convert_asset_to_glb(ref, preset="threejs_web", wait=True, timeout=300)
            res = glb.get("result") or {}
            if glb.get("status") != "done" or not res.get("path"):
                return {"ref": ref, "available": False, "error": glb.get("error") or "conversion did not finish",
                        "job": glb.get("job_id")}
            self.converter.render_preview(Path(res["path"]), rendered, log=lambda *_: None)
            path, source = str(rendered), "rendered"
        out = {
            "ref": f"{pid}/{a['id']}",
            "available": bool(path),
            "source": source,
            "path": path,
            "url": self.file_url(path),
            "viewer": (f"{self.settings.public_base_url.rstrip('/')}/ui/#asset={quote(pid + '/' + a['id'])}"
                       if self.settings.public_base_url else None),
            "image_inline": False,
        }
        if not path:
            out["hint"] = "no thumbnail yet: call again with generate=true (renders locally with Blender)"
        if path and not self.settings.allow_preview_images_to_agent:
            out["note"] = ("preview kept local: image bytes are not sent into the agent context "
                           "(allow_preview_images_to_agent=false). Open the path/url/viewer instead.")
        return out

    # ---------------------------------------------------------- conversion
    def _convert_job(self, pid: str, a: dict, preset: str, options: dict | None, force: bool, refresh: bool = True):
        files = inventory.asset_files(self.db, pid, a["id"])
        if not files or not files.get("mesh_path"):
            cls = asset_class(a)
            why = ("Unity-only asset (prefab without a convertible model file)" if files and files.get("prefab_path")
                   else "no source model file found in the extracted pack")
            if cls in ("effect", "ui", "texture"):
                why = f"{cls} assets are Unity components/shaders and are not converted automatically"
            raise ConversionError(f"{pid}/{a['id']}: {why}")
        params = resolve_preset(preset, options)

        def work(ctx):
            res = self.converter.convert(pack_id=pid, asset_id=a["id"], src=Path(files["mesh_path"]),
                                         node=files.get("mesh_node"), textures=files.get("textures") or {},
                                         preset=preset, options=options, force=force,
                                         max_plausible_m=plausible_max_m(a.get("type")), log=ctx.log)
            res["url"] = self.file_url(res["path"])
            if refresh and preset == "threejs_web" and res["status"] == "converted":
                inventory.write_glb_manifest(self.settings, pid)
            return res

        key = f"convert:{pid}/{a['id']}:{preset}:{hashlib.sha1(dumps(params).encode()).hexdigest()[:8]}"
        return work, key

    def convert_asset_to_glb(self, ref: str, preset: str = "threejs_web", options: dict | None = None,
                             force: bool = False, wait: bool = True, timeout: float = 120) -> dict:
        pid, a = self._one_asset(ref)
        work, key = self._convert_job(pid, a, preset, options, force)
        job = self.jobs.submit("convert", {"ref": f"{pid}/{a['id']}", "preset": preset, "options": options},
                               work, dedupe_key=key)
        return self._wait(job, wait, timeout)

    def optimize_asset(self, ref: str, preset: str = "threejs_mobile", max_texture: int | None = None,
                       decimate: float | None = None, draco: bool | None = None, wait: bool = True,
                       timeout: float = 120) -> dict:
        opts = {k: v for k, v in {"max_texture": max_texture, "decimate": decimate, "draco": draco}.items()
                if v is not None}
        res = self.convert_asset_to_glb(ref, preset=preset, options=opts or None, wait=wait, timeout=timeout)
        if res.get("status") == "done":
            base = self.db.one("SELECT info FROM conversions WHERE pack_id=? AND asset_id=? AND preset='original_quality'"
                               " AND status='ok'", tuple(res["params"]["ref"].split("/", 1)))
            if base:
                b = (loads(base["info"], {}).get("glb") or {})
                n = (res["result"].get("glb") or {})
                res["comparison_vs_original_quality"] = {
                    "bytes": [b.get("bytes"), n.get("bytes")], "triangles": [b.get("triangles"), n.get("triangles")]}
        return res

    def get_asset_file(self, ref: str, format: str = "glb", preset: str = "threejs_web", wait: bool = True,
                       timeout: float = 120) -> dict:
        pid, a = self._one_asset(ref)
        files = inventory.asset_files(self.db, pid, a["id"]) or {}
        if format == "source":
            p = files.get("mesh_path") or files.get("prefab_path")
            if not p or not Path(p).is_file():
                return {"ref": f"{pid}/{a['id']}", "available": False,
                        "reason": "source file not on disk (pack not extracted?)"}
            return {"ref": f"{pid}/{a['id']}", "available": True, "format": Path(p).suffix.lstrip("."),
                    "path": p, "bytes": Path(p).stat().st_size, "url": self.file_url(p),
                    "node_in_model": files.get("mesh_node"), "textures": (files.get("textures") or {}).get("slots"),
                    "licence": LICENCE_NOTE}
        if format != "glb":
            raise ServiceError("format must be 'glb' or 'source'")
        job = self.convert_asset_to_glb(ref, preset=preset, wait=wait, timeout=timeout)
        if job.get("status") != "done":
            return {"ref": f"{pid}/{a['id']}", "available": False, "job": job}
        r = job["result"]
        return {"ref": f"{pid}/{a['id']}", "available": True, "format": "glb", "preset": preset,
                "path": r["path"], "url": r.get("url"), "glb": r.get("glb"), "warnings": r.get("warnings"),
                "licence": LICENCE_NOTE}

    def get_asset_dependencies(self, ref: str) -> dict:
        pid, a = self._one_asset(ref)
        files = inventory.asset_files(self.db, pid, a["id"]) or {}
        tex = files.get("textures") or {}
        deps: dict = {
            "ref": f"{pid}/{a['id']}",
            "model": {"path": files.get("mesh_path"), "node": files.get("mesh_node"),
                      "shared_bundle": bool(files.get("mesh_node"))},
            "unity_prefab": files.get("prefab_path"),
            "materials": tex.get("by_material") or {},
            "textures_by_slot": tex.get("slots") or [],
            "fallback_atlas": tex.get("fallback"),
            "embedded_in_glb": "textures are embedded in converted GLBs; no external files needed",
        }
        module = a.get("module") or {}
        if module.get("family"):
            kin = [(p2, b) for p2, b in self.cache.iter_assets(pid) if (b.get("module") or {}).get("family") == module["family"]]
            roles: dict[str, list[str]] = {}
            for _p2, b in kin:
                roles.setdefault((b.get("module") or {}).get("role") or "other", []).append(b["id"])
            deps["modular_kit"] = {"family": module["family"], "role": module.get("role"),
                                   "pieces_by_role": {k: sorted(v)[:30] for k, v in roles.items()},
                                   "note": "a finished building is assembled from these pieces"}
        if asset_class(a) == "character":
            anim = [r["slug"] for r in self.db.q("SELECT slug FROM packs WHERE content='animation'")]
            deps["animation"] = {
                "rig": "humanoid rig in the model (Synty)",
                "owned_animation_packs": anim,
                "note": "retargeting between rigs is not automatic; Synty ANIMATION packs target Synty humanoids",
            }
        return deps

    # ---------------------------------------------------------- collections
    def prepare_asset_collection(self, refs: list, name: str = "collection", preset: str = "threejs_web",
                                 engine: str = "threejs", export_to: str | None = None, zip_output: bool = True,
                                 godot_res_root: str = "res://synty", wait: bool = True,
                                 timeout: float = 600) -> dict:
        resolve_preset(preset, None)
        items = []
        missing = []
        for r in refs:
            spec = {"ref": r} if isinstance(r, str) else dict(r)
            try:
                pid, a = self._one_asset(spec["ref"])
                items.append((spec, pid, a))
            except ServiceError as exc:
                missing.append({"ref": spec.get("ref"), "reason": str(exc)})
        cname = safe_name(name)
        export_dir = None
        if export_to:
            try:
                export_dir = confine(export_to, self.settings.export_roots or [self.settings.collections_dir])
            except PathViolation as exc:
                raise ServiceError(f"{exc}; add the project folder to export_roots in the config") from exc

        def work(ctx):
            base = self.settings.collections_dir / cname
            models = base / "models"
            models.mkdir(parents=True, exist_ok=True)
            entries = []
            failed = list(missing)
            seen: set[str] = set()
            for i, (spec, pid, a) in enumerate(items):
                ctx.progress(i / max(1, len(items)), f"{pid}/{a['id']}")
                ref = f"{pid}/{a['id']}"
                if ref in seen:
                    for e in entries:
                        if e["ref"] == ref:
                            e["count"] += int(spec.get("qty") or 1)
                    continue
                seen.add(ref)
                try:
                    work_fn, _key = self._convert_job(pid, a, preset, None, False)
                    res = work_fn(ctx)
                except (ConversionError, ValueError) as exc:
                    failed.append({"ref": ref, "reason": str(exc)})
                    continue
                fname = f"{safe_name(pid)}__{safe_name(a['id'])}.glb"
                copy_file(Path(res["path"]), models / fname)
                g = res.get("glb") or {}
                entry = {
                    "ref": ref, "id": a["id"], "name": a.get("name"), "pack_id": pid,
                    "class": asset_class(a), "type": a.get("type"), "count": int(spec.get("qty") or 1),
                    "file": f"models/{fname}", "bytes": g.get("bytes"),
                    "triangles": g.get("triangles"), "vertices": g.get("vertices"),
                    "bounds_m": g.get("bounds"), "materials": g.get("materials"), "animations": g.get("animations"),
                    "skinned": bool(g.get("skins")), "extensions_required": g.get("extensions_required"),
                    "conversion_warnings": res.get("warnings") or [],
                    "placement": a.get("placement"), "kit": a.get("module") if (a.get("module") or {}).get("family") else None,
                }
                if engine == "godot":
                    tscn = models / f"{Path(fname).stem}.tscn"
                    tscn.write_text(godot_wrapper(a["id"], f"{godot_res_root.rstrip('/')}/models/{fname}"), encoding="utf-8")
                    entry["godot_scene"] = f"models/{tscn.name}"
                entries.append(entry)
            manifest = {
                "schema": "synty-mcp/collection.v1",
                "name": cname,
                "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "engine": engine,
                "preset": preset,
                "units": "meters",
                "up_axis": "+y",
                "forward_axis": "+z (glTF convention)",
                "loader_hint": ("GLTFLoader + DRACOLoader" if PRESETS[preset].get("draco") else "GLTFLoader"),
                "assets": entries,
                "missing": failed,
                "licence": LICENCE_NOTE,
            }
            (base / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            out = {"collection": cname, "path": str(base), "manifest": str(base / "manifest.json"),
                   "manifest_url": self.file_url(base / "manifest.json"), "assets": len(entries),
                   "missing": failed}
            if zip_output:
                zpath = self.settings.collections_dir / f"{cname}.zip"
                with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in sorted(base.rglob("*")):
                        if f.is_file():
                            zf.write(f, f.relative_to(base).as_posix())
                out["zip"] = str(zpath)
                out["zip_url"] = self.file_url(zpath)
            if export_dir:
                export_dir.mkdir(parents=True, exist_ok=True)
                for f in base.rglob("*"):
                    if f.is_file():
                        copy_file(f, export_dir / f.relative_to(base))
                out["exported_to"] = str(export_dir)
            out["manifest_preview"] = {k: manifest[k] for k in ("engine", "preset", "units", "up_axis", "loader_hint")}
            return out

        job = self.jobs.submit("collection", {"name": cname, "refs": [f"{p}/{a['id']}" for _s, p, a in items],
                                              "preset": preset}, work, dedupe_key=f"collection:{cname}")
        return self._wait(job, wait, timeout)

    def prepare_asset_for_godot(self, ref: str, export_to: str | None = None, godot_res_root: str = "res://synty",
                                wait: bool = True, timeout: float = 120) -> dict:
        pid, a = self._one_asset(ref)
        res = self.prepare_asset_collection([f"{pid}/{a['id']}"], name=f"godot_{pid}_{a['id']}", preset="godot4",
                                            engine="godot", export_to=export_to, zip_output=False,
                                            godot_res_root=godot_res_root, wait=wait,
                                            timeout=timeout)
        if res.get("status") == "done":
            res["godot_notes"] = [
                "Copy models/*.glb (+ .tscn) under res:// ; Godot 4 imports GLB natively (no plugin).",
                "Units are metres and +Y up, as Godot expects; scale is already baked to 1.",
                "Unity shaders/particles are not converted; materials are StandardMaterial3D from glTF PBR.",
            ]
        return res

    # ------------------------------------------------------------- syntydex
    def search_syntydex(self, query: str, limit: int = 20) -> dict:
        return self.dex.search(query, limit=limit)

    def match_syntydex_asset_to_local(self, name: str, pack: str | None = None) -> dict:
        return self.dex.match_to_local(name, pack)

    # ----------------------------------------------------------------- jobs
    def get_job(self, job_id: str, with_log: bool = False) -> dict:
        j = self.jobs.get(job_id, with_log=with_log)
        if j is None:
            raise ServiceError(f"unknown job {job_id}")
        return j

    def list_jobs(self, status: str | None = None, limit: int = 30) -> dict:
        return {"jobs": self.jobs.list(status, limit)}


def _version_key(variant: str) -> tuple:
    """'Unity_2022_3 | v1_0_1' -> ((1, 0, 1), (2022, 3)) for "newest first" selection."""
    left, _, right = variant.partition("|")
    nums = lambda t: tuple(int(x) for x in re.findall(r"\d+", t))  # noqa: E731
    return (nums(right), nums(left))


def godot_wrapper(node_name: str, glb_res_path: str) -> str:
    safe = safe_name(node_name)
    return (
        "[gd_scene load_steps=2 format=3]\n\n"
        f'[ext_resource type="PackedScene" path="{glb_res_path}" id="1_glb"]\n\n'
        f'[node name="{safe}" instance=ExtResource("1_glb")]\n'
    )

