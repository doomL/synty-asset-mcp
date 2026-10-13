# Architecture

```
 agents (Claude Code, Codex, Grok…)            browser
   │ stdio            │ Streamable HTTP + Bearer   │ HttpOnly cookie
   ▼                  ▼                            ▼
 mcp_server.py ── MCPServer (mcp 2.x) ──┐   http_app.py: /mcp /api /files /ui  (ASGI AuthMiddleware)
                                         ▼
                                    service.py   ← one function per tool, shared by MCP and the dashboard
        ┌──────────────┬───────────────┼──────────────────┬────────────────┬───────────────┐
   library.py      inventory.py     search.py         convert/          store.py       syntydex.py
   import/extract  adapter to       EN/IT lexicon +   headless Blender  Playwright     local export
   archives.py     synty-inventory  rerank + scenes   cache + locks     (optional)     (optional)
   (zip/unitypkg)  unity.py (GUID,  lexicon.py        glbinfo.py
   packs.py        .mat, MaterialList)
        └──────────────┴───────────────┴──────── db.py (SQLite WAL) ── jobs.py (pool, dedupe, logs)
```

## Data on disk (`data_dir`)

| Directory | Contents | Written by |
|---|---|---|
| `archives/<pack>/` | original downloads, read-only (0444), deduplicated by SHA-256 | import |
| `inbox/` | files to import (manual or downloaded from the store) | user / store |
| `library/<pack>/unity/` | `Assets/…` tree + `.meta` rebuilt from the `.unitypackage` | extraction |
| `library/<pack>/<kind>_<id>/` | rest of the ZIP (Source Files, Godot, …) | extraction |
| `library/<pack>/unity_previews/` | the package's `preview.png`, per asset | extraction |
| `catalogs/` | **synty-inventory** JSON catalogs + `index.json` + `search.db` | scan |
| `converted/<preset>/<PACK_ID>/` | cached GLBs; `threejs_web` doubles as synty-inventory's `threejs_v2` | conversion |
| `previews/` | Blender renders | previews |
| `collections/<name>/` | `models/*.glb`, `.tscn`, `manifest.json`, zip | collections |
| `secrets/` (0700) | Synty Store session, signed links | `store.py` only; never served |
| `state.sqlite3` | packs, archives, trees, GUIDs, per-asset file resolution, conversions, jobs, store items | all |

## Flow "one object → GLB"

1. `search_assets("well")` → `lexicon.analyse_phrase` → concept `well` + synonyms → candidates from
   `synty_inventory.query.search_assets` → rerank (head noun required, right asset class, style, penalties for
   bundles and kit pieces) → rows with availability.
2. `get_asset_file(ref, "glb")` → `asset_files` gives the source model, node inside a bundle, textures per slot.
3. `Converter.convert` computes the cache key (source + mtime/size + preset + options + textures + version).
   File lock per key (several processes); hit → returns immediately. Miss → `blender -b --python blender_job.py`.
4. `blender_job.py`: import → isolate node → wire textures → type-aware unit fix → bake transforms (without
   animations) → decimation / texture cap → GLB export (+Y, metres).
5. `glbinfo` reads only the GLB's JSON chunk (triangles, materials, animations, bounds), without loading geometry.

## Concurrency and idempotency

- SQLite in WAL mode, one connection per thread, `BEGIN IMMEDIATE` for writes.
- Jobs with the same `dedupe_key` (e.g. `convert:PACK/ID:preset:options-hash`) are not started twice while one is
  queued or running; jobs left "running" by a dead process are marked `interrupted`.
- Idempotent import (SHA-256), idempotent extraction (unless `force`), cached conversions, manifests written
  atomically.
- Large files: hashing, CRC, extraction and downloads stream in 1 MiB chunks; files are served with
  `FileResponse` (streaming).

## Security boundaries

- Every path supplied by an agent goes through `safepaths.confine()` (symlinks resolved) against explicit roots:
  `inbox` + `source_roots` for reading, `collections` + `export_roots` for writing.
- Archive members go through `safe_member_path()`. Limits: `max_extract_bytes`, `max_extract_files`.
- HTTP: token required (also in `sha256:` form); Host header check against DNS rebinding (MCP SDK);
  `/files` limited to `converted|collections|previews|library`; `secrets/` never reachable.
