"""One ASGI app for remote use: MCP Streamable HTTP at /mcp, the dashboard's
REST API at /api, authenticated file downloads at /files, the static
dashboard at /ui. Everything except /healthz and the static dashboard shell
requires a bearer token (or the HttpOnly cookie set by /api/login)."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .convert import ConversionError
from .mcp_server import build_server
from .safepaths import PathViolation, confine
from .service import Service, ServiceError
from .store import StoreUnavailable

COOKIE = "synty_mcp_session"
PUBLIC_PREFIXES = ("/healthz", "/ui", "/favicon.ico")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenCheck:
    def __init__(self, tokens: list[str]):
        self.digests = [t.split(":", 1)[1].lower() if t.startswith("sha256:") else _digest(t) for t in tokens if t]

    def ok(self, token: str | None) -> bool:
        if not token or not self.digests:
            return False
        d = _digest(token)
        return any(hmac.compare_digest(d, x) for x in self.digests)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class AuthMiddleware:
    """Pure ASGI (does not buffer streamed MCP / file responses)."""

    def __init__(self, app, check: TokenCheck, allow_anonymous: bool):
        self.app = app
        self.check = check
        self.allow_anonymous = allow_anonymous

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path == "/" or path.startswith(PUBLIC_PREFIXES) or path == "/api/login":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        token = None
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        if token is None and "cookie" in headers:
            for part in headers["cookie"].split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    token = v
        if self.allow_anonymous or self.check.ok(token):
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized", "hint": "send Authorization: Bearer <token>"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b'Bearer realm="synty-mcp"')]})
        await send({"type": "http.response.body", "body": body})


def _json(fn):
    def endpoint(request: Request):
        try:
            return JSONResponse(fn(request))
        except (ServiceError, ConversionError, StoreUnavailable, PathViolation, ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    return endpoint


async def _body(request: Request) -> dict:
    try:
        return await request.json()
    except json.JSONDecodeError:
        return {}


def build_app(svc: Service):
    st = svc.settings
    svc.serving_http = True
    check = TokenCheck(st.tokens)
    anonymous = not check.digests
    if anonymous and not (st.allow_unauthenticated_localhost and _is_loopback(st.host)):
        raise SystemExit("refusing to serve HTTP without tokens: set SYNTY_MCP_TOKENS (or tokens: in the config). "
                         "Unauthenticated mode is only allowed on a loopback host with allow_unauthenticated_localhost.")
    hosts = list(st.allowed_hosts) or [f"127.0.0.1:{st.port}", f"localhost:{st.port}", "127.0.0.1", "localhost"]
    if st.public_base_url:
        netloc = urlparse(st.public_base_url).netloc
        hosts += [netloc, netloc.split(":")[0]]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(set(hosts)),
                                         allowed_origins=[])
    mcp = build_server(svc)
    app = mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=security, host=st.host)

    roots = {"converted": st.converted_dir, "collections": st.collections_dir, "previews": st.previews_dir,
             "library": st.library_dir}

    def files(request: Request):
        root = roots.get(request.path_params["root"])
        if root is None:
            return Response(status_code=404)
        try:
            p = confine(root / request.path_params["path"], [root], must_exist=True)
        except (PathViolation, FileNotFoundError):
            return Response(status_code=404)
        if not p.is_file():
            return Response(status_code=404)
        return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})

    async def login(request: Request):
        token = (await _body(request)).get("token") or ""
        if not check.ok(token):
            return JSONResponse({"ok": False}, status_code=401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                        secure=(st.public_base_url or "").startswith("https"), max_age=7 * 86400)
        return resp

    async def logout(request: Request):
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE)
        return resp

    q = lambda r, k, d=None: r.query_params.get(k, d)  # noqa: E731

    def as_bool(v) -> bool:
        return str(v).lower() in ("1", "true", "yes", "on")

    async def collection(request: Request):
        b = await _body(request)
        try:
            res = svc.prepare_asset_collection(b.get("refs") or [], name=b.get("name") or "collection",
                                               preset=b.get("preset") or "threejs_web",
                                               engine=b.get("engine") or "threejs", wait=False)
        except (ServiceError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(res)

    async def convert(request: Request):
        b = await _body(request)
        try:
            res = svc.convert_asset_to_glb(b["ref"], preset=b.get("preset") or "threejs_web", wait=False)
        except (ServiceError, ConversionError, ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(res)

    api = [
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/status", _json(lambda r: svc.get_library_status())),
        Route("/api/packs", _json(lambda r: svc.list_owned_packs())),
        Route("/api/search", _json(lambda r: svc.search_assets(
            q(r, "q", ""), pack=q(r, "pack") or None, asset_class=q(r, "class") or None,
            available_only=as_bool(q(r, "available", "0")), include_nonplaceable=as_bool(q(r, "all", "0")),
            limit=int(q(r, "limit", "40"))))),
        Route("/api/scene", _json(lambda r: svc.suggest_assets_for_scene(q(r, "q", "")))),
        Route("/api/pack/{pack}", _json(lambda r: svc.list_pack_assets(
            r.path_params["pack"], asset_class=q(r, "class") or None, query=q(r, "q") or None,
            offset=int(q(r, "offset", "0")), limit=int(q(r, "limit", "100"))))),
        Route("/api/asset", _json(lambda r: svc.get_asset_details(q(r, "ref", "")))),
        Route("/api/similar", _json(lambda r: svc.find_similar_assets(q(r, "ref", "")))),
        Route("/api/deps", _json(lambda r: svc.get_asset_dependencies(q(r, "ref", "")))),
        Route("/api/preview", _json(lambda r: svc.get_asset_preview(q(r, "ref", ""),
                                                                    generate=as_bool(q(r, "generate", "0"))))),
        Route("/api/jobs", _json(lambda r: svc.list_jobs(status=q(r, "status") or None))),
        Route("/api/job/{job_id}", _json(lambda r: svc.get_job(r.path_params["job_id"], with_log=True))),
        Route("/api/convert", convert, methods=["POST"]),
        Route("/api/collection", collection, methods=["POST"]),
        Route("/files/{root}/{path:path}", files),
        Route("/healthz", lambda r: JSONResponse({"ok": True})),
        Route("/", lambda r: RedirectResponse("/ui/")),
    ]
    web_dir = Path(str(resources.files("synty_mcp").joinpath("web")))
    app.router.routes.extend(api)
    app.router.routes.append(Mount("/ui", app=StaticFiles(directory=web_dir, html=True), name="ui"))
    return AuthMiddleware(app, check, anonymous)
