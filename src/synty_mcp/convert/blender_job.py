"""Runs *inside* Blender: ``blender -b --factory-startup --python blender_job.py -- job.json``.

Modes (``job["mode"]``):

* ``convert`` — import FBX/OBJ/glTF/GLB/.blend, optionally keep one node of a
  bundle file, wire textures, optimise, export GLB, write a JSON report.
* ``preview`` — import a GLB and render a small PNG thumbnail (Cycles CPU).
* ``measure`` — import each of ``job["inputs"]`` and report its largest dimension.

The source file is only ever *read*. Images are loaded into memory and
embedded in the GLB; nothing is saved back over the original textures.
"""

import json
import math
import os
import re
import sys
import traceback

import bpy
import mathutils

REPORT_MARK = "@@SYNTY_MCP_REPORT@@"


def log(msg):
    print(f"[synty-mcp] {msg}", flush=True)


def norm(name):
    n = re.sub(r"\.\d{3}$", "", name or "")
    n = re.sub(r"^(sm|sk|chr|pf|fx)_", "", n, flags=re.I)
    n = re.sub(r"_\d{1,3}$", "", n)
    return n.lower()


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_any(path, anim):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path, use_anim=anim, ignore_leaf_bones=True,
                                 automatic_bone_orientation=False)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=path)
    elif ext == ".blend":
        with bpy.data.libraries.load(path, link=False) as (src, dst):
            dst.objects = list(src.objects)
        for ob in dst.objects:
            if ob is not None:
                bpy.context.scene.collection.objects.link(ob)
    else:
        raise RuntimeError(f"unsupported model format: {ext}")


HELPER_MESH_RE = re.compile(r"(_shadow|_shadowcaster|_collider|_collision)(_?\d+)?$|^ucx_", re.I)


