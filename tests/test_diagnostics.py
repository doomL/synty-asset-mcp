from pathlib import Path

from synty_mcp import unity
from synty_mcp.diagnostics import export_content


def test_uninspected_character_is_not_claimed_to_have_or_lack_clips():
    content = export_content(None)
    assert content["verified"] is False
    assert content["animation_clips"] is None
    assert content["skinned"] is None


def test_skin_and_animation_presence_are_independent_and_export_options_are_visible():
    rig_only = export_content({"skins": 1, "animations": []}, {"animations": True})
    assert rig_only["skinned"] and rig_only["animation_count"] == 0
    assert rig_only["animations_exported"] is True
    disabled = export_content({"skins": 1, "animations": []}, {"animations": False})
    assert disabled["animations_exported"] is False
    standalone_clip = export_content({"skins": 0, "animations": ["Walk"]}, {"animations": True})
    assert not standalone_clip["skinned"] and standalone_clip["animation_clips"] == ["Walk"]


def test_per_mesh_slots_distinguish_declared_missing_from_explicitly_untextured(tmp_path):
    source = tmp_path / "MaterialList_Synthetic.txt"
    source.write_text(
        "Prefab Name: SM_Test_01\n"
        "    Mesh Name: Painted\n        Slot: Shared\n            Albedo: Atlas_A.png (_Albedo_Map)\n"
        "    Mesh Name: Flat\n        Slot: Shared\n            No textures assigned\n"
        "    Mesh Name: Broken\n        Slot: Shared\n            Albedo: Missing.png (_Albedo_Map)\n"
    )
    meshes = unity.parse_material_list_meshes(source)["SM_Test_01"]
    textures = {"atlas_a": Path("/synthetic/Atlas_A.png")}
    painted = unity.resolve_material_slots(meshes["Painted"], textures)
    flat = unity.resolve_material_slots(meshes["Flat"], textures)
    broken = unity.resolve_material_slots(meshes["Broken"], textures)
    assert painted["slots"] == ["/synthetic/Atlas_A.png"]
    assert flat["slots"] == broken["slots"] == [None]
    assert flat["slot_states"] == ["untextured"]
    assert broken["slot_states"] == ["missing"]
    assert broken["expected_textures"] == ["Missing"]


def test_empty_slot_without_explicit_intent_is_unknown():
    bindings = unity.resolve_material_slots([{"material": "FlatOrMissing", "texture": ""}], {})
    assert bindings["slot_states"] == ["unknown"]


def test_asset_details_without_conversion_retains_unknown_content(indexed_pack):
    details = indexed_pack.get_asset_details("SK_Chr_Goblin_01")
    assert details["content"]["verified"] is False
    assert details["content"]["animation_count"] is None
    assert details["provenance"] is None
