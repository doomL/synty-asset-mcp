"""GLB facts from the JSON chunk only (no vertex data is loaded):
triangles, vertices, materials, textures, animations, skins, bounds."""

from __future__ import annotations

from pathlib import Path

from synty_inventory.sources.glb_measure import measure_glb, read_glb_json

_MODE_TRIS = {4: lambda n: n // 3, 5: lambda n: max(0, n - 2), 6: lambda n: max(0, n - 2)}


def glb_info(path: Path) -> dict | None:
    doc = read_glb_json(Path(path))
    if doc is None:
        return None
    acc = doc.get("accessors") or []
    tris = verts = 0
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            mode = prim.get("mode", 4)
            pos = (prim.get("attributes") or {}).get("POSITION")
            if pos is not None and pos < len(acc):
                verts += int(acc[pos].get("count") or 0)
            idx = prim.get("indices")
            n = int(acc[idx].get("count") or 0) if idx is not None and idx < len(acc) else (
                int(acc[pos].get("count") or 0) if pos is not None and pos < len(acc) else 0)
            tris += _MODE_TRIS.get(mode, lambda _n: 0)(n)
    bounds = None
    try:
        mm = measure_glb(Path(path))
    except Exception:  # noqa: BLE001
        mm = None
    if mm:
        mn, mx = mm
        bounds = {"min": [round(v, 4) for v in mn], "max": [round(v, 4) for v in mx],
                  "size": [round(mx[i] - mn[i], 4) for i in range(3)], "units": "m", "up": "+y"}
    return {
        "bytes": Path(path).stat().st_size,
        "triangles": tris,
        "vertices": verts,
        "meshes": len(doc.get("meshes") or []),
        "nodes": len(doc.get("nodes") or []),
        "materials": [m.get("name") for m in doc.get("materials") or []],
        "textures": len(doc.get("textures") or []),
        "images": [i.get("name") or i.get("uri") for i in doc.get("images") or []],
        "animations": [a.get("name") for a in doc.get("animations") or []],
        "skins": len(doc.get("skins") or []),
        "extensions_required": doc.get("extensionsRequired") or [],
        "bounds": bounds,
    }
