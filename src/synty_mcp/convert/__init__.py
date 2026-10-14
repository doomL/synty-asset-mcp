"""Model conversion (Blender headless) with a shared, idempotent cache."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..db import StateDB, dumps, loads, now
from ..diagnostics import export_content
from ..glbinfo import glb_info
from ..safepaths import safe_name

CONVERTER_VERSION = 6
JOB_SCRIPT = Path(__file__).with_name("blender_job.py")

PRESETS: dict[str, dict] = {
    "original_quality": {
        "description": "No mesh or texture reduction; animations kept. Archival / offline baking.",
        "max_texture": 0, "image_format": "AUTO", "draco": False, "decimate": 1.0, "animations": True,
    },
    "threejs_web": {
        "description": "Plain GLB (GLTFLoader, no decoder needed), textures capped at 2048 px.",
        "max_texture": 2048, "image_format": "AUTO", "draco": False, "decimate": 1.0, "animations": True,
    },
    "threejs_mobile": {
        "description": "Draco-compressed mesh (needs DRACOLoader), JPEG textures capped at 1024 px.",
        "max_texture": 1024, "image_format": "JPEG", "jpeg_quality": 80, "draco": True, "draco_level": 6,
        "decimate": 1.0, "animations": True,
    },
    "godot4": {
        "description": "GLB for Godot 4's native importer (no Draco: Godot cannot decode it), 2048 px textures.",
        "max_texture": 2048, "image_format": "AUTO", "draco": False, "decimate": 1.0, "animations": True,
    },
}
OPTION_KEYS = {"max_texture", "image_format", "jpeg_quality", "draco", "draco_level", "decimate", "animations",
               "scale", "auto_unit_fix", "bake_transforms"}
CONVERTIBLE = {".fbx", ".obj", ".gltf", ".glb", ".blend", ".dae"}


class ConversionError(RuntimeError):
    pass


def plausible_max_m(asset_type: str | None) -> float:
    """Largest believable dimension for an asset class, used by the cm->m unit check."""
    t = (asset_type or "").lower()
    if t.startswith("character") or t == "weapon":
        return 5.0
    if t.startswith(("building", "environment", "vehicle/spacecraft")) or t in ("skybox", "terrain"):
        return 150.0
    if t.startswith("vehicle"):
        return 40.0
    return 20.0


def resolve_preset(name: str, options: dict | None) -> dict:
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}; choose one of {sorted(PRESETS)}")
    p = {k: v for k, v in PRESETS[name].items() if k != "description"}
    for k, v in (options or {}).items():
        if k not in OPTION_KEYS:
            raise ValueError(f"unknown conversion option {k!r}; allowed: {sorted(OPTION_KEYS)}")
        p[k] = v
    if name == "godot4" and p.get("draco"):
        raise ValueError("Draco is not supported by Godot 4's glTF importer")
    if not 0.01 <= float(p.get("decimate", 1.0)) <= 1.0:
        raise ValueError("decimate must be within 0.01..1.0")
    return p


def fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def cache_key(pack_id: str, asset_id: str, preset: str, params: dict, src: Path, node: str | None,
              textures: dict) -> str:
    h = hashlib.sha256()
    h.update(dumps([CONVERTER_VERSION, pack_id, asset_id, preset, params, str(src), fingerprint(src), node,
                    textures]).encode())
    return h.hexdigest()[:32]


@contextmanager
def file_lock(lock_dir: Path, key: str):
    """Cross-process lock: two agents asking for the same conversion run it once."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    fh = open(lock_dir / f"{key}.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def run_blender(blender: str, job: dict, timeout: int, log=print) -> dict:
    with tempfile.TemporaryDirectory(prefix="synty_mcp_") as td:
        job = dict(job, report=os.path.join(td, "report.json"))
        job_path = os.path.join(td, "job.json")
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh)
        cmd = [blender, "-b", "--factory-startup", "-noaudio", "--python", str(JOB_SCRIPT), "--", job_path]
        env = dict(os.environ, PYTHONNOUSERSITE="1")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(f"Blender timed out after {timeout}s") from exc
        tail = "\n".join(ln for ln in (proc.stdout or "").splitlines() if "[synty-mcp]" in ln or "Error" in ln)[-4000:]
        if tail:
            log(tail)
        if not os.path.isfile(job["report"]):
            raise ConversionError(f"Blender exited {proc.returncode} without a report: {(proc.stderr or proc.stdout)[-1500:]}")
        with open(job["report"], encoding="utf-8") as fh:
            rep = json.load(fh)
    if not rep.get("ok"):
        raise ConversionError(rep.get("error") or "conversion failed")
    return rep


