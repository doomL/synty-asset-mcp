#!/usr/bin/env python3
"""Real MCP round-trip against a running server (HTTP) or a spawned one (stdio).

    python scripts/mcp_smoke.py http http://127.0.0.1:8765/mcp --token $TOKEN
    python scripts/mcp_smoke.py stdio --config config.yaml

Lists the tools, then calls the read-only ones plus one search, one scene
request and (with --convert REF) a conversion + collection. Prints a compact
pass/fail table; exit code 1 if any call errored unexpectedly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mcp.client.session import ClientSession

REQUIRED = {
    "list_owned_packs", "sync_owned_packs", "download_pack", "extract_pack", "scan_library", "get_library_status",
    "search_assets", "get_asset_details", "find_similar_assets", "suggest_assets_for_scene", "list_pack_assets",
    "get_asset_preview", "get_asset_file", "convert_asset_to_glb", "prepare_asset_for_godot", "optimize_asset",
    "get_asset_dependencies", "prepare_asset_collection", "search_syntydex", "match_syntydex_asset_to_local",
}


def payload(res) -> dict | list | str:
    if getattr(res, "structured_content", None) is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
    try:
        return json.loads(texts[0]) if texts else {}
    except json.JSONDecodeError:
        return texts[0]


async def run(session: ClientSession, args) -> int:
    await session.initialize()
    tools = {t.name for t in (await session.list_tools()).tools}
    missing = REQUIRED - tools
    print(f"tools: {len(tools)} exposed; required missing: {sorted(missing) or 'none'}")
    calls: list[tuple[str, dict]] = [
        ("get_library_status", {}),
        ("list_owned_packs", {}),
        ("sync_owned_packs", {}),
        ("search_assets", {"query": args.query, "limit": 5}),
        ("suggest_assets_for_scene", {"request": args.scene}),
        ("search_syntydex", {"query": "house"}),
        ("match_syntydex_asset_to_local", {"name": args.ref or "SM_Prop_Chair_01"}),
        ("download_pack", {"pack": "polygon-city"}),
        ("list_jobs", {}),
    ]
    if args.ref:
        calls += [
            ("get_asset_details", {"ref": args.ref}),
            ("find_similar_assets", {"ref": args.ref, "limit": 3}),
            ("get_asset_dependencies", {"ref": args.ref}),
            ("list_pack_assets", {"pack": args.ref.split("/")[0], "limit": 3}),
            ("get_asset_preview", {"ref": args.ref}),
        ]
    if args.convert and args.ref:
        calls += [
            ("convert_asset_to_glb", {"ref": args.ref, "preset": "threejs_web"}),
            ("get_asset_file", {"ref": args.ref, "format": "glb"}),
            ("optimize_asset", {"ref": args.ref, "preset": "threejs_mobile"}),
            ("prepare_asset_for_godot", {"ref": args.ref}),
            ("prepare_asset_collection", {"refs": [args.ref], "name": "smoke_test"}),
            ("get_asset_preview", {"ref": args.ref, "generate": True}),
        ]
    failures = 0
    for name, params in calls:
        res = await session.call_tool(name, params)
        data = payload(res)
        status = "ERROR" if res.is_error else "ok"
        if res.is_error:
            failures += 1
        summary = json.dumps(data, ensure_ascii=False, default=str)
        print(f"{status:5} {name:32} {summary[:args.width]}")
    if missing:
        failures += 1
    return 1 if failures else 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transport", choices=["http", "stdio"])
    ap.add_argument("url", nargs="?", default="http://127.0.0.1:8765/mcp")
    ap.add_argument("--token")
    ap.add_argument("--config")
    ap.add_argument("--query", default="sedia di legno")
    ap.add_argument("--scene", default="tre case medievali, un pozzo, due alberi, cinque sedie e un personaggio goblin")
    ap.add_argument("--ref", help="PACK/ASSET to exercise asset-level tools")
    ap.add_argument("--convert", action="store_true")
    ap.add_argument("--width", type=int, default=220)
    args = ap.parse_args()
    if args.transport == "http":
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        client = create_mcp_http_client(headers={"Authorization": f"Bearer {args.token}"} if args.token else None)
        async with client, streamable_http_client(args.url, http_client=client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as s:
                return await run(s, args)
    from mcp.client.stdio import StdioServerParameters, stdio_client

    cmd = [sys.executable, "-m", "synty_mcp"] + (["--config", args.config] if args.config else []) + ["serve"]
    params = StdioServerParameters(command=cmd[0], args=cmd[1:])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            return await run(s, args)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
