"""Unity prefab parsing and unit-scale heuristics."""

from __future__ import annotations

from pathlib import Path

from synty_mcp import unity
from synty_mcp.convert import plausible_min_m

MODEL_GUID = "4a5a8c8ff5d6d2e48ab8d2b27100cf57"
MAT_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MAT_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _character(go: int, smr: int, name: str, active: int, mesh_id: int, mat: str) -> str:
    return (
        f"--- !u!1 &{go}\nGameObject:\n  m_Component:\n  - component: {{fileID: {smr}}}\n"
        f"  m_Name: {name}\n  m_IsActive: {active}\n"
        f"--- !u!137 &{smr}\nSkinnedMeshRenderer:\n  m_GameObject: {{fileID: {go}}}\n  m_Enabled: 1\n"
        f"  m_Materials:\n  - {{fileID: 2100000, guid: {mat}, type: 2}}\n"
        f"  m_Mesh: {{fileID: {mesh_id}, guid: {MODEL_GUID}, type: 3}}\n"
    )


def test_prefab_model_picks_the_active_character(tmp_path: Path):
    # Synty character prefabs embed every character of Characters.fbx, all but one switched off.
    model = tmp_path / "Characters.fbx"
    model.write_bytes(b"")
    (tmp_path / "Characters.fbx.meta").write_text(
        "fileIDToRecycleName:\n    4300010: Character_Grandma\n    4300020: Character_Biker\n"
    )
    for g in (MAT_A, MAT_B):
        (tmp_path / f"{g}.mat").write_text("")
    prefab = tmp_path / "SM_Chr_Biker_01.prefab"
    prefab.write_text(
        "%YAML 1.1\n"
        + _character(100, 200, "Character_Grandma", 0, 4300010, MAT_A)
        + _character(101, 201, "Character_Biker", 1, 4300020, MAT_B)
    )
    guids = {MODEL_GUID: model, MAT_A: tmp_path / f"{MAT_A}.mat", MAT_B: tmp_path / f"{MAT_B}.mat"}
    path, node = unity.prefab_model(prefab, guids)
    assert path == model
    assert node == "Character_Biker"
    assert [p.stem for p in unity.prefab_material_paths(prefab, guids)] == [MAT_B]


def test_plausible_min_keeps_tiny_items_tiny():
    assert plausible_min_m("prop", "SM_Prop_Barrel_01") == 0.02
    assert plausible_min_m("prop", "SM_Prop_Coin_01") < 0.01
    assert plausible_min_m("prop", "SM_Prop_CoinPile_02") < 0.01
    assert plausible_min_m("prop", "SM_Prop_Trash_01") == 0.02
    assert plausible_min_m("character", "SK_Chr_Monkey_01") == 0.2
