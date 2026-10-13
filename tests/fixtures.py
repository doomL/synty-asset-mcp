"""Synthetic fixtures. No Synty content: every file is generated here.

* ``write_png`` — a tiny valid PNG (pure Python).
* ``build_unitypackage`` — a .unitypackage (tar.gz of <guid>/{asset,asset.meta,pathname,preview.png})
  shaped like a Synty POLYGON pack: prefabs -> FBX (bundle + single), materials -> textures.
* ``build_pack_zip`` — a store-style ZIP wrapping that .unitypackage plus Source_Files with a
  MaterialList, like a Synty download.
* ``build_evil_zip`` / ``build_evil_unitypackage`` — path traversal / absolute / symlink members.
* ``BLENDER_FIXTURE`` — Blender script generating real FBX/OBJ/glTF models (used when Blender exists).
"""

from __future__ import annotations

import gzip
import io
import struct
import tarfile
import zipfile
import zlib
from pathlib import Path

PACK = "POLYGON_FakeVillage"


def write_png(path: Path, rgb=(200, 120, 40), size: int = 4) -> Path:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def png_bytes(rgb=(200, 120, 40)) -> bytes:
    buf = io.BytesIO()
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = write_png(Path(td) / "x.png", rgb)
        buf.write(p.read_bytes())
    return buf.getvalue()


def g(n: int) -> str:
    return f"{n:032x}"


def _meta(guid: str, extra: str = "") -> str:
    return f"fileFormatVersion: 2\nguid: {guid}\n{extra}"


def _prefab(name: str, mesh_guid: str, mesh_file_id: str, mat_guids: list[str]) -> str:
    mats = "\n".join(f"  - {{fileID: 2100000, guid: {mg}, type: 2}}" for mg in mat_guids)
    return f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100000
GameObject:
  m_Name: {name}
--- !u!33 &3300000
MeshFilter:
  m_Mesh: {{fileID: {mesh_file_id}, guid: {mesh_guid}, type: 3}}
--- !u!23 &2300000
MeshRenderer:
  m_Materials:
{mats}
  m_StaticBatchInfo:
    firstSubMesh: 0
"""


def _mat(name: str, tex_guid: str) -> str:
    return f"""%YAML 1.1
--- !u!21 &2100000
Material:
  m_Name: {name}
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _BumpMap:
        m_Texture: {{fileID: 0}}
    - _BaseMap:
        m_Texture: {{fileID: 2800000, guid: {tex_guid}, type: 3}}
        m_Scale: {{x: 1, y: 1}}
    m_Floats:
    - _Smoothness: 0
"""


# Asset layout of the fake pack: (prefab name, model file, node name in model or None)
PREFABS = [
    ("SM_Bld_House_Medieval_01", "Models/Buildings.fbx", "SM_Bld_House_Medieval_01"),
    ("SM_Bld_House_Medieval_02", "Models/Buildings.fbx", "SM_Bld_House_Medieval_02"),
    ("SM_Prop_Well_01", "Models/SM_Prop_Well_01.fbx", None),
    ("SM_Env_Tree_Pine_01", "Models/SM_Env_Tree_Pine_01.fbx", None),
    ("SM_Prop_Chair_01", "Models/SM_Prop_Chair_01.fbx", None),
    ("SM_Prop_Chair_02", "Models/SM_Prop_Chair_02.fbx", None),
    ("SK_Chr_Goblin_01", "Models/SK_Chr_Goblin_01.fbx", None),
]


def unity_entries(models: dict[str, bytes] | None = None) -> list[tuple[str, str, bytes, bytes | None]]:
    """(guid, pathname, asset bytes, meta bytes) for every file of the fake Unity pack."""
    base = f"Assets/Synty/{PACK}"
    entries = []
    tex_guid = g(1)
    entries.append((tex_guid, f"{base}/Textures/PolygonFakeVillage_Texture_01_A.png", png_bytes(), None))
    mat_guid = g(2)
    entries.append((mat_guid, f"{base}/Materials/PolygonFakeVillage_Mat_01_A.mat",
                    _mat("PolygonFakeVillage_Mat_01_A", tex_guid).encode(), None))
    model_guids: dict[str, str] = {}
    n = 10
    for _prefab_name, model, _node in PREFABS:
        if model in model_guids:
            continue
        model_guids[model] = g(n)
        n += 1
    for model, mg in model_guids.items():
        nodes = [p for p, m, _ in PREFABS if m == model]
        table = "".join(
            f"  - first:\n      43: {4300000 + i * 2}\n    second: {name}\n" for i, name in enumerate(nodes))
        meta = _meta(mg, f"ModelImporter:\n  internalIDToNameTable:\n{table}")
        body = (models or {}).get(Path(model).name, b"; synthetic FBX placeholder\n")
        entries.append((mg, f"{base}/{model}", body, meta.encode()))
    for i, (name, model, _node) in enumerate(PREFABS):
        idx = [p for p, m, _ in PREFABS if m == model].index(name)
        pg = g(100 + i)
        entries.append((pg, f"{base}/Prefabs/{name}.prefab",
                        _prefab(name, model_guids[model], str(4300000 + idx * 2), [mat_guid]).encode(), None))
    return entries


def build_unitypackage(path: Path, models: dict[str, bytes] | None = None, previews: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        def add(name: str, data: bytes):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        for guid, pathname, asset, meta in unity_entries(models):
            add(f"{guid}/pathname", (pathname + "\n00").encode())
            add(f"{guid}/asset", asset)
            add(f"{guid}/asset.meta", meta or _meta(guid).encode())
            if previews and pathname.endswith(".prefab"):
                add(f"{guid}/preview.png", png_bytes((40, 160, 90)))
    return path


def build_pack_zip(path: Path, models: dict[str, bytes] | None = None) -> Path:
    """Store-style download: ``POLYGON_FakeVillage_Unity_2022_v1_0.zip`` with the package inside,
    plus a Source_Files tree and Synty's MaterialList text."""
    tmp_pkg = path.with_suffix(".tmp.unitypackage")
    build_unitypackage(tmp_pkg, models)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp_pkg, f"{PACK}_Unity_2022/{PACK}_BuiltIn.unitypackage")
        zf.writestr("Source_Files/Textures/PolygonFakeVillage_Texture_01_A.png", png_bytes())
        zf.writestr("Source_Files/MaterialList_PolygonFakeVillage.txt",
                    "Prefab Name: SM_Prop_Well_01\n    Mesh Name: SM_Prop_Well_01\n"
                    "        Slot: PolygonFakeVillage_Mat_01_A (PolygonFakeVillage_Texture_01_A)\n")
    tmp_pkg.unlink()
    return path


