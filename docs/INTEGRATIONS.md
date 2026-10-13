# External integrations report

Checks performed on **2026-10-13**. "Verified" means actually exercised in this environment, not inferred.

## synty-inventory — integrated, verified

| | |
|---|---|
| Repository | https://github.com/tom-swift-tech/synty-inventory |
| Version used | commit `7eee0bce9b28fe149db593375c385c2d278073a5` (pinned in `pyproject.toml`) |
| Licence | Apache-2.0 (compatible; its NOTICE: no Synty assets/catalogs included) |
| Upstream tests | `448 passed, 3 skipped` in a clean Python 3.12 venv |
| Integration mode | versioned Python dependency, **no changes** to the original code |

What it does: scans extracted Unity packs or `.unitypackage` files (without the Unity Editor), applies Synty naming
rules, type, semantic role, placement, modular kit families, ship and mech parts, bounds measured from GLBs,
footprints, recipes, a JSON catalog per pack and an SQLite FTS5 index.

APIs used by this server (`src/synty_mcp/inventory.py`, `search.py`, `glbinfo.py`):

- `catalog.scan_and_write(PackRef, catalogs_dir, threejs_v2=…)` and `catalog.rebuild_index()` for indexing;
- `catalog.load_all_catalogs()` for the in-memory view;
- `query.search_assets()` as the candidate generator, with its score and BM25 bonus;
- `query.suggest_recipes_for()` for assembly recipes;
- `sources.glb_measure.read_glb_json()` / `measure_glb()` for GLB facts.

Adaptations made in this project, without touching the library:

- The conversion cache `converted/threejs_web/<pack>/` is written in the `threejs_v2` layout synty-inventory expects
  (`manifest.json`), so a rescan measures `bounds` from the GLBs produced here.
- `.unitypackage` files are extracted by this server, safely, into an `Assets/…` tree with `.meta` files, because
  synty-inventory reads prefab GUIDs only from extracted trees.
- Source Files (FBX + `MaterialList_*.txt`, no prefabs) are indexed by building a `PackRef` directly: synty-inventory's
  `discover()` only recognises trees containing Unity prefabs.
- Known library limitation: for skinned meshes whose armature carries a 0.01 scale, `measure_glb` under-reports the
  size by 100×. Conversion here bakes the transforms when there are no animations, so the resulting GLBs have unit
  scale and measurements are correct (character height checked in three.js).

## SyntyDex — no API: optional provider, disabled

| Check | Result |
|---|---|
| `GET https://dex.syntystore.com/` | 200: HTML page of a **Unity WebGL** app (`productVersion 0.1.20260921`), data in `Build/synty-dex-deploy.data.br` |
| `robots.txt` | 404 |
| `StreamingAssets/`, `StreamingAssets/aa/catalog.json` | 404 |
| Public API documentation | none found (web search returned nothing relevant) |
| "Copy" feature in the UI | present (`#copy-buffer`): Dex copies asset names to the clipboard |

Conclusion: the catalog is compiled into the Unity bundle and there is no documented or permitted way to query it
automatically. No endpoints were invented and the WebGL bundle was **not** reverse-engineered.

Implemented:

- `search_syntydex`: with `syntydex_enabled=false` (default) it answers `available: false` with the reason. With a
  hand-made **local export** (names copied from Dex, one per line, optionally `Name<TAB>Pack`) it searches that file
  and links every result to local assets, labelled `user-provided … (not live)`.
- `match_syntydex_asset_to_local`: always available and offline. Maps a name seen on Dex (or anywhere) to indexed
  assets: exact id, then normalised id (`SM_`/`_01`), then all tokens.

## Synty Store "My Downloads" — Playwright with manual login (not verified on a real account)

| Check | Result |
|---|---|
| Platform | Shopify, with the Sky Pilot digital-downloads app (public Shopify/ShopCircle case studies) |
| Documented customer API | none found |
| Verified endpoints | none: an authenticated session is required and none was available here |

Following the brief's order of preference:

1. Official API: **does not exist** (as far as could be verified).
2. Verified existing endpoints: **none verifiable** without an account.
3. **Playwright with manual login**: implemented in `src/synty_mcp/store.py`.
   - `synty-mcp store-login` opens a real Chromium; you do the login (and any 2FA) yourself. The `storage_state` is
     saved in `secrets/` with 0600 permissions.
   - `sync_owned_packs` opens the `store_downloads_url` page (configuration: copy it from your browser, no guessed
     URL) and lists download links (configurable selector and regex). The real hrefs stay in `secrets/`; agents only
     see the title, the matched pack and an opaque `link_ref`.
   - `download_pack` streams the download with resume (`Range`) using the session cookies inside the process; if it
     receives HTML instead of a file it falls back to Playwright's downloader. Then it verifies, deduplicates,
     extracts and indexes.
   - The status stays `verified: false` until a real listing has been recorded.
4. **Manual import**: always available and **verified** (inbox, `import_archive`, `scripts/import_packs.sh`).

Still to be verified with a real account: the actual structure of the *My Downloads* page, the link selector, file
names, `Range` support on signed links and session lifetime.

## Synty licence — read, rules applied

Page: https://syntystore.com/pages/one-time-purchase-licence (read on 2026-10-13). Relevant clauses:

- no inclusion of the assets in datasets used by generative-AI programs or in their development;
- no generation of 3D models with generative AI from the assets;
- no uploading source files or models to third-party services for 3D model generation;
- source files may only be shared within your team; licences are per seat (some bundle purchases cover a single seat).

How the server applies them: no uploads to external services; search is local only; preview images do not enter
the agents' context (`allow_preview_images_to_agent: false`); `/files` requires authentication; the licence note is
included in every manifest and every `get_asset_file` response.

## Runtime components verified

| Component | Version | Check |
|---|---|---|
| MCP Python SDK | `mcp 2.3.0` (`MCPServer`) | stdio (subprocess) and Streamable HTTP with real clients |
| Blender | 4.2.3 LTS headless | FBX/OBJ/glTF → GLB, Cycles CPU render, also inside Docker |
| three.js | 0.170.0 `GLTFLoader` (Node) | GLBs loaded; SkinnedMesh bounds match Blender's measurement |
| Khronos glTF-Validator | 2.0.0-dev.3.10 | 0 errors on every produced GLB (warnings: `NODE_SKINNED_MESH_NON_ROOT`, `IMAGE_FEATURES_UNSUPPORTED`) |
| Docker / Compose | Docker Compose v5.1.2 | image built (1.87 GB); import, conversion, MCP over HTTP and rendering inside the container |
| Codex CLI | 0.160.1 | `configs/codex.config.toml` accepted by `codex mcp list/get` (stdio + streamable_http + bearer) |
