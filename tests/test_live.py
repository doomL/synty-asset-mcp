"""Optional end-to-end test on a real, licensed Synty folder on this machine.

    SYNTY_MCP_LIVE_SOURCE=/path/to/Source_Files SYNTY_MCP_LIVE_PACK=<pack-slug> \
    SYNTY_MCP_BLENDER=/path/to/blender pytest -m live

The folder is registered read-only (scanned in place, never copied into the
repo) and must contain at least one SK_*/SM_* FBX.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live
LIVE = os.environ.get("SYNTY_MCP_LIVE_SOURCE")


@pytest.mark.skipif(not LIVE, reason="SYNTY_MCP_LIVE_SOURCE not set")
def test_real_pack_search_and_glb(tmp_path):
    from conftest import blender_path

    from synty_mcp.config import load_settings
    from synty_mcp.glbinfo import glb_info
    from synty_mcp.service import Service

    src = Path(LIVE).resolve()
    svc = Service(load_settings(data_dir=str(tmp_path / "data"), source_roots=[str(src.parent)],
                                blender=blender_path()))
    res = svc.scan_library(register_path=str(src), pack=os.environ.get("SYNTY_MCP_LIVE_PACK"), wait=True,
                           timeout=1800)
    assert res["status"] == "done", res
    scanned = res["result"]["scanned"][0]
    assert scanned["assets"] > 0 and scanned["resolved_models"] > 0
    model = next(a for a in svc.cache.iter_assets() if a[1]["id"].startswith(("SK_", "SM_")))
    ref = f"{model[0]}/{model[1]['id']}"
    hits = svc.search_assets(model[1]["name"] or model[1]["id"], limit=20)["results"]
    assert ref in [h["ref"] for h in hits]
    if not svc.settings.blender_executable():
        pytest.skip("no Blender for the conversion half")
    f = svc.get_asset_file(ref, format="glb", wait=True, timeout=600)
    assert f["available"], f
    g = glb_info(Path(f["path"]))
    assert g["triangles"] > 0 and g["images"], g
    size = f["glb"]["bounds"]["size"]  # as reported by the MCP (Blender-evaluated for skinned meshes)
    assert 0.05 < max(size) < 200, size
    if model[1]["id"].startswith("SK_"):
        assert 1.0 < size[1] < 3.0, f"character height {size[1]} m"
