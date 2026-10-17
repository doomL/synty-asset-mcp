"""MCP tool surface (real MCP client sessions), HTTP auth and file serving,
SyntyDex provider behaviour, job deduplication."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

REQUIRED = {
    "list_owned_packs", "sync_owned_packs", "download_pack", "extract_pack", "scan_library", "get_library_status",
    "search_assets", "get_asset_details", "find_similar_assets", "suggest_assets_for_scene", "list_pack_assets",
    "get_asset_preview", "get_asset_file", "convert_asset_to_glb", "prepare_asset_for_godot", "optimize_asset",
    "get_asset_dependencies", "prepare_asset_collection", "search_syntydex", "match_syntydex_asset_to_local",
}


def _payload(res):
    sc = res.structured_content
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


def test_mcp_in_process_tools(indexed_pack):
    from mcp.client import Client

    from synty_mcp.mcp_server import build_server

    server = build_server(indexed_pack)

    async def go():
        async with Client(server) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            assert REQUIRED <= tools
            res = await c.call_tool("search_assets", {"query": "pozzo"})
            data = _payload(res)
            assert data["results"][0]["id"] == "SM_Prop_Well_01"
            res = await c.call_tool("get_asset_details", {"ref": "nope/nothing"})
            assert res.is_error and "not found" in res.content[0].text
            res = await c.call_tool("search_syntydex", {"query": "house"})
            assert _payload(res)["available"] is False

    asyncio.run(go())


def test_mcp_stdio_subprocess(tmp_path):
    """Spawn `python -m synty_mcp serve` and talk MCP over stdio, like Claude Code / Codex do."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    cfg = tmp_path / "cfg.yaml"

    example = str(Path(__file__).parent / "data" / "test_packs.yaml")
    cfg.write_text(yaml.safe_dump({"data_dir": str(tmp_path / "data"), "owned_packs_file": example}))
    params = StdioServerParameters(command=sys.executable, args=["-m", "synty_mcp", "--config", str(cfg), "serve"])

    async def go():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            assert REQUIRED <= tools
            data = _payload(await s.call_tool("list_owned_packs", {}))
            assert data["owned"] == 12
            data = _payload(await s.call_tool("search_assets", {"query": "tre case medievali"}))
            assert data["results"] == [] and data["owned_unindexed_candidates"]

    asyncio.run(asyncio.wait_for(go(), 60))


@pytest.fixture
def http(svc):
    from synty_mcp.http_app import build_app

    svc.settings.tokens = ["secret-token"]
    svc.settings.port = 8765
    with TestClient(build_app(svc), base_url="http://127.0.0.1:8765") as client:
        yield client


