"""Bilingual parsing, ranking, scene requests, no-invention guarantees."""

from __future__ import annotations

from pathlib import Path

import pytest

from synty_mcp import lexicon, unity


@pytest.mark.parametrize("text,qty,concept,mods", [
    ("tre case medievali", 3, "house", ["medieval"]),
    ("un pozzo", 1, "well", []),
    ("due alberi", 2, "tree", []),
    ("cinque sedie", 5, "chair", []),
    ("un personaggio goblin", 1, "goblin", []),
    ("three medieval houses", 3, "house", ["medieval"]),
    ("2 wooden chairs", 2, "chair", ["wood"]),
    ("a goblin character", 1, "goblin", []),
    ("alcune casse di legno", 3, "crate", ["wood"]),
    ("un falò", 1, "campfire", []),
])
def test_analyse_phrase(text, qty, concept, mods):
    a = lexicon.analyse_phrase(text)
    assert a["qty"] == qty
    assert a["concept"] == concept
    assert a["modifiers"] == mods


def test_goblin_character_wants_character():
    a = lexicon.analyse_phrase("un personaggio goblin")
    assert a["wants_character"] and "character" in a["qualifiers"]


def test_split_full_request():
    items = lexicon.parse_request("Mi servono tre case medievali, un pozzo, due alberi, cinque sedie e un personaggio goblin.")
    assert [(i["qty"], i["concept"]) for i in items] == [(3, "house"), (1, "well"), (2, "tree"), (5, "chair"), (1, "goblin")]


def test_scene_phrase_ignores_engine_words():
    items = lexicon.parse_request("Crea una piccola città fantasy in Three.js con case, negozi, alberi e NPC")
    concepts = [i["concept"] for i in items]
    assert concepts == ["house", "shop", "tree", "npc"]
    assert all(i["qty"] != 3 for i in items)  # "three" from Three.js is not a quantity


def test_material_list_parser(tmp_path):
    ml = tmp_path / "MaterialList_X.txt"
    ml.write_text(
        "Prefab Name: Character_Alpha_01\n    Mesh Name: Character_Alpha\n"
        "        Slot: Mat_01_A (Texture_01_A)\n    Mesh Name: Character_Beta\n        Slot: Mat_02 (Texture_02)\n"
        "Prefab Name: SM_Prop_Well_01\n    Mesh Name: SM_Prop_Well_01\n        Slot: Mat_03 (Texture_03)\n"
        "        Slot: Mat_04 (Texture_04)\n")
    parsed = unity.parse_material_list(ml)
    assert parsed["Character_Alpha"] == [{"material": "Mat_01_A", "texture": "Texture_01_A"}]
    assert parsed["Character_Beta"] == [{"material": "Mat_02", "texture": "Texture_02"}]
    assert [s["texture"] for s in parsed["SM_Prop_Well_01"]] == ["Texture_03", "Texture_04"]
    assert unity.lookup_material_list(parsed, "SK_Character_Alpha")[0]["texture"] == "Texture_01_A"


def test_search_finds_house_not_chair(indexed_pack):
    res = indexed_pack.search_assets("casa medievale", limit=5)
    ids = [r["id"] for r in res["results"]]
    assert ids[:2] == ["SM_Bld_House_Medieval_01", "SM_Bld_House_Medieval_02"] or set(ids[:2]) == {
        "SM_Bld_House_Medieval_01", "SM_Bld_House_Medieval_02"}
    assert "SM_Prop_Chair_01" not in ids
    r = res["results"][0]
    assert r["ref"].startswith("POLYGON_FakeVillage/")
    assert r["available"]["source_file"] is True
    assert r["class"] in ("prefab", "model")


def test_search_english_equivalent(indexed_pack):
    it = {r["id"] for r in indexed_pack.search_assets("sedia", limit=5)["results"]}
    en = {r["id"] for r in indexed_pack.search_assets("chair", limit=5)["results"]}
    assert it == en == {"SM_Prop_Chair_01", "SM_Prop_Chair_02"}


def test_character_request_does_not_return_props(indexed_pack):
    res = indexed_pack.search_assets("personaggio goblin", limit=5)
    assert [r["id"] for r in res["results"]][:1] == ["SK_Chr_Goblin_01"]
    assert all(r["class"] == "character" for r in res["results"])


def test_scene_suggestion_quantities_and_missing(indexed_pack):
    res = indexed_pack.suggest_assets_for_scene(
        "tre case medievali, un pozzo, due alberi, cinque sedie e un personaggio goblin e un drago")
    by = {i["understood"]["concept"]: i for i in res["items"]}
    assert by["house"]["status"] == "partial"  # only 2 house prefabs exist: no padding with fakes
    assert len(by["house"]["selected"]) == 2 and "note" in by["house"]
    assert by["well"]["status"] == "found"
    assert by["tree"]["status"] == "partial"
    assert by["chair"]["status"] == "partial" and len(by["chair"]["selected"]) == 2
    assert by["goblin"]["selected"][0]["id"] == "SK_Chr_Goblin_01"
    # "drago" is not in the lexicon nor in the catalog: reported missing, never invented
    missing = [i for i in res["items"] if i["status"] == "not_found"]
    assert missing and all(not i["selected"] for i in missing)
    assert res["packs_used"] == {"POLYGON_FakeVillage": sum(len(i["selected"]) for i in res["items"])}


def test_unknown_asset_returns_nothing(indexed_pack):
    res = indexed_pack.search_assets("astronave aliena")
    assert res["results"] == []
    assert "nothing is invented" in res["note"]
    # owned but unindexed packs are suggested, flagged as unverified
    cands = res["owned_unindexed_candidates"]
    assert cands and all(c["verified"] is False for c in cands)
    assert cands[0]["pack"] == "polygon-starport"


def test_details_similar_dependencies(indexed_pack):
    svc = indexed_pack
    d = svc.get_asset_details("POLYGON_FakeVillage/SM_Bld_House_Medieval_01")
    assert d["files"]["node_in_model"] == "SM_Bld_House_Medieval_01"
    assert d["record"]["id"] == "SM_Bld_House_Medieval_01"
    sim = svc.find_similar_assets("SM_Prop_Chair_01")
    assert sim["similar"][0]["id"] == "SM_Prop_Chair_02"
    deps = svc.get_asset_dependencies("SM_Prop_Well_01")
    assert any(Path(t).name == "PolygonFakeVillage_Texture_01_A.png" for t in deps["textures_by_slot"] if t)
    lp = svc.list_pack_assets("polygon-fakevillage", query="chair")
    assert lp["total"] == 2


def test_list_unindexed_pack_says_so(svc):
    res = svc.list_pack_assets("polygon-hamlet")
    assert res["assets"] == [] and res["status"] == "owned_not_downloaded"


def test_ambiguous_and_missing_refs(indexed_pack):
    from synty_mcp.service import ServiceError

    with pytest.raises(ServiceError, match="not found"):
        indexed_pack.get_asset_details("SM_Does_Not_Exist")
