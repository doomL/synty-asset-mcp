"""MCP tool surface (official MCP Python SDK, ``MCPServer``)."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from .convert import ConversionError
from .safepaths import PathViolation
from .service import Service, ServiceError
from .store import StoreUnavailable

INSTRUCTIONS = """\
Synty asset library of the owner's *purchased* Synty packs.
Workflow for building a scene:
 1. suggest_assets_for_scene("tre case medievali, un pozzo, due alberi") or search_assets("medieval house")
    — Italian and English both work. Only indexed assets are returned; nothing is invented.
 2. get_asset_details(ref) for the picks (ref = "PACK_ID/ASSET_ID").
 3. prepare_asset_collection(refs=[...], preset="threejs_web") converts ONLY those assets (cached, idempotent)
    and returns a manifest.json with files, sizes (metres, +Y up), triangles and dependencies.
Use convert_asset_to_glb / get_asset_file for a single model. Long work runs as jobs: poll get_job(job_id).
If an item is missing, read `owned_unindexed_candidates`: those packs are owned but not yet indexed —
ask the user before downloading. Assets are licensed to the owner: never upload them to other AI services.
"""

AssetClass = Literal["model", "prefab", "character", "animation", "effect", "ui", "texture"]
Preset = Literal["threejs_web", "threejs_mobile", "godot4", "original_quality"]
# All four MCP behaviour hints are set explicitly on every tool, describing the worst case of the
# handler (any argument combination), so clients never fall back to the spec defaults
# (readOnly=false, destructive=true, idempotent=false, openWorld=true).
# Reads only the local catalog / state DB / job table; no files written, no network.
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Writes only additive or derived local data (stored archive copies, index rows, cached GLBs,
# rendered previews); repeating the call with the same arguments yields the same state.
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Local writes that can delete or overwrite existing files: extract_pack(force=true) removes and
# re-creates the extracted tree; collections overwrite collections/<name>/ and copy into the
# owner's project folder (export_to), replacing same-named files there.
LOCAL_OVERWRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)
# Talks to the Synty Store (owner's session) and writes additive local data; deduplicated.
NETWORK = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

EXPECTED = (ServiceError, ConversionError, StoreUnavailable, PathViolation, ValueError, KeyError, FileNotFoundError)


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except EXPECTED as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def build_server(svc: Service) -> MCPServer:
    mcp = MCPServer(name="synty-assets", title="Synty Asset Library", instructions=INSTRUCTIONS, version="0.1.0")

    def tool(annotations: ToolAnnotations):
        def deco(fn):
            return mcp.tool(annotations=annotations)(_guard(fn))

        return deco

    # ------------------------------------------------------------ library
    @tool(RO)
    def list_owned_packs() -> dict:
        """List the owned Synty packs with status (owned_not_downloaded / downloaded / extracted / indexed),
        archives on disk, extracted trees and indexed asset counts."""
        return svc.list_owned_packs()

    @tool(NETWORK)
    def sync_owned_packs(wait: bool = True) -> dict:
        """Import archives dropped in the inbox folder and, if the Synty Store session is configured, list the
        account's downloadable files and match them to owned packs. Reports clearly when the store is unavailable."""
        return svc.sync_owned_packs(wait=wait)

    @tool(NETWORK)
    def download_pack(
        pack: Annotated[str, Field(description="pack slug, Synty pack id or name, e.g. 'polygon-city'")],
        kinds: Annotated[list[str] | None, Field(description="archive kinds to fetch: unity, source, godot, unreal")] = None,
        extract: bool = True,
        wait: bool = False,
        timeout: float = 1800,
    ) -> dict:
        """Download a purchased pack from the Synty Store (needs the owner's saved session), skipping files already
        downloaded, then verify, extract and index it. Runs as a job. Without a store session it explains the
        manual-import fallback instead."""
        return svc.download_pack(pack, kinds=kinds, extract=extract, wait=wait, timeout=timeout)

    @tool(LOCAL_OVERWRITE)
    def extract_pack(pack: str, archive_id: int | None = None, force: bool = False, wait: bool = True,
                     timeout: float = 1800) -> dict:
        """Extract a pack's verified archives (ZIP and .unitypackage, path-traversal safe) into the library and
        index it with synty-inventory. Idempotent: already extracted archives are skipped unless force=true."""
        return svc.extract_pack(pack, archive_id=archive_id, force=force, wait=wait, timeout=timeout)

    @tool(LOCAL_WRITE)
    def import_archive(
        path: Annotated[str, Field(description="archive inside the inbox or a configured source root")],
        pack: str | None = None,
        extract: bool = True,
        wait: bool = True,
    ) -> dict:
        """Import one .zip/.unitypackage (deduplicated by SHA-256, integrity-checked), optionally extract + index."""
        return svc.import_archive(path, pack=pack, extract=extract, wait=wait)

    @tool(LOCAL_WRITE)
    def scan_library(
        pack: str | None = None,
        register_path: Annotated[str | None, Field(
            description="optional already-extracted folder to use in place (must be under source_roots)")] = None,
        wait: bool = True,
        timeout: float = 1800,
    ) -> dict:
        """(Re)index extracted packs with synty-inventory: names, semantics, kits, measured bounds of converted
        GLBs, and the per-asset source model / texture resolution."""
        return svc.scan_library(pack=pack, register_path=register_path, wait=wait, timeout=timeout)

    @tool(RO)
    def get_library_status() -> dict:
        """Totals: packs by status, indexed assets, conversions, active jobs, disk use, Blender and provider status."""
        return svc.get_library_status()

    # ------------------------------------------------------------- search
    @tool(RO)
    def search_assets(
        query: Annotated[str, Field(description="what you need, Italian or English: 'casa medievale', 'wooden chair'")],
        pack: str | None = None,
        asset_class: AssetClass | None = None,
        available_only: Annotated[bool, Field(description="only assets whose source file is on disk")] = False,
        include_nonplaceable: bool = False,
        limit: int = 10,
    ) -> dict:
        """Find individual Synty assets across all indexed owned packs. Each result has ref, name, pack, class
        (model/prefab/character/animation/effect/ui/texture), size in metres when measured, and availability
        (source file, cached GLBs, preview). Empty results come with owned-but-unindexed pack candidates."""
        return svc.search_assets(query, pack=pack, asset_class=asset_class, available_only=available_only,
                                 include_nonplaceable=include_nonplaceable, limit=limit)

    @tool(RO)
    def get_asset_details(ref: Annotated[str, Field(description="PACK_ID/ASSET_ID (or a unique asset id)")],
                          include_footprint: bool = False) -> dict:
        """Full catalog record (semantics, placement, bounds, kit/part data) plus source files, textures,
        cached conversions and measured geometry. content separates verified exported skins/clips
        and material diagnostics from catalog hints; null facts mean not inspected."""
        return svc.get_asset_details(ref, include_footprint=include_footprint)

    @tool(RO)
    def find_similar_assets(ref: str, limit: int = 10) -> dict:
        """Assets similar to `ref` (same type / kit family / name tokens / size) — for variety or substitutes."""
        return svc.find_similar_assets(ref, limit=limit)

    @tool(RO)
    def suggest_assets_for_scene(
        request: Annotated[str, Field(description="e.g. 'tre case medievali, un pozzo, due alberi, cinque sedie e "
                                                  "un personaggio goblin'")],
        available_only: bool = False,
        alternatives: int = 4,
    ) -> dict:
        """Split a multi-item request (quantities in words or digits, IT/EN), find each item, pick `qty` distinct
        assets, list alternatives, report what is missing and which owned packs could contain it, and list
        matching synty-inventory recipes for modular builds."""
        return svc.suggest_assets_for_scene(request, available_only=available_only, alternatives=alternatives)

    @tool(RO)
    def list_pack_assets(pack: str, asset_class: AssetClass | None = None, query: str | None = None,
                         offset: int = 0, limit: int = 100) -> dict:
        """Page through the indexed assets of one pack (optionally filtered by class or id substring)."""
        return svc.list_pack_assets(pack, asset_class=asset_class, query=query, offset=offset, limit=limit)

    @tool(LOCAL_WRITE)
    def get_asset_preview(ref: str, generate: bool = False) -> Any:
        """Local preview for an asset: the pack's own thumbnail when present, else (generate=true) a render made
        locally with Blender. Returns paths/URLs and the 3D viewer link. Image bytes are only included when the
        owner enabled allow_preview_images_to_agent."""
        res = svc.get_asset_preview(ref, generate=generate)
        if res.get("path") and svc.settings.allow_preview_images_to_agent:
            res["image_inline"] = True
            return [res, Image(path=Path(res["path"]))]
        return res

    # --------------------------------------------------------- conversion
    @tool(LOCAL_WRITE)
    def get_asset_file(ref: str, format: Literal["glb", "source"] = "glb", preset: Preset = "threejs_web",
                       wait: bool = True) -> dict:
        """Path (and authenticated URL in HTTP mode) of a ready-to-use file: a GLB for the preset (converted on
        demand, cached) or the original source model (never modified)."""
        return svc.get_asset_file(ref, format=format, preset=preset, wait=wait)

    @tool(LOCAL_WRITE)
    def convert_asset_to_glb(
        ref: str,
        preset: Preset = "threejs_web",
        options: Annotated[dict | None, Field(description="override: max_texture, image_format, draco, decimate, "
                                                          "animations, scale, auto_unit_fix, bake_transforms")] = None,
        force: bool = False,
        wait: bool = True,
        timeout: float = 120,
    ) -> dict:
        """Convert one asset (FBX/OBJ/glTF) to GLB with Blender headless: textures wired from the Unity
        materials / Synty material list, metres, +Y up, animations kept when present. Cached per preset+options;
        concurrent identical requests run once. Unity shaders/particles/components are NOT converted."""
        return svc.convert_asset_to_glb(ref, preset=preset, options=options, force=force, wait=wait, timeout=timeout)

    @tool(LOCAL_OVERWRITE)
    def prepare_asset_for_godot(ref: str, export_to: str | None = None, godot_res_root: str = "res://synty",
                                wait: bool = True) -> dict:
        """GLB for Godot 4 (no Draco) plus a .tscn wrapper scene; optionally copied into a Godot project folder
        that is listed in export_roots."""
        return svc.prepare_asset_for_godot(ref, export_to=export_to, godot_res_root=godot_res_root, wait=wait)

    @tool(LOCAL_WRITE)
    def optimize_asset(ref: str, preset: Preset = "threejs_mobile", max_texture: int | None = None,
                       decimate: Annotated[float | None, Field(ge=0.01, le=1.0)] = None,
                       draco: bool | None = None, wait: bool = True) -> dict:
        """Produce a lighter GLB (texture cap, optional decimation, Draco). Originals are untouched; result cached."""
        return svc.optimize_asset(ref, preset=preset, max_texture=max_texture, decimate=decimate, draco=draco,
                                  wait=wait)

    @tool(RO)
    def get_asset_dependencies(ref: str) -> dict:
        """What an asset needs: source model (+ node inside a bundle), Unity prefab, materials and textures per
        slot, the modular kit it belongs to (pieces by role) and, for characters, owned animation packs."""
        return svc.get_asset_dependencies(ref)

    @tool(LOCAL_OVERWRITE)
    def prepare_asset_collection(
        refs: Annotated[list[str | dict], Field(description="refs or {ref, qty} objects")],
        name: str = "collection",
        preset: Preset = "threejs_web",
        engine: Literal["threejs", "godot"] = "threejs",
        export_to: Annotated[str | None, Field(description="project folder inside export_roots (local use)")] = None,
        zip_output: bool = True,
        godot_res_root: str = "res://synty",
        wait: bool = True,
        timeout: float = 600,
    ) -> dict:
        """Convert only the listed assets (cache reused), gather them into collections/<name>/models and write
        manifest.json (files, bytes, triangles, bounds in metres, materials, animations, placement, missing items).
        Returns paths and, in HTTP mode, authenticated URLs for the manifest and a zip."""
        return svc.prepare_asset_collection(refs, name=name, preset=preset, engine=engine, export_to=export_to,
                                            zip_output=zip_output, godot_res_root=godot_res_root, wait=wait,
                                            timeout=timeout)

    # ----------------------------------------------------------- syntydex
    @tool(RO)
    def search_syntydex(query: str, limit: int = 20) -> dict:
        """Search SyntyDex. SyntyDex has no documented API, so this works only from a user-provided local export
        and otherwise answers available=false with the reason. Never returns simulated results."""
        return svc.search_syntydex(query, limit=limit)

    @tool(RO)
    def match_syntydex_asset_to_local(name: str, pack: str | None = None) -> dict:
        """Map an asset name seen on SyntyDex (or anywhere) to local indexed assets. Works offline."""
        return svc.match_syntydex_asset_to_local(name, pack=pack)

    # --------------------------------------------------------------- jobs
    @tool(RO)
    def get_job(job_id: str, with_log: bool = False) -> dict:
        """Status, progress, result and (optionally) log of a background job."""
        return svc.get_job(job_id, with_log=with_log)

    @tool(RO)
    def list_jobs(status: Literal["queued", "running", "done", "failed"] | None = None, limit: int = 30) -> dict:
        """Recent background jobs (downloads, extraction, scans, conversions, collections)."""
        return svc.list_jobs(status=status, limit=limit)

    return mcp
