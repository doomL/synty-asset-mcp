"""``synty-mcp`` command line: run the server (stdio / HTTP) and do library
chores without an agent (import, extract, scan, convert, tokens, store login)."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from pathlib import Path

from .config import load_settings


def _svc(args):
    from .service import Service

    return Service(load_settings(args.config))


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def cmd_serve(args) -> int:
    settings = load_settings(args.config, host=args.host, port=args.port)
    from .service import Service

    svc = Service(settings)
    if args.transport == "stdio":
        from .mcp_server import build_server

        build_server(svc).run("stdio")
        return 0
    import uvicorn

    from .http_app import build_app

    app = build_app(svc)
    print(f"synty-mcp: MCP at http://{settings.host}:{settings.port}/mcp  dashboard at /ui/", file=sys.stderr)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info", proxy_headers=True,
                forwarded_allow_ips="127.0.0.1")
    return 0


def cmd_token(args) -> int:
    tok = secrets.token_urlsafe(32)
    print(f"token:   {tok}")
    print(f"config:  sha256:{hashlib.sha256(tok.encode()).hexdigest()}")
    print("Put the 'config' form in tokens: (or SYNTY_MCP_TOKENS) and give the token to the agent.", file=sys.stderr)
    return 0


def cmd_import(args) -> int:
    from . import library

    svc = _svc(args)
    out = []
    for p in args.paths:
        res = library.import_archive(svc.settings, svc.db, Path(p), pack=args.pack, log=lambda *a: print(*a, file=sys.stderr))
        if args.extract and res.get("pack") and res["status"] in ("imported", "already_imported"):
            arc = res.get("archive_id") or res["archive"]["id"]
            res["extract"] = library.extract_archive(svc.settings, svc.db, arc, log=lambda *a: print(*a, file=sys.stderr))
        out.append(res)
    if args.extract:
        from . import inventory

        for slug in {r.get("pack") or (r.get("archive") or {}).get("pack_slug") for r in out} - {None}:
            out.append(inventory.scan_pack(svc.settings, svc.db, slug, log=lambda *a: print(*a, file=sys.stderr)))
    _print(out)
    return 0


def cmd_inbox(args) -> int:
    svc = _svc(args)
    _print(svc.sync_owned_packs())
    return 0


def cmd_extract(args) -> int:
    svc = _svc(args)
    _print(svc.extract_pack(args.pack, force=args.force, wait=True, timeout=24 * 3600))
    return 0


def cmd_scan(args) -> int:
    svc = _svc(args)
    _print(svc.scan_library(pack=args.pack, register_path=args.register, wait=True, timeout=24 * 3600))
    return 0


def cmd_status(args) -> int:
    _print(_svc(args).get_library_status())
    return 0


def cmd_packs(args) -> int:
    res = _svc(args).list_owned_packs()
    for p in res["packs"]:
        print(f"{p['status']:22} {p['indexed_assets'] or 0:>6}  {p['pack']:34} {p['name']}")
    print(json.dumps(res["by_status"]), file=sys.stderr)
    return 0


def cmd_search(args) -> int:
    svc = _svc(args)
    if args.scene:
        _print(svc.suggest_assets_for_scene(args.query))
    else:
        _print(svc.search_assets(args.query, limit=args.limit))
    return 0


def cmd_convert(args) -> int:
    svc = _svc(args)
    _print(svc.convert_asset_to_glb(args.ref, preset=args.preset, force=args.force, wait=True, timeout=3600))
    return 0


def cmd_store_login(args) -> int:
    from .store import SyntyStore

    settings = load_settings(args.config)
    settings.ensure_dirs()
    from .db import StateDB

    path = SyntyStore(settings, StateDB(settings.state_db)).login_interactive()
    print(f"session saved to {path} (mode 0600). It is never exposed to agents.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="synty-mcp", description=__doc__)
    ap.add_argument("--config", help="YAML config (default: $SYNTY_MCP_CONFIG)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the MCP server")
    s.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("token", help="generate an access token for HTTP mode")
    s.set_defaults(fn=cmd_token)

    s = sub.add_parser("import", help="import .zip/.unitypackage archives")
    s.add_argument("paths", nargs="+")
    s.add_argument("--pack")
    s.add_argument("--no-extract", dest="extract", action="store_false")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("inbox", help="import the inbox folder (and list store purchases when configured)")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("extract", help="extract a pack's archives and index it")
    s.add_argument("pack")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_extract)

    s = sub.add_parser("scan", help="index extracted packs with synty-inventory")
    s.add_argument("pack", nargs="?")
    s.add_argument("--register", help="use an existing extracted folder in place (must be under source_roots)")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("status")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("packs", help="owned packs and their status")
    s.set_defaults(fn=cmd_packs)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--scene", action="store_true", help="multi-item request (suggest_assets_for_scene)")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("convert")
    s.add_argument("ref")
    s.add_argument("--preset", default="threejs_web")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_convert)

    s = sub.add_parser("store-login", help="log in to the Synty Store in a real browser and save the session")
    s.set_defaults(fn=cmd_store_login)

    args = ap.parse_args(argv)
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
