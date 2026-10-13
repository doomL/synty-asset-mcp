# Example calls for every tool

Responses are real but **abridged** (`…`). They were produced on the synthetic test pack
(`POLYGON_FakeVillage`, no Synty content).
Asset references have the form `PACK_ID/ASSET_ID`. Queries can be written in English or Italian; some examples
below use Italian on purpose to show the bilingual parser.

## Library

### `list_owned_packs`
```json
{}
```
```json
{"owned": 13, "by_status": {"owned_not_downloaded": 12, "indexed": 1},
 "packs": [{"pack": "polygon-fakevillage", "name": "POLYGON Fake Village", "pack_id": "POLYGON_FakeVillage",
            "pack_id_confirmed": true, "status": "indexed", "indexed_assets": 8, "archives": [{"id": 1, …}],
            "trees": [{"kind": "unity", "read_only": false}, {"kind": "source", "read_only": false}],
            "store_downloads": []}, …],
 "note": "assets are only listed for packs with status 'indexed'"}
```

### `sync_owned_packs`
```json
{}
```
```json
{"inbox_imports": [{"file": "POLYGON_City_Unity_2022_v1_2.zip", "status": "imported", "pack": "polygon-city", …}],
 "store": {"provider": "synty_store", "available": false, "verified": false, "method": "manual_import_only",
           "missing": ["store_enabled=false", "no saved session: run `synty-mcp store-login` …"], "purchases_found": null},
 "packs": [{"pack": "polygon-city", "status": "downloaded", …}, …]}
```

### `download_pack`
```json
{"pack": "polygon-city", "kinds": ["unity", "source"], "extract": true, "wait": false}
```
Without a store session (real response):
```json
{"status": "store_unavailable", "available": false, "method": "manual_import_only", "pack": "polygon-city",
 "manual_import": "put the pack's .zip/.unitypackage in /data/inbox then call sync_owned_packs"}
```
With a session: `{"job_id": "…", "kind": "download_pack", "status": "running", …}` → then `get_job`.

### `import_archive`
```json
{"path": "/data/inbox/POLYGON_City_Unity_2022_v1_2.zip", "extract": true}
```
```json
{"job_id": "c1f0…", "status": "done", "result": {"status": "imported", "pack": "polygon-city", "kind": "unity",
 "sha256": "9b1e…", "verify": "zip ok (3 members)", "extract": {"scan": {"status": "indexed", "assets": 8}}}}
```

### `extract_pack`
```json
{"pack": "polygon-city", "force": false}
```
```json
{"status": "done", "result": {"extracted": [{"status": "already_extracted", "path": "…"}],
 "scan": {"pack": "polygon-city", "status": "indexed", "assets": 8, "catalog_source": "unity",
          "resolved_models": 8, "resolved_textures": …}}}
```

### `scan_library`
```json
{"register_path": "/home/me/games/my-project/Source_Files"}
```
```json
{"status": "done", "result": {"registered": {"pack": "<matched pack>", "kind": "source", "read_only": true},
 "scanned": [{"pack": "<matched pack>", "status": "indexed", "assets": …, "resolved_models": …, "resolved_textures": …}]}}
```

### `get_library_status`
```json
{"packs_owned": 13, "packs_by_status": {"owned_not_downloaded": 12, "indexed": 1}, "indexed_assets": 8,
 "conversions": [{"preset": "threejs_web", "status": "ok", "n": …}], "jobs_active": [],
 "disk_bytes": {"archives": …, "library": …, "converted": …}, "blender": "/usr/local/bin/blender",
 "providers": {"synty_store": {"available": false, …}, "syntydex": {"available": false, "mode": "disabled", …}}}
```

## Search

### `search_assets`
```json
{"query": "medieval house", "limit": 5}
```
```json
{"understood": {"qty": 1, "concept": "house", "modifiers": ["medieval"], "terms": ["house", "home", "cottage", …]},
 "results": [{"ref": "POLYGON_FakeVillage/SM_Bld_House_Medieval_01", "name": "House Medieval",
   "class": "prefab", "type": "building/module", "size_m": [6.0, 5.0, 7.0], "size_source": "converted_glb",
   "available": {"source_file": true, "convertible": true, "glb_cached": ["threejs_web"], "preview": true},
   "score": 60.0, "match": ["name:house", "+medieval"]}, …]}
```
No result for "astronave aliena" (Italian for "alien spaceship"; real response, nothing invented):
```json
{"query": "astronave aliena", "results": [],
 "owned_unindexed_candidates": [{"pack": "polygon-starport", "name": "POLYGON Starport",
   "status": "owned_not_downloaded", "because": ["ship", "spaceship"], "verified": false}],
 "note": "no indexed asset matches; nothing is invented. See owned_unindexed_candidates."}
```

