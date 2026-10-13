from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import fixtures  # noqa: E402


def blender_path() -> str | None:
    cand = os.environ.get("SYNTY_MCP_BLENDER") or shutil.which("blender")
    return cand if cand and Path(cand).exists() else None


@pytest.fixture
def settings(tmp_path):

    from synty_mcp.config import load_settings

    example = str(Path(__file__).parent / "data" / "test_packs.yaml")
    s = load_settings(data_dir=str(tmp_path / "data"), blender=blender_path(), workers=2, owned_packs_file=example,
                      source_roots=[str(tmp_path / "external")], export_roots=[str(tmp_path / "game")])
    s.ensure_dirs()
    return s


@pytest.fixture
def svc(settings):
    from synty_mcp.service import Service

    return Service(settings)


@pytest.fixture(scope="session")
def blender_models(tmp_path_factory) -> dict[str, bytes]:
    """Real FBX/OBJ/glTF models made by Blender (session-cached). Skips without Blender."""
    exe = blender_path()
    if not exe:
        pytest.skip("Blender not available (set SYNTY_MCP_BLENDER)")
    out = tmp_path_factory.mktemp("blender_models")
    script = out / "make.py"
    script.write_text(fixtures.BLENDER_FIXTURE)
    proc = subprocess.run([exe, "-b", "--factory-startup", "-noaudio", "--python", str(script), "--", str(out)],
                          capture_output=True, text=True, timeout=300)
    assert "FIXTURES_OK" in proc.stdout, proc.stdout[-2000:] + proc.stderr[-2000:]
    return {p.name: p.read_bytes() for p in out.iterdir() if p.suffix in (".fbx", ".obj", ".gltf", ".bin")}


@pytest.fixture
def indexed_pack(svc, tmp_path):
    """The fake pack imported from a store-style zip, extracted and indexed (placeholder models)."""
    z = fixtures.build_pack_zip(svc.settings.inbox_dir / "POLYGON_FakeVillage_Unity_2022_v1_0.zip")
    _register_fake_pack(svc)
    res = svc.import_archive(str(z), pack="polygon-fakevillage", wait=True, timeout=120)
    assert res["status"] == "done", res
    return svc


@pytest.fixture
def indexed_pack_real(svc, blender_models):
    """Same pack but with real Blender-made FBX models inside."""
    z = fixtures.build_pack_zip(svc.settings.inbox_dir / "POLYGON_FakeVillage_Unity_2022_v1_0.zip", blender_models)
    _register_fake_pack(svc)
    res = svc.import_archive(str(z), pack="polygon-fakevillage", wait=True, timeout=300)
    assert res["status"] == "done", res
    return svc


def _register_fake_pack(svc) -> None:
    """Tests use a synthetic 28th pack so no real Synty pack names are exercised with fake data."""
    from synty_mcp.db import dumps

    seed = {"slug": "polygon-fakevillage", "line": "POLYGON", "name": "Fake Village", "pack_id": "POLYGON_FakeVillage",
            "match": ["fakevillage"], "content": "environment", "hints": ["house", "well"]}
    svc.db.x("INSERT OR IGNORE INTO packs(slug, line, name, pack_id, content, seed) VALUES(?,?,?,?,?,?)",
             (seed["slug"], seed["line"], seed["name"], seed["pack_id"], seed["content"], dumps(seed)))