def drop_helper_meshes():
    """Delete shadow-caster / collision proxies (``SM_Bld_Barracks_01_Shadow``) when a
    visible mesh remains: engines cast shadows from the real mesh, and Blender's FBX
    importer can blow these proxies up ~100x, wrecking bounds and the unit check."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    helpers = [o for o in meshes if HELPER_MESH_RE.search(o.name)]
    if not helpers or len(helpers) == len(meshes):
        return []
    names = [o.name for o in helpers]
    for o in helpers:
        bpy.data.objects.remove(o, do_unlink=True)
    return names


def keep_node(node):
    """Keep the object(s) for ``node`` (+ their armature/parents/children); delete other meshes."""
    objs = list(bpy.context.scene.objects)
    target = [o for o in objs if o.name == node or (o.type == "MESH" and o.data and o.data.name == node)]
    if not target:
        key = norm(node)
        target = [o for o in objs if norm(o.name) == key or (o.type == "MESH" and o.data and norm(o.data.name) == key)]
    if not target:
        meshes = [o for o in objs if o.type == "MESH"]
        if len(meshes) == 1:
            return [meshes[0].name], 0  # single-mesh file whose object is named differently
        names = sorted(o.name for o in meshes)
        raise RuntimeError(f"node {node!r} not found in model; meshes: {names[:40]}")
    keep = set()
    for t in target:
        keep.add(t)
        keep.update(t.children_recursive)
        p = t.parent
        while p is not None:
            keep.add(p)
            p = p.parent
        for m in t.modifiers:
            if m.type == "ARMATURE" and m.object is not None:
                keep.add(m.object)
                q = m.object.parent
                while q is not None:
                    keep.add(q)
                    q = q.parent
    # Children of a kept armature that are other meshes are not wanted.
    removed = 0
    for o in objs:
        if o in keep:
            continue
        if o.type in ("MESH", "EMPTY", "CURVE", "LIGHT", "CAMERA"):
            bpy.data.objects.remove(o, do_unlink=True)
            removed += 1
    return [t.name for t in target], removed


def _find_by_basename(name, dirs):
    base = os.path.basename(name.replace("\\", "/"))
    stem = os.path.splitext(base)[0].lower()
    for d in dirs:
        try:
            for f in os.listdir(d):
                if f.lower() == base.lower() or os.path.splitext(f)[0].lower() == stem:
                    return os.path.join(d, f)
        except OSError:
            continue
    return None


def _image(path, cache):
    if path in cache:
        return cache[path]
    img = bpy.data.images.load(path, check_existing=True)
    cache[path] = img
    return img


def _base_color_image(mat):
    if not mat.use_nodes:
        return None
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image is not None:
            return n
    return None


def _wire(mat, img):
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None) or nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    for link in list(bsdf.inputs["Base Color"].links):
        nt.links.remove(link)
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    # Synty materials are matte; FBX imports often come in glossy.
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = max(bsdf.inputs["Roughness"].default_value, 0.8)
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0


def assign_textures(tex):
    dirs = tex.get("search_dirs") or []
    cache, done, report = {}, {}, []
    by_mesh = tex.get("by_mesh") or {}
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        bindings = by_mesh.get(ob.name) or by_mesh.get(ob.data.name)
        if bindings is None:
            bindings = next((v for k, v in by_mesh.items() if norm(k) in {norm(ob.name), norm(ob.data.name)}), tex)
        slots = bindings.get("slots") or []
        states = bindings.get("slot_states") or []
        expected = bindings.get("expected_textures") or []
        by_mat = bindings.get("by_material") or {}
        mat_states = bindings.get("material_states") or {}
        fallback = tex.get("fallback")
        for i, slot in enumerate(ob.material_slots):
            mat = slot.material
            if mat is None:
                mat = bpy.data.materials.new(f"{ob.name}_mat_{i}")
                slot.material = mat
            chosen, how, state = None, None, None
            key = next((k for k in by_mat if k == mat.name), None)
            if key is None:
                key = next((k for k in by_mat if norm(k) == norm(mat.name)), None)
            state_key = next((k for k in mat_states if k == mat.name), None)
            if state_key is None:
                state_key = next((k for k in mat_states if norm(k) == norm(mat.name)), None)
            if key is not None:
                chosen, how = by_mat[key], "material_name"
            elif state_key is not None and mat_states[state_key] in ("untextured", "missing"):
                state = mat_states[state_key]
            elif i < len(states) and states[i] in ("untextured", "missing"):
                state = states[i]
            elif i < len(slots) and slots[i]:
                chosen, how = slots[i], "slot_order"
            if state is None and chosen is None:
                node = _base_color_image(mat)
                if node is not None and node.image is not None:
                    fp = bpy.path.abspath(node.image.filepath)
                    if os.path.isfile(fp):
                        chosen, how = fp, "embedded_path"
                    else:
                        found = _find_by_basename(node.image.filepath or node.image.name, dirs)
                        if found:
                            chosen, how = found, "relinked_by_name"
                if chosen is None and len(slots) == 1 and slots[0]:
                    chosen, how = slots[0], "single_slot"
                if chosen is None and fallback:
                    chosen, how = fallback, "pack_atlas_fallback"
            state = "resolved" if chosen and os.path.isfile(chosen) else state or "unknown"
            binding = (chosen, state)
            if mat.name in done and done[mat.name] != binding:
                # A shared source material can have different per-mesh bindings.
                # Copy it inside the export rather than rewiring an earlier mesh.
                mat = mat.copy()
                slot.material = mat
            if mat.name not in done:
                if state == "resolved":
                    _wire(mat, _image(chosen, cache))
                elif state in ("untextured", "missing") and mat.use_nodes:
                    for node in mat.node_tree.nodes:
                        if node.type == "BSDF_PRINCIPLED":
                            for link in list(node.inputs["Base Color"].links):
                                mat.node_tree.links.remove(link)
                done[mat.name] = binding
            report.append({"mesh": ob.name, "slot": i, "material": mat.name,
                           "texture": os.path.basename(chosen) if state == "resolved" else None,
                           "state": state, "how": how or state,
                           "expected_texture": expected[i] if i < len(expected) else None})
    return report


def limit_textures(max_size):
    resized = []
    if not max_size:
        return resized
    for img in bpy.data.images:
        if img.size[0] > max_size or img.size[1] > max_size:
            w, h = img.size
            f = max_size / float(max(w, h))
            img.scale(max(1, int(w * f)), max(1, int(h * f)))
            resized.append({"image": img.name, "from": [w, h], "to": list(img.size)})
    return resized


def decimate(ratio):
    if not ratio or ratio >= 0.999:
        return 0
    n = 0
    for ob in bpy.context.scene.objects:
        if ob.type == "MESH":
            m = ob.modifiers.new("synty_mcp_decimate", "DECIMATE")
            m.ratio = ratio
            m.use_collapse_triangulate = True
            n += 1
    return n


def world_bbox():
    mn = [math.inf] * 3
    mx = [-math.inf] * 3
    dg = bpy.context.evaluated_depsgraph_get()
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        ev = ob.evaluated_get(dg)
        for c in ev.bound_box:
            w = ob.matrix_world @ mathutils.Vector(c)
            for i in range(3):
                mn[i] = min(mn[i], w[i])
                mx[i] = max(mx[i], w[i])
    if mn[0] == math.inf:
        return None
    # Blender is Z-up; report in glTF/three.js Y-up axes: (x, z, -y)
    size_z_up = [mx[i] - mn[i] for i in range(3)]
    return {"size_yup": [size_z_up[0], size_z_up[2], size_z_up[1]],
            "min_zup": mn, "max_zup": mx}


def apply_scale(factor):
    if not factor or abs(factor - 1.0) < 1e-9:
        return
    roots = [o for o in bpy.context.scene.objects if o.parent is None]
    for o in roots:
        o.scale = [s * factor for s in o.scale]
        o.location = [v * factor for v in o.location]
    bpy.context.view_layer.update()


def normalize_transforms():
    """Bake object rotation/scale (Synty FBX rigs carry a 0.01 scale + 90° X
    rotation on the armature) so every exported node has unit scale and the
    GLB's own bounds are in metres. Skipped when actions exist: baking scale
    into an animated rig would invalidate its location keys."""
    if bpy.data.actions:
        return False
    if any(o.type == "ARMATURE" for o in bpy.context.scene.objects):
        # Rigs stay as authored (Synty: 0.01 armature scale) so separately converted
        # animation clips keep matching them bone for bone.
        return False
    objs = [o for o in bpy.context.scene.objects if o.type in ("MESH", "ARMATURE", "EMPTY")]
    if not objs:
        return False
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True, properties=False)
    return True


def stats():
    dg = bpy.context.evaluated_depsgraph_get()
    tris = verts = 0
    meshes = []
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH":
            continue
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        tris += len(me.loop_triangles)
        verts += len(me.vertices)
        meshes.append(ob.name)
        ev.to_mesh_clear()
    return {
        "meshes": meshes,
        "triangles": tris,
        "vertices": verts,
        "materials": sorted({s.material.name for o in bpy.context.scene.objects if o.type == "MESH"
                             for s in o.material_slots if s.material}),
        "armatures": [o.name for o in bpy.context.scene.objects if o.type == "ARMATURE"],
        "actions": [a.name for a in bpy.data.actions],
    }


def export_glb(path, p):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    kw = dict(
        filepath=path,
        export_format="GLB",
        export_yup=True,
        export_apply=True,
        export_texcoords=True,
        export_normals=True,
        export_materials="EXPORT",
        export_animations=bool(p.get("animations", True)),
        export_skins=True,
        export_image_format=p.get("image_format", "AUTO"),
        export_draco_mesh_compression_enable=bool(p.get("draco", False)),
    )
    if p.get("draco"):
        kw["export_draco_mesh_compression_level"] = int(p.get("draco_level", 6))
    if p.get("image_format") == "JPEG":
        kw["export_image_quality"] = int(p.get("jpeg_quality", 85))
    bpy.ops.export_scene.gltf(**kw)


def run_convert(job):
    p = job.get("preset") or {}
    reset()
    import_any(job["input"], bool(p.get("animations", True)))
    report = {"input": job["input"], "warnings": []}
    if job.get("node"):
        kept, removed = keep_node(job["node"])
        report["kept_nodes"] = kept
        report["removed_objects"] = removed
    helpers = drop_helper_meshes()
    if helpers:
        report["removed_helpers"] = helpers
        report["warnings"].append(f"removed shadow/collision proxy meshes: {', '.join(helpers)}")
    if not any(o.type == "MESH" for o in bpy.context.scene.objects) and not bpy.data.actions:
        raise RuntimeError("model contains no mesh and no animation")
    report["textures"] = assign_textures(job.get("textures") or {})
    if any(t["state"] == "missing" for t in report["textures"]):
        report["warnings"].append("some declared textures are missing (exported with flat colour); inspect material diagnostics")
    if any(t["state"] == "unknown" for t in report["textures"]):
        report["warnings"].append("some materials have no verified texture binding; inspect material diagnostics")
    bb = world_bbox()
    scale = float(p.get("scale", 1.0))
    limit = float(job.get("max_plausible_m") or 150.0)
    size = max(bb["size_yup"]) * scale if bb else 0.0
    if p.get("auto_unit_fix", True) and size > limit:
        # Centimetre-authored file imported without unit scale: a 50 m "stool".
        scale *= 0.01
        report["warnings"].append(
            f"auto unit fix: max dimension {size:.1f} m > {limit:g} m plausible -> scaled by 0.01")
    elif (p.get("auto_unit_fix", True) and job.get("min_plausible_m")
          and 0 < size < float(job["min_plausible_m"]) and size * 100 <= limit):
        # Metre geometry in a file that declares centimetres: a 1 cm barrel.
        scale *= 100.0
        report["warnings"].append(
            f"auto unit fix: max dimension {size * 100:.1f} cm < {float(job['min_plausible_m']) * 100:g} cm"
            " plausible -> scaled by 100")
    apply_scale(scale)
    report["transforms_baked"] = normalize_transforms() if p.get("bake_transforms", True) else False
    report["decimated_objects"] = decimate(float(p.get("decimate", 1.0)))
    report["resized_images"] = limit_textures(int(p.get("max_texture", 0) or 0))
    report["bounds"] = world_bbox()
    report["stats"] = stats()
    export_glb(job["output"], p)
    report["output"] = job["output"]
    report["output_bytes"] = os.path.getsize(job["output"])
    return report


def run_preview(job):
    reset()
    bpy.ops.import_scene.gltf(filepath=job["input"])
    bb = world_bbox()
    if not bb:
        raise RuntimeError("nothing to render")
    mn, mx = bb["min_zup"], bb["max_zup"]
    center = mathutils.Vector([(mn[i] + mx[i]) / 2 for i in range(3)])
    radius = max(0.05, (mathutils.Vector(mx) - mathutils.Vector(mn)).length / 2)
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    direction = mathutils.Vector((1.0, -1.3, 0.8)).normalized()
    dist = radius / math.tan(cam_data.angle / 2) * 0.95
    cam.location = center + direction * dist
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(50), 0, math.radians(30))
    scene.collection.objects.link(sun)
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.82, 0.84, 0.88, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
    scene.world = world
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = int(job.get("samples", 24))
    scene.cycles.use_denoising = False
    size = int(job.get("size", 384))
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = job["output"]
    os.makedirs(os.path.dirname(job["output"]), exist_ok=True)
    bpy.ops.render.render(write_still=True)
    return {"output": job["output"], "size": size}


def run_measure(job):
    """Largest imported dimension (m) of each input, as Blender's importer scales it."""
    sizes = {}
    for path in job["inputs"]:
        reset()
        try:
            import_any(path, False)
        except Exception as exc:  # noqa: BLE001 - one unreadable reference must not sink the probe
            sizes[path] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        bb = world_bbox()
        sizes[path] = {"max_m": max(bb["size_yup"]) if bb else None}
    return {"sizes": sizes}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    with open(argv[0], encoding="utf-8") as fh:
        job = json.load(fh)
    result = {"ok": False}
    try:
        if job.get("mode") == "preview":
            result = {"ok": True, **run_preview(job)}
        elif job.get("mode") == "measure":
            result = {"ok": True, **run_measure(job)}
        else:
            result = {"ok": True, **run_convert(job)}
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-2000:]}
    with open(job["report"], "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=str)
    print(REPORT_MARK, flush=True)


main()