def test_http_requires_token(http):
    assert http.get("/healthz").status_code == 200
    assert http.get("/ui/").status_code == 200
    assert http.get("/api/status").status_code == 401
    assert http.post("/mcp", json={}).status_code == 401
    assert http.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = http.get("/api/status", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200 and ok.json()["packs_owned"] == 12


def test_http_cookie_login(http):
    assert http.post("/api/login", json={"token": "nope"}).status_code == 401
    r = http.post("/api/login", json={"token": "secret-token"})
    assert r.status_code == 200
    assert http.get("/api/packs").status_code == 200  # cookie now set on the client


def test_http_rejects_foreign_host(http):
    r = http.post("/mcp", headers={"Authorization": "Bearer secret-token", "Host": "evil.example"},
                  json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code in (400, 421)


def test_http_files_confined(http, svc):
    (svc.settings.converted_dir / "a.glb").write_bytes(b"glb")
    (svc.settings.data_dir / "state_copy.txt").write_text("private")
    h = {"Authorization": "Bearer secret-token"}
    assert http.get("/files/converted/a.glb", headers=h).content == b"glb"
    assert http.get("/files/converted/a.glb").status_code == 401
    for bad in ("/files/converted/../state_copy.txt", "/files/converted/%2e%2e/state_copy.txt",
                "/files/secrets/x", "/files/converted/..%2F..%2Fstate.sqlite3"):
        assert http.get(bad, headers=h).status_code == 404, bad


def test_http_refuses_to_start_without_tokens(svc):
    from synty_mcp.http_app import build_app

    svc.settings.tokens = []
    svc.settings.host = "0.0.0.0"
    with pytest.raises(SystemExit):
        build_app(svc)


def test_hashed_tokens(svc):
    import hashlib

    from synty_mcp.http_app import build_app

    svc.settings.tokens = ["sha256:" + hashlib.sha256(b"abc").hexdigest()]
    c = TestClient(build_app(svc), base_url="http://127.0.0.1:8765")
    assert c.get("/api/status", headers={"Authorization": "Bearer abc"}).status_code == 200


def test_syntydex_local_export(indexed_pack, tmp_path):
    svc = indexed_pack
    st = svc.search_syntydex("house")
    assert st["available"] is False and st["results"] == [] and "disabled" in st["reason"]
    export = tmp_path / "dex.txt"
    export.write_text("SM_Bld_House_Medieval_01\tPOLYGON Fake Village\nSM_Prop_Barrel_01\n")
    svc.settings.syntydex_enabled = True
    svc.settings.syntydex_export_file = export
    res = svc.search_syntydex("house medieval")
    assert res["available"] is True and res["source"].startswith("user-provided")
    assert res["results"][0]["name"] == "SM_Bld_House_Medieval_01"
    assert res["results"][0]["local"][0]["ref"] == "POLYGON_FakeVillage/SM_Bld_House_Medieval_01"
    m = svc.match_syntydex_asset_to_local("SM_Prop_Barrel_01")
    assert m["matches"] == [] and m["note"]
    m = svc.match_syntydex_asset_to_local("Prop Chair")
    assert {x["id"] for x in m["matches"]} == {"SM_Prop_Chair_01", "SM_Prop_Chair_02"}


def test_job_dedupe(svc):
    started = threading.Event()
    release = threading.Event()

    def slow(ctx):
        started.set()
        release.wait(5)
        return {"ok": True}

    a = svc.jobs.submit("t", {}, slow, dedupe_key="same")
    started.wait(5)
    b = svc.jobs.submit("t", {}, slow, dedupe_key="same")
    assert b["job_id"] == a["job_id"] and b.get("deduplicated")
    release.set()
    done = svc.jobs.wait(a["job_id"], 5)
    assert done["status"] == "done" and done["result"] == {"ok": True}
    c = svc.jobs.submit("t", {}, lambda ctx: 1, dedupe_key="same")
    assert c["job_id"] != a["job_id"]


def test_job_failure_is_recorded(svc):
    def boom(ctx):
        ctx.log("about to fail")
        raise ValueError("bad input")

    j = svc.jobs.submit("t", {}, boom)
    j = svc.jobs.wait(j["job_id"], 5)
    time.sleep(0.05)
    j = svc.get_job(j["job_id"], with_log=True)
    assert j["status"] == "failed" and "bad input" in j["error"] and "about to fail" in j["log"]


def test_conversion_without_source_is_explicit(indexed_pack):
    """Conversion errors are reported, never a fake output."""
    from synty_mcp.service import ServiceError

    svc = indexed_pack
    svc.settings.blender = "/nonexistent/blender"
    res = svc.convert_asset_to_glb("SM_Prop_Well_01", wait=True, timeout=30)
    assert res["status"] == "failed"
    assert "Blender not found" in res["error"] or "Blender" in res["error"]
    with pytest.raises(ServiceError):
        svc.get_asset_file("SM_Unknown_99")


def test_preview_not_sent_inline_by_default(indexed_pack):
    p = indexed_pack.get_asset_preview("SM_Prop_Well_01")
    assert p["available"] and p["source"] == "unity_package_thumbnail"
    assert p["image_inline"] is False and "not sent" in p["note"]


# (readOnlyHint, destructiveHint, idempotentHint, openWorldHint) per tool, matching what each handler does.
EXPECTED_HINTS = {
    "list_owned_packs": (True, False, True, False),
    "get_library_status": (True, False, True, False),
    "search_assets": (True, False, True, False),
    "get_asset_details": (True, False, True, False),
    "find_similar_assets": (True, False, True, False),
    "suggest_assets_for_scene": (True, False, True, False),
    "list_pack_assets": (True, False, True, False),
    "get_asset_dependencies": (True, False, True, False),
    "search_syntydex": (True, False, True, False),
    "match_syntydex_asset_to_local": (True, False, True, False),
    "get_job": (True, False, True, False),
    "list_jobs": (True, False, True, False),
    "import_archive": (False, False, True, False),
    "scan_library": (False, False, True, False),
    "get_asset_preview": (False, False, True, False),
    "get_asset_file": (False, False, True, False),
    "convert_asset_to_glb": (False, False, True, False),
    "optimize_asset": (False, False, True, False),
    "extract_pack": (False, True, True, False),
    "prepare_asset_for_godot": (False, True, True, False),
    "prepare_asset_collection": (False, True, True, False),
    "sync_owned_packs": (False, False, True, True),
    "download_pack": (False, False, True, True),
}


def test_every_tool_has_all_four_explicit_hints(indexed_pack):
    from mcp.client import Client

    from synty_mcp.mcp_server import build_server

    async def go():
        async with Client(build_server(indexed_pack)) as c:
            return (await c.list_tools()).tools

    tools = asyncio.run(go())
    assert {t.name for t in tools} == set(EXPECTED_HINTS)
    assert len(tools) == 23
    for t in tools:
        ann = t.annotations
        assert ann is not None, t.name
        got = (ann.read_only_hint, ann.destructive_hint, ann.idempotent_hint, ann.open_world_hint)
        assert all(isinstance(v, bool) for v in got), (t.name, got)
        assert got == EXPECTED_HINTS[t.name], (t.name, got)
