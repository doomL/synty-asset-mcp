"""Read just enough Unity YAML to convert assets outside Unity:

* GUID index of a tree (``*.meta``),
* prefab -> source model (+ the node name inside a bundle FBX),
* material -> albedo texture,
* Synty ``MaterialList_*.txt`` (shipped with Source Files) -> per-mesh textures.

Text-level parsing only; no Unity Editor, no binary formats.
"""

from __future__ import annotations

import re
from pathlib import Path

GUID_RE = re.compile(r"^guid:\s*([0-9a-fA-F]{32})\s*$", re.MULTILINE)
REF_RE = re.compile(r"\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32})")
MESH_REF_RE = re.compile(r"m_Mesh:\s*\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32})")
SOURCE_PREFAB_RE = re.compile(r"m_SourcePrefab:\s*\{fileID:\s*(-?\d+),\s*guid:\s*([0-9a-fA-F]{32})")
MATERIAL_REF_RE = re.compile(r"-\s*\{fileID:\s*2100000,\s*guid:\s*([0-9a-fA-F]{32})")
MODEL_EXTS = {".fbx", ".obj", ".gltf", ".glb", ".blend", ".dae"}
TEXTURE_EXTS = {".png", ".tga", ".jpg", ".jpeg", ".psd", ".tif", ".tiff", ".exr"}
ALBEDO_KEYS = ("_basemap", "_maintex", "_albedo", "_base_texture", "_basecolormap", "_diffuse", "_texture", "_color")


