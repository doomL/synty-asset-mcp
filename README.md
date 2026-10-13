# Synty Asset MCP

[![tests](https://github.com/doomL/synty-asset-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/doomL/synty-asset-mcp/actions/workflows/test.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue) ![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20streamable%20HTTP-6f42c1) ![license](https://img.shields.io/badge/license-Apache--2.0-green)

An MCP server that turns **your own** purchased Synty packs into a library AI agents can query
(Claude Code, Codex, Grok…): search individual assets in English or Italian, inspect their size and geometry,
extract only what is needed and convert it to a **GLB ready for Three.js** or **Godot 4**, with a shared cache.

- Cataloguing and semantics: **[synty-inventory](https://github.com/tom-swift-tech/synty-inventory)**, used as a
  library (dependency pinned to commit `7eee0bc`, original code unmodified).
- Conversion: **headless Blender** (FBX / OBJ / glTF / GLB → GLB), textures wired from the Unity materials or the
  Source Files `MaterialList`, metres and +Y up, animations preserved.
- Access: **MCP stdio** (local) and **MCP Streamable HTTP** with tokens (remote, several agents at once).
- Optional **web dashboard** with a Three.js 3D viewer.

> This project contains no Synty assets, generated catalogs or previews. It is not affiliated with Synty Studios.
> Read [Security and licensing](#security-and-licensing) before exposing it on a network.

Related documents: [tested / untested features and limitations](docs/TESTING_STATUS.md) ·
[verified external integrations](docs/INTEGRATIONS.md) · [architecture](docs/ARCHITECTURE.md) ·
[Synty Store login](docs/STORE_LOGIN.md) · [example tool calls](examples/tool-calls.md) ·
[example manifest](examples/manifest.example.json)

---

## Installation on Linux

Requirements: Python 3.11+, `git`, Blender 4.x (for conversions; search works without it).

```bash
git clone https://github.com/doomL/synty-asset-mcp.git && cd synty-asset-mcp
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # also installs synty-inventory from the pinned commit

# Headless Blender (if not installed already)
curl -fsSL https://download.blender.org/release/Blender4.2/blender-4.2.3-linux-x64.tar.xz | tar -xJ -C ~/.local/opt
export SYNTY_MCP_BLENDER=~/.local/opt/blender-4.2.3-linux-x64/blender

cp examples/config.example.yaml config.yaml   # set data_dir and, optionally, source_roots / export_roots
export SYNTY_MCP_CONFIG=$PWD/config.yaml
synty-mcp packs                               # owned packs and their status
```

### Importing packs

Download your packs from your Synty Store account (*My Downloads*) and put them in the inbox folder, or import
them directly. Both the download `.zip` files (containing a `.unitypackage` and/or `Source_Files`) and bare
`.unitypackage` files work.

```bash
scripts/import_packs.sh ~/Downloads/POLYGON_*.zip      # import + verify + extract + index
# or
cp ~/Downloads/*.zip data/inbox/ && synty-mcp inbox && synty-mcp scan
```

- Archives are deduplicated by **SHA-256**: importing the same file again is a no-op.
- Integrity: ZIP CRCs / gzip are verified while streaming; a corrupt archive is never extracted.
- Originals stay untouched in `data/archives/<pack>/` (hard link when possible, otherwise a copy).
- The file name is matched to one of the owned packs (`POLYGON_City_Unity_2022_v1_2.zip` → *POLYGON City*);
  if it does not match, pass `--pack polygon-city`.

Already have an extracted folder (e.g. `Source_Files` inside a Godot project)? Add it to `source_roots` in the
config and register it **read-only**, without copying it:

```bash
synty-mcp scan --register /path/to/project/Source_Files
```

### Command line

```bash
synty-mcp search "medieval house"
synty-mcp search --scene "three medieval houses, a well, two trees, five chairs and a goblin character"
synty-mcp convert POLYGON_City/SM_Bld_House_01 --preset threejs_web
synty-mcp status
```

### Owned packs

You do not have to list your packs by hand: the pack list builds itself from what you actually have.

- **Synty Store sync** (optional, see [STORE_LOGIN](docs/STORE_LOGIN.md)): every downloadable purchase found on your
  *My Downloads* page becomes an owned pack.
- **Imported archives**: a file named like `POLYGON_City_Unity_2022_v1_2.zip` registers *POLYGON City* with its
  confirmed Synty id.

Optionally you can pre-declare packs (e.g. to get "probably in pack X, not indexed yet" hints before downloading) in a
local `data/owned_packs.yaml` (or `owned_packs_file` in the config), using
[`owned_packs.example.yaml`](src/synty_mcp/data/owned_packs.example.yaml) as a template. That file is local
configuration and is git-ignored. Without it, nothing is claimed as owned until it is found in the store or imported.

---

## Connecting agents

### Claude Code (local, stdio)

```bash
claude mcp add synty -s user -e SYNTY_MCP_CONFIG=$PWD/config.yaml -- $PWD/.venv/bin/synty-mcp serve
```

or the project file [`configs/claude-code.mcp.json`](configs/claude-code.mcp.json) (copy it as `.mcp.json`).

### Claude Code (remote, HTTP)

```bash
claude mcp add --transport http synty https://synty.example.com/mcp --header "Authorization: Bearer $SYNTY_TOKEN"
```

### Codex CLI

[`configs/codex.config.toml`](configs/codex.config.toml) → merge into `~/.codex/config.toml` (local stdio or remote HTTP).

### Grok

[`configs/grok_remote_mcp.py`](configs/grok_remote_mcp.py): example connecting Grok to the remote endpoint through
the xAI API's remote MCP tools. Not tested end-to-end: see [TESTING_STATUS](docs/TESTING_STATUS.md).

### Typical agent workflow

1. `suggest_assets_for_scene("small fantasy town: houses, shops, trees and NPCs")`
2. `get_asset_details(ref)` for the chosen assets
3. `prepare_asset_collection(refs=[...], preset="threejs_web")` → converts **only** those assets (from the cache if
   already done) and returns a `manifest.json` with files, bytes, triangles, bounds in metres, materials,
   animations and missing items.
4. The agent places the GLBs in the project according to `bounds_m` (metres, +Y up, base at y=0).

Missing assets are never invented: they appear under `missing` / `not_found`, together with owned packs that are
not indexed yet and *probably* contain them (flagged as unverified).

---

## MCP tools

| Group | Tools |
|---|---|
| Library | `list_owned_packs`, `sync_owned_packs`, `download_pack`, `extract_pack`, `import_archive`, `scan_library`, `get_library_status` |
| Search | `search_assets`, `get_asset_details`, `find_similar_assets`, `suggest_assets_for_scene`, `list_pack_assets`, `get_asset_preview` |
| Conversion | `get_asset_file`, `convert_asset_to_glb`, `prepare_asset_for_godot`, `optimize_asset`, `get_asset_dependencies`, `prepare_asset_collection` |
| SyntyDex | `search_syntydex`, `match_syntydex_asset_to_local` |
| Jobs | `get_job`, `list_jobs` |

Asset reference: `PACK_ID/ASSET_ID` (e.g. `POLYGON_City/SM_Bld_House_01`); the bare id is enough when it is unique.
Full examples of every call: [examples/tool-calls.md](examples/tool-calls.md).

### Conversion presets

| Preset | Use | Details |
|---|---|---|
| `threejs_web` | web default | plain GLB (`GLTFLoader` only), textures ≤ 2048 px |
| `threejs_mobile` | mobile | Draco (needs `DRACOLoader`), JPEG ≤ 1024 px |
| `godot4` | Godot 4 | no Draco (Godot cannot decode it), textures ≤ 2048 px, `.tscn` wrapper |
| `original_quality` | archival | no reduction |

Overridable options: `max_texture`, `image_format`, `draco`, `decimate`, `animations`, `scale`,
`auto_unit_fix`, `bake_transforms`. Unity shaders, particle systems and components are **not** converted.

---

## Dashboard

`synty-mcp serve --transport http` → `http://127.0.0.1:8765/ui/` (log in with the same token). It shows packs and
their status, search with class/pack/availability filters, details and geometry, an **interactive 3D preview** of
the GLB (with a 1.8 m reference figure), multi-select, collection preparation (zip + manifest) and job status.
The dashboard is optional: the MCP server works without it.

---

## Docker Compose (VPS)

```bash
cp .env.example .env
docker compose run --rm synty-mcp token      # generate a token: put the sha256:... form in .env
docker compose up -d --build
cp ~/Downloads/POLYGON_*.zip inbox/ && docker compose exec synty-mcp synty-mcp inbox
docker compose exec synty-mcp synty-mcp scan
```

- The port is published on `127.0.0.1:8765` only: expose it through a TLS reverse proxy (Caddy, nginx) or a tunnel.
- Separate volumes for archives, extracted packs, GLB cache, catalogs, state and secrets.
- The server **refuses to start** over HTTP without tokens (unless `allow_unauthenticated_localhost` on loopback).
- Blender 4.2 LTS is included in the image. Playwright only with `WITH_PLAYWRIGHT=1`.

---

## Security and licensing

- Assets stay on your server: `/files/*` requires the token and is confined to the library directories;
  `secrets/` (store session) is never served nor returned to agents.
- Safe extraction: path traversal is blocked (`..`, absolute paths, drive letters, symlinks), byte and file-count
  limits apply, nothing is written outside the configured roots. Exports only into `export_roots`.
- Search is **local** (EN/IT lexicon + synty-inventory metadata + FTS5). No commercial API.
- **Previews are not sent** into the agent's context (`allow_preview_images_to_agent: false`): the agent gets a
  path/URL; the images can be viewed in the dashboard.
- Synty licence (checked 2026-10-13, see [INTEGRATIONS](docs/INTEGRATIONS.md)): no use in generative-AI datasets,
  no 3D model generation with generative AI, no uploading source files or models to third-party services for 3D
  generation; source files may only be shared within your team. Licences are per seat (some bundle purchases
  cover a single seat): do not hand the token to people outside your licence. Using the assets to *compose* your own scenes (what this server
  does) is normal use in your own products.

## Tests

```bash
pytest                                                   # full suite; Blender tests are skipped without it
SYNTY_MCP_BLENDER=/path/blender pytest -m blender        # real conversions on synthetic models
SYNTY_MCP_LIVE_SOURCE=/path/Source_Files SYNTY_MCP_BLENDER=/path/blender pytest -m live   # your own real pack
python scripts/mcp_smoke.py http http://127.0.0.1:8765/mcp --token $TOKEN --ref PACK/ASSET --convert
```

Code licence: Apache-2.0. Synty and POLYGON are trademarks of Synty Studios Limited.