### `suggest_assets_for_scene`
```json
{"request": "tre case medievali, un pozzo, due alberi, cinque sedie e un personaggio goblin"}
```
Italian for "three medieval houses, a well, two trees, five chairs and a goblin character".
```json
{"items": [
   {"request": "tre case medievali", "qty": 3, "status": "partial",
    "selected": [{"ref": "POLYGON_FakeVillage/SM_Bld_House_Medieval_01", …}, {"ref": "…_02", …}],
    "note": "only 2 distinct matching asset(s) indexed; reuse with variation or pick from alternatives"},
   {"request": "un pozzo", "qty": 1, "status": "found", "selected": [{"ref": "POLYGON_FakeVillage/SM_Prop_Well_01", …}]},
   {"request": "due alberi", "qty": 2, "status": "partial", …},
   {"request": "cinque sedie", "qty": 5, "status": "partial", …},
   {"request": "un personaggio goblin", "qty": 1, "status": "found",
    "selected": [{"ref": "POLYGON_FakeVillage/SK_Chr_Goblin_01", "class": "character", …}]}],
 "packs_used": {"POLYGON_FakeVillage": 7}, "missing": [], "recipes": [],
 "next_step": "prepare_asset_collection(refs=[...selected refs...], preset='threejs_web')"}
```

### `get_asset_details`
```json
{"ref": "POLYGON_FakeVillage/SM_Prop_Well_01"}
```
```json
{"ref": "…", "class": "prefab", "size_m": [1.6, 1.2, 1.6],
 "record": {"type": "prop", "description": "…", "placement": {"mount": "ground", "attachment": "base_to_ground", …}},
 "files": {"source_model": "/data/library/…/Models/SM_Prop_Well_01.fbx", "node_in_model": null,
           "unity_prefab": "/data/library/…/Prefabs/SM_Prop_Well_01.prefab", "textures": ["…/PolygonFakeVillage_Texture_01_A.png"]},
 "geometry": {"triangles": 12, "vertices": 24, "materials": ["PolygonFakeVillage_Mat_01_A"],
              "bounds": {"size": [1.6, 1.2, 1.6], "units": "m", "up": "+y"}},
 "conversions": [{"preset": "threejs_web", "path": "/data/converted/threejs_web/…/SM_Prop_Well_01.glb", "url": "…"}]}
```

### `find_similar_assets`
```json
{"ref": "SM_Prop_Chair_01", "limit": 3}
```
```json
{"asset": {"ref": "POLYGON_FakeVillage/SM_Prop_Chair_01", …},
 "similar": [{"ref": "POLYGON_FakeVillage/SM_Prop_Chair_02", "score": 80.0, …},
             {"ref": "POLYGON_FakeVillage/SM_Prop_Well_01", "score": 40.0, …}]}
```

### `list_pack_assets`
```json
{"pack": "polygon-fakevillage", "asset_class": "character", "offset": 0, "limit": 50}
```
```json
{"pack": "polygon-fakevillage", "pack_id": "POLYGON_FakeVillage", "total": 1,
 "assets": [{"ref": "POLYGON_FakeVillage/SK_Chr_Goblin_01", "class": "character", …}]}
```

### `get_asset_preview`
```json
{"ref": "POLYGON_FakeVillage/SM_Prop_Chair_01", "generate": true}
```
```json
{"available": true, "source": "rendered", "path": "/data/previews/POLYGON_FakeVillage/SM_Prop_Chair_01.png",
 "url": "https://synty.example.com/files/previews/…", "viewer": "https://synty.example.com/ui/#asset=…",
 "image_inline": false, "note": "preview kept local: image bytes are not sent into the agent context …"}
```

## Conversion

### `convert_asset_to_glb`
```json
{"ref": "POLYGON_FakeVillage/SK_Chr_Goblin_01", "preset": "threejs_web"}
```
```json
{"status": "done", "result": {"status": "converted", "path": "/data/converted/threejs_web/POLYGON_FakeVillage/SK_Chr_Goblin_01.glb",
 "url": "…", "textures": [{"material": "PolygonFakeVillage_Mat_01_A", "texture": "PolygonFakeVillage_Texture_01_A.png", "how": "material_name"}],
 "glb": {"bytes": 5540, "triangles": 12, "skins": 1, "animations": ["Root|Root|Goblin_Walk"],
         "bounds": {"size": [0.6, 1.2, 0.4], "units": "m", "up": "+y"}},
 "warnings": []}}
```
A second identical call returns `"status": "cached"` without starting Blender.