def build_evil_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ok/readme.txt", "fine")
        zf.writestr("../../escaped.txt", "nope")
        zf.writestr("/abs/escaped.txt", "nope")
        zf.writestr("C:/windows/escaped.txt", "nope")
        info = zipfile.ZipInfo("ok/link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    return path


def build_evil_unitypackage(path: Path) -> Path:
    with gzip.open(path, "wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        def add(name, data):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        add(f"{g(1)}/pathname", b"../../escaped.txt")
        add(f"{g(1)}/asset", b"nope")
        add(f"{g(2)}/pathname", b"/etc/escaped")
        add(f"{g(2)}/asset", b"nope")
        add(f"{g(3)}/pathname", b"Assets/Good/file.txt")
        add(f"{g(3)}/asset", b"good")
        sym = tarfile.TarInfo(f"{g(4)}/asset")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc/passwd"
        tar.addfile(sym)
        add(f"{g(4)}/pathname", b"Assets/Good/link")
    return path


BLENDER_FIXTURE = r'''
import bpy, sys, os
out = sys.argv[sys.argv.index("--") + 1]
os.makedirs(out, exist_ok=True)

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def mat(name):
    m = bpy.data.materials.new(name); m.use_nodes = True; return m

def box(name, size, loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o = bpy.context.active_object; o.name = name; o.data.name = name
    o.scale = size; bpy.ops.object.transform_apply(scale=True)
    o.location = (loc[0], loc[1], size[2] / 2)
    bpy.ops.object.transform_apply(location=True)
    o.data.materials.append(mat("PolygonFakeVillage_Mat_01_A"))
    return o

def export_fbx(path, **kw):
    bpy.ops.export_scene.fbx(filepath=path, apply_unit_scale=True, bake_space_transform=False, **kw)

# Bundle with two houses (6 x 5 x 7 m and 8 x 6 x 9 m)
reset(); box("SM_Bld_House_Medieval_01", (6, 7, 5), (-10, 0, 0)); box("SM_Bld_House_Medieval_02", (8, 9, 6), (10, 0, 0))
export_fbx(os.path.join(out, "Buildings.fbx"))
for name, size in (("SM_Prop_Well_01", (1.6, 1.6, 1.2)), ("SM_Env_Tree_Pine_01", (2, 2, 8)),
                   ("SM_Prop_Chair_01", (0.5, 0.5, 0.9)), ("SM_Prop_Chair_02", (0.55, 0.55, 1.0))):
    reset(); box(name, size); export_fbx(os.path.join(out, name + ".fbx"))
# Animated character stand-in: armature + skinned box + a 'Walk' action.
reset()
body = box("SK_Chr_Goblin_01", (0.6, 0.4, 1.2))
bpy.ops.object.armature_add(location=(0, 0, 0))
arm = bpy.context.active_object; arm.name = "Root"
body.parent = arm; mod = body.modifiers.new("Armature", "ARMATURE"); mod.object = arm
vg = body.vertex_groups.new(name=arm.data.bones[0].name); vg.add(list(range(len(body.data.vertices))), 1.0, "REPLACE")
arm.animation_data_create(); act = bpy.data.actions.new("Goblin_Walk"); arm.animation_data.action = act
bpy.context.view_layer.objects.active = arm; bpy.ops.object.mode_set(mode="POSE")
pb = arm.pose.bones[0]
for f, z in ((1, 0.0), (10, 0.3), (20, 0.0)):
    pb.location = (0, z, 0); pb.keyframe_insert("location", frame=f)
bpy.ops.object.mode_set(mode="OBJECT")
export_fbx(os.path.join(out, "SK_Chr_Goblin_01.fbx"), bake_anim=True)
# OBJ and glTF variants of a crate
reset(); box("SM_Prop_Crate_01", (1, 1, 1))
bpy.ops.wm.obj_export(filepath=os.path.join(out, "SM_Prop_Crate_01.obj"))
bpy.ops.export_scene.gltf(filepath=os.path.join(out, "SM_Prop_Crate_02.gltf"), export_format="GLTF_SEPARATE")
# Centimetre-authored file without unit scale: a 50 cm stool exported as 50 "metres"
reset(); box("SM_Prop_Stool_01", (40, 40, 50))
bpy.ops.export_scene.fbx(filepath=os.path.join(out, "SM_Prop_Stool_01.fbx"), apply_unit_scale=False, global_scale=1.0)
print("FIXTURES_OK")
'''
