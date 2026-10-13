"""Archive import / integrity / extraction safety / pack matching / indexing."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import fixtures
import pytest

from synty_mcp import archives, library, packs
from synty_mcp.safepaths import PathViolation, confine, safe_member_path


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_seed_from_pack_list(svc):
    res = svc.list_owned_packs()
    assert res["owned"] == 12
    assert res["by_status"] == {"owned_not_downloaded": 12}
    assert res["pack_list"].endswith("test_packs.yaml")
    lines = {p["line"] for p in res["packs"]}
    assert lines == {"POLYGON", "POLYGON MINI", "SIMPLE", "ANIMATION", "INTERFACE", "SIDEKICK"}
    # Nothing is claimed as available before indexing.
    assert all(not p["indexed_assets"] for p in res["packs"])


def test_without_list_packs_are_discovered_not_invented(tmp_path):
    from synty_mcp.config import load_settings
    from synty_mcp.service import Service

    svc = Service(load_settings(data_dir=str(tmp_path / "d")))
    assert svc.list_owned_packs()["owned"] == 0  # nothing claimed as owned
    z = fixtures.build_pack_zip(svc.settings.inbox_dir / "POLYGON_Knights_Unity_2022_v1_1.zip")
    res = library.import_archive(svc.settings, svc.db, z)
    assert res["pack"] == "polygon-knights"
    row = svc.list_owned_packs()["packs"][0]
    assert (row["name"], row["pack_id"], row["pack_id_confirmed"]) == ("POLYGON Knights", "POLYGON_Knights", True)


@pytest.mark.parametrize("name,slug", [
    ("POLYGON_Mini_Legends_Characters_Unity_2022_v1_1.zip", "polygon-mini-legends-characters"),
    ("POLYGON_Legends_Characters_SourceFiles_v1_2.zip", "polygon-legends-characters"),
    ("POLYGON_Mini_Legends_Unity_v1.zip", "polygon-mini-legends"),
    ("PolygonStarport_Unity_2021.zip", "polygon-starport"),
    ("SIDEKICK_Crew_v1_0.unitypackage", "sidekick-test-crew"),
    ("ANIMATION_Test_Locomotion_v1.zip", "animation-test-locomotion"),
    ("POLYGON_Corsairs_Pack_Unity_5_6.zip", "polygon-corsair"),
    ("SIMPLE_Hamlet_Unity.zip", "simple-hamlet"),
    ("POLYGON_Hamlet_Unity_2022.zip", "polygon-hamlet"),
    ("INTERFACE_Test_HUD_v1.zip", "interface-test-hud"),
    ("POLYGON_Metro_Characters_SourceFiles.zip", "polygon-metro-characters"),
    ("POLYGON_Metro_Pack_Unity.zip", "polygon-metro"),
    ("MaterialList_POLYGONMetroCharacters.txt", "polygon-metro-characters"),
])
def test_archive_names_match_owned_pack(svc, name, slug):
    assert packs.match_pack(svc.db, name)[0] == slug


def test_unrelated_name_does_not_match(svc):
    assert packs.match_pack(svc.db, "holiday_photos_2024.zip")[0] is None


def test_kind_version_and_pack_id_from_filename():
    assert packs.guess_kind("POLYGON_Hamlet_SourceFiles_v2.zip") == "source"
    assert packs.guess_kind("POLYGON_Hamlet_Unity_2022.zip") == "unity"
    assert packs.guess_kind("x.unitypackage") == "unity"
    assert packs.guess_version("POLYGON_Hamlet_Unity_v1_2_3.zip") == "1.2.3"
    assert packs.pack_id_from_filename("POLYGON_Hamlet_Unity_2022_v1_2.zip") == "POLYGON_Hamlet"
    assert packs.pack_id_from_filename("random.zip") is None


def test_safe_member_path_rejects_escapes(tmp_path):
    for bad in ("../x", "a/../../x", "/etc/passwd", "C:/x", "C:\\x", "a\x00b", ""):
        with pytest.raises(PathViolation):
            safe_member_path(tmp_path, bad)
    assert safe_member_path(tmp_path, "a/b/c.txt") == tmp_path.resolve() / "a/b/c.txt"


def test_safe_member_path_rejects_symlinked_dir(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "link").symlink_to(outside)
    with pytest.raises(PathViolation):
        safe_member_path(dest, "link/file.txt")


def test_confine(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "f").write_text("x")
    assert confine(root / "f", [root]) == (root / "f").resolve()
    with pytest.raises(PathViolation):
        confine(root / ".." / "f", [root])


def test_evil_zip_extracts_only_safe_members(tmp_path):
    z = fixtures.build_evil_zip(tmp_path / "evil.zip")
    dest = tmp_path / "out"
    rep = archives.extract_zip(z, dest, max_bytes=10**6, max_files=100)
    assert (dest / "ok/readme.txt").read_text() == "fine"
    assert not (tmp_path / "escaped.txt").exists()
    assert not (dest / "ok/link").exists()
    assert len(rep.rejected) == 4
    assert not list(tmp_path.rglob("escaped.txt"))


def test_evil_unitypackage(tmp_path):
    pkg = fixtures.build_evil_unitypackage(tmp_path / "evil.unitypackage")
    dest = tmp_path / "out"
    rep = archives.extract_unitypackage(pkg, dest, tmp_path / "prev", max_bytes=10**6, max_files=100)
    assert (dest / "Assets/Good/file.txt").read_text() == "good"
    assert not (dest / "Assets/Good/link").exists()
    assert not list(tmp_path.rglob("escaped.txt"))
    assert rep.rejected


def test_zip_bomb_limits(tmp_path):
    z = tmp_path / "big.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"\0" * 2_000_000)
    with pytest.raises(archives.ExtractLimit):
        archives.extract_zip(z, tmp_path / "o", max_bytes=1_000_000, max_files=10)
    with pytest.raises(archives.ExtractLimit):
        archives.extract_zip(z, tmp_path / "o2", max_bytes=10**9, max_files=0)


def test_verify_detects_corruption(tmp_path):
    z = fixtures.build_pack_zip(tmp_path / "POLYGON_FakeVillage_Unity_v1.zip")
    assert archives.verify_archive(z)[0]
    data = bytearray(z.read_bytes())
    data[len(data) // 2] ^= 0xFF
    bad = tmp_path / "bad.zip"
    bad.write_bytes(bytes(data))
    ok, msg = archives.verify_archive(bad)
    assert not ok and msg


def test_import_is_idempotent_and_keeps_original(svc):
    from conftest import _register_fake_pack

    _register_fake_pack(svc)
    z = fixtures.build_pack_zip(svc.settings.inbox_dir / "POLYGON_FakeVillage_Unity_2022_v1_0.zip")
    before = sha(z)
    a = library.import_archive(svc.settings, svc.db, z)
    assert a["status"] == "imported" and a["pack"] == "polygon-fakevillage" and a["kind"] == "unity"
    b = library.import_archive(svc.settings, svc.db, z)
    assert b["status"] == "already_imported"
    assert svc.db.one("SELECT COUNT(*) AS n FROM archives")["n"] == 1
    assert sha(z) == before
    row = svc.db.one("SELECT * FROM packs WHERE slug='polygon-fakevillage'")
    assert row["pack_id"] == "POLYGON_FakeVillage" and row["pack_id_confirmed"] == 1


def test_extract_and_index(indexed_pack):
    svc = indexed_pack
    pack = svc.db.one("SELECT * FROM packs WHERE slug='polygon-fakevillage'")
    assert pack["status"] == "indexed"
    ids = {r["asset_id"] for r in svc.db.q("SELECT asset_id FROM asset_files WHERE pack_slug='polygon-fakevillage'")}
    assert {p[0] for p in fixtures.PREFABS} <= ids
    assert pack["asset_count"] == len(ids)  # + the bundle model itself, as synty-inventory catalogs it
    kinds = {t["kind"] for t in svc.db.q("SELECT kind FROM trees WHERE pack_slug='polygon-fakevillage'")}
    assert kinds == {"unity", "source"}
    # Bundle prefab -> model + node; single prefab -> its own model; texture via .mat _BaseMap.
    f1 = svc.db.one("SELECT * FROM asset_files WHERE asset_id='SM_Bld_House_Medieval_02'")
    assert f1["mesh_path"].endswith("Models/Buildings.fbx")
    assert f1["mesh_node"] == "SM_Bld_House_Medieval_02"
    f2 = svc.db.one("SELECT * FROM asset_files WHERE asset_id='SM_Prop_Well_01'")
    assert f2["mesh_path"].endswith("SM_Prop_Well_01.fbx") and f2["mesh_node"] is None
    assert "PolygonFakeVillage_Texture_01_A.png" in f2["textures"]
    assert f2["preview_path"] and Path(f2["preview_path"]).is_file()
    # Re-extracting is a no-op.
    again = svc.extract_pack("polygon-fakevillage", wait=True)
    assert again["status"] == "done"
    assert all(e["status"] == "already_extracted" for e in again["result"]["extracted"])


def test_extract_refuses_corrupt_archive(svc):
    from conftest import _register_fake_pack

    _register_fake_pack(svc)
    z = fixtures.build_pack_zip(svc.settings.tmp_dir / "POLYGON_FakeVillage_Unity_v1.zip")
    data = bytearray(z.read_bytes())
    data[len(data) // 2] ^= 0xFF
    bad = svc.settings.inbox_dir / "POLYGON_FakeVillage_Unity_v1.zip"
    bad.write_bytes(bytes(data))
    res = library.import_archive(svc.settings, svc.db, bad)
    assert res["status"] == "imported_but_corrupt"
    with pytest.raises(ValueError, match="integrity"):
        library.extract_archive(svc.settings, svc.db, res["archive_id"])


def test_register_external_folder_read_only(svc, tmp_path):
    src = tmp_path / "external" / "Source_Files"
    (src / "Characters").mkdir(parents=True)
    (src / "Characters" / "SK_Character_Test.fbx").write_bytes(b"placeholder")
    (src / "MaterialList_POLYGONMetroCharacters.txt").write_text("")
    res = svc.scan_library(register_path=str(src), wait=True)
    assert res["status"] == "done"
    assert res["result"]["registered"]["pack"] == "polygon-metro-characters"
    assert res["result"]["registered"]["read_only"] is True
    # Folders outside source_roots are refused.
    with pytest.raises(Exception, match="outside allowed roots"):
        svc.scan_library(register_path=str(tmp_path), wait=True)


def test_inbox_sync_reports_store_unavailable(svc):
    res = svc.sync_owned_packs()
    assert res["store"]["available"] is False
    assert res["store"]["missing"]
    d = svc.download_pack("polygon-hamlet")
    assert d["status"] == "store_unavailable"
    assert "manual_import" in d