class Converter:
    def __init__(self, settings, db: StateDB):
        self.settings = settings
        self.db = db

    def output_path(self, preset: str, pack_id: str, asset_id: str, params: dict) -> Path:
        base = self.settings.converted_dir / preset / safe_name(pack_id)
        default = resolve_preset(preset, None)
        name = safe_name(asset_id)
        if params != default:
            name += "__" + hashlib.sha1(dumps(params).encode()).hexdigest()[:8]
        return base / f"{name}.glb"

    def cached(self, key: str) -> dict | None:
        r = self.db.one("SELECT * FROM conversions WHERE key=? AND status='ok'", (key,))
        if r and r["output_path"] and Path(r["output_path"]).is_file():
            r["info"] = loads(r["info"], {})
            return r
        return None

    def convert(self, *, pack_id: str, asset_id: str, src: Path, node: str | None, textures: dict,
                preset: str = "threejs_web", options: dict | None = None, force: bool = False,
                max_plausible_m: float | None = None, log=print) -> dict:
        params = resolve_preset(preset, options)
        src = Path(src)
        if not src.is_file():
            raise ConversionError(f"source model missing on disk: {src}")
        if src.suffix.lower() not in CONVERTIBLE:
            raise ConversionError(f"not a convertible model format: {src.suffix}")
        key = cache_key(pack_id, asset_id, preset, params, src, node, [textures, max_plausible_m])
        out = self.output_path(preset, pack_id, asset_id, params)
        with file_lock(self.settings.converted_dir / ".locks", key):
            hit = None if force else self.cached(key)
            if hit:
                return {"status": "cached", "path": hit["output_path"], "preset": preset, **hit["info"]}
            blender = self.settings.blender_executable()
            if not blender:
                raise ConversionError("Blender not found: install it or set SYNTY_MCP_BLENDER")
            tmp_out = out.with_name(out.stem + ".tmp.glb")
            job = {"mode": "convert", "input": str(src), "output": str(tmp_out), "node": node,
                   "textures": textures, "preset": params, "max_plausible_m": max_plausible_m}
            log(f"blender convert {asset_id} [{preset}]")
            try:
                rep = run_blender(blender, job, self.settings.blender_timeout_s, log=log)
            except ConversionError as exc:
                self.db.x(
                    "INSERT OR REPLACE INTO conversions(key, pack_id, asset_id, preset, source_path, source_fingerprint,"
                    " output_path, status, info, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (key, pack_id, asset_id, preset, str(src), fingerprint(src), None, "failed",
                     dumps({"error": str(exc)}), now()),
                )
                tmp_out.unlink(missing_ok=True)
                raise
            out.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_out, out)
            info = glb_info(out) or {}
            bb = rep.get("bounds") or {}
            if info.get("skins") and bb.get("min_zup"):
                # Accessor bounds ignore skinning (a 0.01-scaled rig reads 100x too small):
                # use Blender's evaluated world bounds, converted Z-up -> glTF Y-up.
                mn, mx = bb["min_zup"], bb["max_zup"]
                ymin = [mn[0], mn[2], -mx[1]]
                ymax = [mx[0], mx[2], -mn[1]]
                info["bounds"] = {"min": [round(v, 4) for v in ymin], "max": [round(v, 4) for v in ymax],
                                  "size": [round(ymax[i] - ymin[i], 4) for i in range(3)], "units": "m",
                                  "up": "+y", "source": "blender_evaluated"}
            record = {
                "preset": preset,
                "params": params,
                "source": str(src),
                "node": node,
                "textures": rep.get("textures"),
                "warnings": rep.get("warnings"),
                "blender_stats": rep.get("stats"),
                "glb": info,
                "content": export_content(info, params, rep.get("textures")),
                "provenance": {"asset_ref": f"{pack_id}/{asset_id}", "pack_id": pack_id,
                               "source_file": src.name, "source_format": src.suffix.lower(),
                               "node": node, "source_fingerprint": fingerprint(src)},
            }
            self.db.x(
                "INSERT OR REPLACE INTO conversions(key, pack_id, asset_id, preset, source_path, source_fingerprint,"
                " output_path, status, info, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, pack_id, asset_id, preset, str(src), fingerprint(src), str(out), "ok", dumps(record), now()),
            )
            return {"status": "converted", "path": str(out), **record}

    def render_preview(self, glb: Path, out_png: Path, size: int = 384, log=print) -> Path:
        blender = self.settings.blender_executable()
        if not blender:
            raise ConversionError("Blender not found: cannot render previews")
        run_blender(blender, {"mode": "preview", "input": str(glb), "output": str(out_png), "size": size},
                    self.settings.blender_timeout_s, log=log)
        return out_png


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)
