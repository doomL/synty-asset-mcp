# Test status: what is tested, what is not, limitations

Date: 2026-10-13. Environment: Linux, Python 3.12.3, Blender 4.2.3 LTS, Docker Compose v5.1.2.

## Automated suite

```
pytest                                   # 83 passed (with Blender + a real pack)
pytest  (without Blender / real pack)    # 74 passed, 9 skipped
```

| File | Covers |
|---|---|
| `tests/test_library.py` | seeding the owned-packs list; archive-name → pack matching (13 cases); SHA-256 dedupe; original left untouched; CRC/corruption; zip-slip, absolute paths, drive letters, symlinks, symlinked directories, zip bombs (bytes and file count); malicious `.unitypackage`; extraction + indexing; bundle FBX → node; textures from `.mat`; thumbnails; read-only registration and refusal outside `source_roots`; store unavailable → fallback |
| `tests/test_search.py` | EN/IT parsing with quantities (10 phrases); the brief's multi-item request; engine words ignored ("Three.js"); `MaterialList` compact/expanded parser; house ≠ chair; IT ≡ EN; "goblin character" → characters only; partial quantities without invented filler; non-existent items → `not_found`; owned-but-unindexed packs flagged as unverified; details/similar/dependencies; unindexed pack |
| `tests/test_diagnostics.py` | uninspected content remains unknown; skins and clips are independent; animation export options visible; per-mesh material references distinguish missing, flat and unknown |
| `tests/test_server.py` | **in-process** MCP client and **stdio subprocess** client (like Claude Code/Codex): the 20 required tools present and called; HTTP: 401 without token, wrong token, login cookie, `sha256:` tokens, foreign Host rejected, `/files` confined (4 traversal attempts → 404), refusal to start without tokens; SyntyDex disabled and local export; job dedupe and failure; conversion without Blender → explicit error; previews not sent inline |
| `tests/test_convert.py` (marker `blender`) | FBX → GLB with metre sizes and +Y up, base at y=0, embedded texture, original hash unchanged, cache; node extraction from a bundle; animation and skin preserved; Draco in the mobile preset, no Draco for Godot; option validation; OBJ and glTF; type-aware cm→m fix (stool yes, building no); 3 concurrent identical conversions → one; collection with quantities, missing items, zip, **only the requested assets converted**; Godot `.tscn` export into `export_roots` and refusal elsewhere; PNG render; shared source material with per-mesh textured, explicitly flat and missing bindings; content/provenance reports |
| `tests/test_live.py` (marker `live`) | a real Source Files pack registered read-only: scan, search, GLB with textures, character height between 1 and 3 m |

All fixtures are synthetic (`tests/fixtures.py`): PNGs, `.unitypackage` files, store-style ZIPs and models generated
by Blender at test time. The repository contains no Synty files.

## Manual checks with real MCP calls

- `scripts/mcp_smoke.py` over **Streamable HTTP** against the local server and against the **Docker container**:
  23 tools exposed, none of the 20 required ones missing, every call `ok`, including conversion, optimisation,
  Godot, collection and preview.
- A real, licensed Synty Source Files pack on the development machine (scanned read-only, never copied): models
  and textures resolved; conversion with all 4 presets (well under a second per asset); a node extracted from a
  multi-character bundle FBX; GLBs validated (0 errors) and loaded in three.js at a plausible character height.
- Dashboard in Chrome (synthetic pack): every tab working, three.js viewer with DRACO/GLTFLoader from the CDN.
- Docker: image built, healthcheck `healthy`, inbox import, extraction, conversion with the container's Blender,
  Cycles render, `/files` download 200 with the token and 401 without.

## Not tested (or only partially)

| Feature | Status | Why / what is needed |
|---|---|---|
| Synty Store: login, purchase listing, download | **verified on the owner’s real account** | Claude Code reported 28 purchased packs, 135 files and four packs downloaded, verified, extracted and indexed on 2026-10-13. This follow-up did not repeat network downloads. See [STORE_LOGIN](STORE_LOGIN.md). |
| Download resume (`Range`) | not tested | depends on the store's signed links |
| Live SyntyDex | **intentionally not implemented** | no documented API; only the local export remains |
| Dashboard in a browser | **tested in Chrome with synthetic data** | login, search, details, 3D viewer (GLB loaded, 6.00 × 5.00 × 7.00 m measured by three.js), scene, packs, collection (zip + manifest), jobs. Not tried with real Synty packs, to avoid viewing their images |
| Claude Code with `configs/claude-code.mcp.json` | not run | standard `.mcp.json` format; the stdio server is tested with an equivalent real MCP client |
| Grok (`configs/grok_remote_mcp.py`) | **not tested** | needs an xAI key and a public server; check against current xAI docs |
| Real `.unitypackage` packs (real Synty prefabs + `.mat`) | tested with synthetic fixtures only | synthetic fixtures cover GUIDs/`.mat`; Claude Code subsequently downloaded and indexed real packs, but detailed real-prefab coverage has not been established |
| Real ANIMATION / INTERFACE / SIDEKICK / FX packs | ANIMATION conversion verified; other categories not tested | Goblin Locomotion was downloaded and used with real characters. INTERFACE / SIDEKICK / FX pack coverage remains unverified. |
| Godot: import in the editor | not tested | GLBs are valid glTF without Draco; the `.tscn` uses the standard format 3 |

## Known limitations

- **Shaders, particle systems, Unity components, LOD groups and composite prefabs** (several nested meshes or
  prefabs inside prefabs) are not rebuilt: conversion exports the prefab's main mesh. Materials become glTF PBR with
  the albedo texture (roughness 0.8, non-metallic).
- Per-mesh MaterialList bindings preserve explicitly flat and unresolved declared slots. Otherwise textures are assigned in this order: material name → prefab/MaterialList slot order → path embedded in the FBX →
  pack atlas. Normal and emissive maps are not wired.
- When animations are present, armature transforms are **not** baked (to keep the keys valid): those GLBs may have
  a root node at 0.01 scale. They display correctly in three.js, but synty-inventory's static measurement may be 100×
  too small; in that case use the Blender measurement in the conversion report.
- Animation retargeting between different rigs is not automatic.
- The EN/IT lexicon covers about 150 typical game concepts: out-of-lexicon words are searched as raw tokens and
  only match literally. Add synonyms in `src/synty_mcp/lexicon.py`.
- The `hints` of unindexed packs are hand-written expectations from the pack description, always flagged
  `verified: false`.
- The Synty `pack_id` of each owned pack is a guess until a real archive name confirms it (`pack_id_confirmed`).
- Jobs run in threads inside the server process. Several processes (one stdio server per agent + HTTP) share the
  SQLite database and the file locks for conversions, but each process has its own pool.
- During development a render of one real Synty asset was opened **once** to check texture assignment, so that
  image passed through the AI model writing the code. From then on previews were verified numerically only
  (dimensions, pixel coverage), without viewing them.

## Follow-up verification (2026-10-13)

The full suite was rerun with Blender: 80 passed, one live test skipped. The live Source Files test was then run separately and passed. Two store parser/privacy regression tests were added afterward. Browser listing errors and downloader fallback errors are sanitized before they reach job logs, and login hints do not print private URLs.

Claude Code’s local session also reports a complete real-account workflow: store sync, four downloaded packs, Italian scene search, and a collection of 26 GLBs exported into a private Three.js demo. Those assets and session files remain ignored and are not part of this repository.
