"""Content facts from inspected exports, separate from catalog classifications."""
from __future__ import annotations


def export_content(glb: dict | None, params: dict | None = None, materials: list | None = None) -> dict:
    if glb is None:
        return {"verified": False, "evidence": "not_inspected", "skinned": None,
                "animation_count": None, "animation_clips": None, "animations_exported": None,
                "material_bindings": None}
    clips = glb.get("animations") or []
    return {"verified": True, "evidence": "converted_glb", "skinned": bool(glb.get("skins")),
            "animation_count": len(clips), "animation_clips": clips,
            "animations_exported": bool(params.get("animations", True)) if params is not None else None,
            "material_bindings": materials}