### `get_asset_file`
```json
{"ref": "POLYGON_FakeVillage/SK_Chr_Goblin_01", "format": "glb", "preset": "threejs_web"}
```
```json
{"available": true, "format": "glb", "path": "/data/converted/threejs_web/…/SK_Chr_Goblin_01.glb", "url": "…",
 "glb": {…}, "licence": "Synty Studios assets licensed to the library owner. …"}
```
`"format": "source"` returns the original FBX (never modified), with node and textures.

### `optimize_asset`
```json
{"ref": "POLYGON_FakeVillage/SK_Chr_Goblin_01", "preset": "threejs_mobile", "max_texture": 512}
```
```json
{"status": "done", "result": {"glb": {"bytes": 5116, "extensions_required": ["KHR_draco_mesh_compression"], …}},
 "comparison_vs_original_quality": {"bytes": [5540, 5116], "triangles": [12, 12]}}
```

### `prepare_asset_for_godot`
```json
{"ref": "POLYGON_FakeVillage/SM_Prop_Well_01", "export_to": "/home/me/games/godot-proj/synty", "godot_res_root": "res://synty"}
```
```json
{"status": "done", "result": {"assets": 1, "exported_to": "/home/me/games/godot-proj/synty"},
 "godot_notes": ["Copy models/*.glb (+ .tscn) under res:// …", "Units are metres and +Y up …", …]}
```
Generated `.tscn`:
```
[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://synty/models/POLYGON_FakeVillage__SM_Prop_Well_01.glb" id="1_glb"]

[node name="SM_Prop_Well_01" instance=ExtResource("1_glb")]
```

### `get_asset_dependencies`
```json
{"ref": "POLYGON_FakeVillage/SM_Bld_House_Medieval_02"}
```
```json
{"model": {"path": "…/Models/Buildings.fbx", "node": "SM_Bld_House_Medieval_02", "shared_bundle": true},
 "unity_prefab": "…/Prefabs/SM_Bld_House_Medieval_02.prefab",
 "materials": {"PolygonFakeVillage_Mat_01_A": "…/Textures/PolygonFakeVillage_Texture_01_A.png"},
 "textures_by_slot": ["…/PolygonFakeVillage_Texture_01_A.png"],
 "modular_kit": {"family": "House", "role": "misc", "pieces_by_role": {"misc": ["SM_Bld_House_Medieval_01", "…_02"]}},
 "embedded_in_glb": "textures are embedded in converted GLBs; no external files needed"}
```

### `prepare_asset_collection`
```json
{"refs": [{"ref": "SM_Bld_House_Medieval_01", "qty": 3}, "SM_Prop_Well_01", {"ref": "SM_Env_Tree_Pine_01", "qty": 2},
          {"ref": "SM_Prop_Chair_01", "qty": 5}, "SK_Chr_Goblin_01", "SM_Prop_Dragon_01"],
 "name": "fantasy_village", "preset": "threejs_web"}
```
```json
{"status": "done", "result": {"collection": "fantasy_village", "manifest": "/data/collections/fantasy_village/manifest.json",
 "manifest_url": "…", "zip": "/data/collections/fantasy_village.zip", "zip_url": "…", "assets": 5,
 "missing": [{"ref": "SM_Prop_Dragon_01", "reason": "asset not found in the indexed catalogs: …"}]}}
```
Full manifest: [manifest.example.json](manifest.example.json).

## SyntyDex

### `search_syntydex`
```json
{"query": "medieval house"}
```
Default (real response):
```json
{"provider": "syntydex", "available": false, "mode": "disabled",
 "integration": "no documented API; live querying not implemented by design",
 "reason": "SyntyDex provider is disabled (syntydex_enabled=false).", "results": []}
```
With a local export enabled: `{"available": true, "source": "user-provided SyntyDex export (not live)", "results": [{"name": "…", "local": [{"ref": "…"}]}]}`.

### `match_syntydex_asset_to_local`
```json
{"name": "Prop Chair"}
```
```json
{"matches": [{"ref": "POLYGON_FakeVillage/SM_Prop_Chair_01", "match": "normalized_id", "confidence": 90, …},
             {"ref": "POLYGON_FakeVillage/SM_Prop_Chair_02", "match": "normalized_id", "confidence": 90, …}], "note": null}
```

## Jobs

### `get_job`
```json
{"job_id": "cd9b10921a2f", "with_log": true}
```
```json
{"job_id": "cd9b10921a2f", "kind": "collection", "status": "done", "progress": 1.0, "result": {…},
 "log": "blender convert SM_Prop_Well_01 [threejs_web]\n…"}
```

### `list_jobs`
```json
{"status": "running"}
```