def read_guid(meta: Path) -> str | None:
    try:
        m = GUID_RE.search(meta.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return m.group(1).lower() if m else None


def index_guids(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for meta in root.rglob("*.meta"):
        g = read_guid(meta)
        if g:
            out[g] = meta.with_name(meta.name[:-5])
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def model_mesh_names(model: Path) -> dict[str, str]:
    """fileID -> object name for meshes inside a model, from its .meta
    (``internalIDToNameTable`` on Unity 2019+, ``fileIDToRecycleName`` before)."""
    text = _read(Path(str(model) + ".meta"))
    names: dict[str, str] = {}
    # Legacy: fileIDToRecycleName:\n    4300000: SM_Name
    legacy = re.search(r"fileIDToRecycleName:\n((?:\s+-?\d+:.*\n)+)", text)
    if legacy:
        for line in legacy.group(1).splitlines():
            k, _, v = line.strip().partition(":")
            names[k.strip()] = v.strip()
    # Modern: - first:\n      43: 4300000\n    second: SM_Name
    for m in re.finditer(r"-\s*first:\s*\n\s*(-?\d+):\s*(-?\d+)\s*\n\s*second:\s*(.+)", text):
        names[m.group(2)] = m.group(3).strip()
    return names


def prefab_model(prefab: Path, guids: dict[str, Path], depth: int = 0) -> tuple[Path | None, str | None]:
    """(model file, node name inside it) that a prefab renders, or (None, None)."""
    text = _read(prefab)
    if not text:
        return None, None
    for rx in (MESH_REF_RE, SOURCE_PREFAB_RE):
        for m in rx.finditer(text):
            file_id, guid = m.group(1), m.group(2).lower()
            target = guids.get(guid)
            if target is None:
                continue
            if target.suffix.lower() in MODEL_EXTS:
                node = model_mesh_names(target).get(file_id)
                return target, node
            if target.suffix.lower() == ".prefab" and depth < 4:
                found = prefab_model(target, guids, depth + 1)
                if found[0]:
                    return found
    return None, None


def prefab_material_paths(prefab: Path, guids: dict[str, Path]) -> list[Path]:
    out: list[Path] = []
    for g in MATERIAL_REF_RE.findall(_read(prefab)):
        p = guids.get(g.lower())
        if p is not None and p.suffix.lower() == ".mat" and p not in out:
            out.append(p)
    return out


def material_albedo(mat: Path, guids: dict[str, Path]) -> Path | None:
    """The base-colour texture a Unity .mat samples (URP/Built-in/Synty shaders)."""
    text = _read(mat)
    m = re.search(r"m_TexEnvs:\n(.*?)(?:\n\s{4}m_Ints:|\n\s{4}m_Floats:|\Z)", text, re.S)
    block = m.group(1) if m else text
    entries: list[tuple[str, str]] = []
    current = None
    for line in block.splitlines():
        km = re.match(r"\s*-\s*([A-Za-z0-9_]+):\s*$", line)
        if km:
            current = km.group(1)
            continue
        tm = re.search(r"m_Texture:\s*\{fileID:\s*-?\d+,\s*guid:\s*([0-9a-fA-F]{32})", line)
        if tm and current:
            entries.append((current.lower(), tm.group(1).lower()))
    # Prefer albedo-like slots, then anything that resolves to an image.
    for key in ALBEDO_KEYS:
        for name, g in entries:
            if key in name and "normal" not in name and "emiss" not in name:
                p = guids.get(g)
                if p is not None and p.suffix.lower() in TEXTURE_EXTS:
                    return p
    for _name, g in entries:
        p = guids.get(g)
        if p is not None and p.suffix.lower() in TEXTURE_EXTS:
            return p
    return None


# --- Source Files -------------------------------------------------------------

_ML_PREFAB = re.compile(r"^\s*Prefab Name:\s*(.+?)\s*$")
_ML_MESH = re.compile(r"^\s*Mesh Name:\s*(.+?)\s*$")
_ML_SLOT = re.compile(r"^\s*Slot:\s*(.+?)(?:\s*\((.+?)\))?\s*$")
_ML_ALBEDO = re.compile(r"^\s*Albedo:\s*(.+?)\s*\([^)]*\)\s*$")


def parse_material_list_meshes(path: Path) -> dict[str, dict[str, list[dict]]]:
    """Read compact or expanded material records, preserving per-mesh slots."""
    prefabs: dict[str, dict[str, list[dict]]] = {}
    prefab = None
    mesh = None
    for line in _read(path).splitlines():
        pm = _ML_PREFAB.match(line)
        if pm:
            prefab, mesh = pm.group(1), None
            prefabs.setdefault(prefab, {})
            continue
        mm = _ML_MESH.match(line)
        if mm and prefab:
            mesh = mm.group(1)
            prefabs[prefab].setdefault(mesh, [])
            continue
        sm = _ML_SLOT.match(line)
        if sm and prefab and mesh:
            prefabs[prefab][mesh].append({"material": sm.group(1), "texture": sm.group(2) or ""})
            continue
        am = _ML_ALBEDO.match(line)
        if am and prefab and mesh and prefabs[prefab][mesh]:
            prefabs[prefab][mesh][-1]["texture"] = Path(am.group(1)).stem
        if line.strip().lower() == "no textures assigned" and prefab and mesh and prefabs[prefab][mesh]:
            prefabs[prefab][mesh][-1]["untextured"] = True
    return prefabs


def parse_material_list(path: Path) -> dict[str, list[dict]]:
    """Asset/mesh lookup for MaterialList; explicit flat slots retain their intent."""
    prefabs = parse_material_list_meshes(path)
    out: dict[str, list[dict]] = {}
    for meshes in prefabs.values():
        for mname, slots in meshes.items():
            out.setdefault(mname, slots)
    for pname, meshes in prefabs.items():
        if pname in out or not meshes:
            continue
        key = norm_name(pname)
        best = next((m for m in meshes if norm_name(m) == key), None)
        if best is None and len(meshes) == 1:
            best = next(iter(meshes))
        if best is not None:
            out[pname] = meshes[best]
    return out


_PREFIX = re.compile(r"^(sm|sk|chr|pf|fx)_", re.I)
_VARIANT = re.compile(r"_\d{1,3}$")


def norm_name(name: str) -> str:
    """Compare Synty names loosely: drop SM_/SK_ prefix and a trailing _01."""
    n = _PREFIX.sub("", name.strip())
    return _VARIANT.sub("", n).lower()


def lookup_material_list(ml: dict[str, list[dict]], *names: str) -> list[dict] | None:
    for n in names:
        if n and n in ml:
            return ml[n]
    wanted = {norm_name(n) for n in names if n}
    for k, v in ml.items():
        if norm_name(k) in wanted:
            return v
    return None


def resolve_material_slots(slots: list[dict], textures: dict[str, Path]) -> dict:
    """Resolve references without treating missing and explicitly flat as equivalent."""
    paths, states, expected, by_material, material_states = [], [], [], {}, {}
    for slot in slots:
        name = slot.get("texture") or ""
        path = textures.get(name.lower())
        state = "untextured" if slot.get("untextured") else "resolved" if path else "missing" if name else "unknown"
        paths.append(str(path) if path else None)
        states.append(state)
        expected.append(name or None)
        material_states[slot["material"]] = state
        if path and state != "untextured":
            by_material[slot["material"]] = str(path)
    return {"slots": paths, "slot_states": states, "expected_textures": expected,
            "by_material": by_material, "material_states": material_states}


def texture_index(root: Path) -> dict[str, Path]:
    """lowercase stem -> texture path for every image under ``root``."""
    out: dict[str, Path] = {}
    for p in root.rglob("*"):
        if p.suffix.lower() in TEXTURE_EXTS and p.is_file() and ".mayaSwatches" not in p.parts:
            out.setdefault(p.stem.lower(), p)
    return out


def default_atlas(textures: dict[str, Path]) -> Path | None:
    """The pack's main colour atlas (``Polygon<Pack>_Texture_01[_A].png``) if one exists."""
    ranked = sorted(
        (k for k in textures if re.search(r"texture_0?1(_a)?$", k) and "normal" not in k and "emiss" not in k),
        key=lambda k: (not k.endswith("_a"), len(k)),
    )
    return textures[ranked[0]] if ranked else None
