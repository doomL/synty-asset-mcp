"""Blender conversions on synthetic models (skipped without Blender)."""

from __future__ import annotations

import hashlib
import json
import threading
import zipfile
from pathlib import Path

import pytest

from synty_mcp.convert import Converter, plausible_max_m, resolve_preset
from synty_mcp.glbinfo import glb_info

pytestmark = pytest.mark.blender


def near(a, b, tol=0.02):
    return all(abs(x - y) <= tol * max(1.0, abs(y)) for x, y in zip(a, b))


def digest(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_fbx_to_glb_threejs(indexed_pack_real):
    svc = indexed_pack_real
    src = Path(svc.db.one("SELECT mesh_path FROM asset_files WHERE asset_id='SM_Prop_Well_01'")["mesh_path"])
    before = digest(src)
    res = svc.convert_asset_to_glb("SM_Prop_Well_01", preset="threejs_web", wait=True, timeout=300)
    assert res["status"] == "done", res
    r = res["result"]
    g = glb_info(Path(r["path"]))
    assert g["triangles"] == 12
    assert near(g["bounds"]["size"], [1.6, 1.2, 1.6])  # metres, +Y up
    assert abs(g["bounds"]["min"][1]) < 1e-3  # sits on the ground
    assert len(g["images"]) == 1  # texture from the Unity .mat embedded
    assert r["textures"][0]["how"] in ("material_name", "slot_order")
    assert digest(src) == before  # original untouched
    again = svc.convert_asset_to_glb("SM_Prop_Well_01", preset="threejs_web", wait=True)
    assert again["result"]["status"] == "cached"


def test_bundle_node_extraction(indexed_pack_real):
    res = indexed_pack_real.convert_asset_to_glb("SM_Bld_House_Medieval_02", wait=True, timeout=300)
    g = res["result"]["glb"]
    assert res["result"]["blender_stats"]["meshes"] == ["SM_Bld_House_Medieval_02"]
    assert near(g["bounds"]["size"], [8, 6, 9])


def test_animation_and_skin_preserved(indexed_pack_real):
    res = indexed_pack_real.convert_asset_to_glb("SK_Chr_Goblin_01", wait=True, timeout=300)
    assert res["status"] == "done", res
    g = res["result"]["glb"]
    assert g["animations"] and g["skins"] == 1


def test_presets_mobile_and_godot(indexed_pack_real):
    svc = indexed_pack_real
    m = svc.convert_asset_to_glb("SM_Prop_Chair_01", preset="threejs_mobile", wait=True, timeout=300)
    assert "KHR_draco_mesh_compression" in m["result"]["glb"]["extensions_required"]
    gd = svc.convert_asset_to_glb("SM_Prop_Chair_01", preset="godot4", wait=True, timeout=300)
    assert gd["result"]["glb"]["extensions_required"] == []
    with pytest.raises(ValueError, match="Godot"):
        resolve_preset("godot4", {"draco": True})
    with pytest.raises(ValueError, match="unknown"):
        resolve_preset("threejs_web", {"shader": "x"})
    opt = svc.optimize_asset("SM_Prop_Chair_01", preset="threejs_web", max_texture=2, wait=True)
    assert opt["status"] == "done"
    assert opt["result"]["path"] != gd["result"]["path"]


def test_obj_gltf_and_unit_fix(settings, svc, blender_models, tmp_path):
    src = tmp_path / "models"
    src.mkdir()
    for name, data in blender_models.items():
        (src / name).write_bytes(data)
    conv = Converter(settings, svc.db)
    for f in ("SM_Prop_Crate_01.obj", "SM_Prop_Crate_02.gltf"):
        r = conv.convert(pack_id="T", asset_id=Path(f).stem, src=src / f, node=None, textures={}, log=lambda *a: None)
        assert near(r["glb"]["bounds"]["size"], [1, 1, 1]), f
    r = conv.convert(pack_id="T", asset_id="SM_Prop_Stool_01", src=src / "SM_Prop_Stool_01.fbx", node=None,
                     textures={}, max_plausible_m=plausible_max_m("prop"), log=lambda *a: None)
    size = r["glb"]["bounds"]["size"]
    assert max(size) < 2, size
    assert any("auto unit fix" in w for w in r["warnings"])
    # A building of the same size is left alone.
    r = conv.convert(pack_id="T", asset_id="SM_Bld_Big", src=src / "SM_Prop_Stool_01.fbx", node=None,
                     textures={}, max_plausible_m=plausible_max_m("building/shell"), log=lambda *a: None)
    assert max(r["glb"]["bounds"]["size"]) > 40


def test_concurrent_identical_conversions_run_once(indexed_pack_real):
    svc = indexed_pack_real
    out = []

    def go():
        out.append(svc.convert_asset_to_glb("SM_Prop_Chair_02", preset="original_quality", wait=True, timeout=300))

    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(o["status"] == "done" for o in out)
    assert len({o["result"]["path"] for o in out}) == 1
    n = svc.db.one("SELECT COUNT(*) AS n FROM conversions WHERE asset_id='SM_Prop_Chair_02' AND preset='original_quality'")
    assert n["n"] == 1


def test_collection_manifest_zip_and_godot_export(indexed_pack_real, tmp_path):
    svc = indexed_pack_real
    res = svc.prepare_asset_collection(
        [{"ref": "SM_Bld_House_Medieval_01", "qty": 3}, "SM_Prop_Well_01", "SM_Prop_Well_01", "SM_Nope_01"],
        name="village", wait=True, timeout=600)
    assert res["status"] == "done", res
    r = res["result"]
    man = json.loads(Path(r["manifest"]).read_text())
    assert man["units"] == "meters" and man["up_axis"] == "+y" and man["licence"]
    by = {a["id"]: a for a in man["assets"]}
    assert by["SM_Bld_House_Medieval_01"]["count"] == 3
    assert by["SM_Prop_Well_01"]["count"] == 2
    assert near(by["SM_Prop_Well_01"]["bounds_m"]["size"], [1.6, 1.2, 1.6])
    assert man["missing"] and man["missing"][0]["ref"] == "SM_Nope_01"
    with zipfile.ZipFile(r["zip"]) as zf:
        assert "manifest.json" in zf.namelist()
        assert sum(n.endswith(".glb") for n in zf.namelist()) == 2
    # Only the requested assets were converted (no whole-pack conversion).
    converted = {c["asset_id"] for c in svc.db.q("SELECT asset_id FROM conversions WHERE status='ok'")}
    assert converted == {"SM_Bld_House_Medieval_01", "SM_Prop_Well_01"}
    # Godot: GLB + .tscn exported into an allowed project folder.
    game = tmp_path / "game" / "synty"
    gd = svc.prepare_asset_for_godot("SM_Prop_Well_01", export_to=str(game), wait=True, timeout=300)
    assert gd["status"] == "done", gd
    tscn = next(game.glob("models/*.tscn"))
    assert 'path="res://synty/models/' in tscn.read_text()
    from synty_mcp.service import ServiceError

    with pytest.raises(ServiceError, match="export_roots"):
        svc.prepare_asset_collection(["SM_Prop_Well_01"], export_to=str(tmp_path / "elsewhere"))


def test_preview_render(indexed_pack_real):
    svc = indexed_pack_real
    svc.db.x("UPDATE asset_files SET preview_path=NULL WHERE asset_id='SM_Prop_Chair_01'")
    p = svc.get_asset_preview("SM_Prop_Chair_01", generate=True)
    assert p["available"] and p["source"] == "rendered"
    data = Path(p["path"]).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 1000
