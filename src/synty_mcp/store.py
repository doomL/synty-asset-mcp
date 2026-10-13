"""Synty Store purchase sync (optional, owner-operated).

Verified 2026-10-13 (docs/INTEGRATIONS.md): Synty Store runs on Shopify with
the Sky Pilot digital-downloads app. Neither publishes a customer download
API, so — in the brief's order of preference — this module implements:

3. browser automation with Playwright after a **manual** login by the owner
   (``synty-mcp store-login``), and
4. manual import of archives (``library.import_archive`` / inbox) as the
   always-available fallback.

The exact page structure of "My Downloads" could not be verified without an
authenticated session, so the downloads page URL and link selector are
configuration, not hardcoded guesses. Until a real run is recorded, the
status reports ``verified: false``.

Security: the browser storage state and the resolved download links are kept
in ``secrets_dir`` (mode 0600, never served). Agents only see titles, pack
matches and opaque ``link_ref`` ids.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import packs
from .db import now

STATE_FILE = "synty_store_state.json"
LINKS_FILE = "synty_store_links.json"
LOGIN_URL = "https://syntystore.com/account/login"
# httpx logs every request URL at INFO; store URLs carry the account e-mail and signed tokens.
import logging as _logging  # noqa: E402

for _name in ("httpx", "httpcore"):
    _logging.getLogger(_name).setLevel(_logging.WARNING)
# Sky Pilot customer library (verified 2026-10-13): paginated list of purchased packs, each linking to
# /apps/downloads/customers/<id>/orders/<id>/order_items/<id> with one "Download" link per file.
LIBRARY_URL = "https://syntystore.com/apps/downloads/orders"
DOWNLOAD_SEL = "a[href*='/apps/downloads/downloads/']"
_SIZE = re.compile(r"^\(?\s*([\d.,]+)\s*(B|KB|MB|GB)\s*\)?$", re.I)
_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def parse_file_blocks(text: str) -> list[dict]:
    """Split a pack page's text into one {name, variant, size} per "Download" button, in page order.
    Shape:  NAME / [VARIANT | VERSION] / (SIZE) / Download"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out, buf = [], []
    for ln in lines:
        if ln.lower() == "download":
            size = None
            if buf and _SIZE.match(buf[-1]):
                m = _SIZE.match(buf.pop())
                size = int(float(m.group(1).replace(",", ".")) * _UNITS[m.group(2).upper()])
            variant = buf.pop() if len(buf) >= 2 and "|" in buf[-1] else ""
            name = buf[-1] if buf else ""
            out.append({"name": name, "variant": variant, "size": size})
            buf = []
        else:
            buf.append(ln)
    return out


def file_kind(name: str, variant: str) -> str:
    v = (variant or "").lower()
    if re.search(r"\.(png|jpe?g|pdf|txt|gif)$", name.lower()):
        return "extra"
    if "source" in v:
        return "source"
    if "unreal" in v or v.startswith("ue"):
        return "unreal"
    if "godot" in v:
        return "godot"
    if "unity" in v:
        return "unity"
    return packs.guess_kind(name + "_" + variant)



class StoreUnavailable(RuntimeError):
    pass


def _write_secret(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def _read_secret(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class SyntyStore:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.state_path = settings.secrets_dir / STATE_FILE
        self.links_path = settings.secrets_dir / LINKS_FILE
        self.hints_path = settings.secrets_dir / "synty_store_hints.json"

    # -- browser profile ------------------------------------------------------
    @property
    def profile_dir(self) -> Path:
        """Dedicated, persistent browser profile (never the owner's personal Chrome profile)."""
        return self.settings.secrets_dir / "browser-profile"

    def has_session(self) -> bool:
        return (self.profile_dir / "Default" / "Cookies").is_file() or self.state_path.is_file()

    def _launch(self, pw, *, headless: bool):
        """Google Chrome when installed (falls back to Playwright's Chromium), on the dedicated profile."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.profile_dir, 0o700)
        channel = None
        if self.settings.store_browser == "chrome" or (
                self.settings.store_browser == "auto" and shutil.which("google-chrome")):
            channel = "chrome"
        return pw.chromium.launch_persistent_context(
            str(self.profile_dir), channel=channel, headless=headless, accept_downloads=True,
            viewport=None if not headless else {"width": 1280, "height": 900})

    def _save_cookies(self, ctx) -> None:
        """Cookies for the in-process streaming downloader (resume support). 0600, never served."""
        _write_secret(self.state_path, {"cookies": ctx.cookies()})

    def _remember(self, urls: list[str]) -> None:
        last = next((u for u in reversed(urls) if u.startswith("http") and "login" not in u.lower()
                     and "authentication" not in u.lower()), None)
        if last and not self.settings.store_downloads_url:
            _write_secret(self.hints_path, {"downloads_url": last, "pages_visited": urls[-10:]})
            print("downloads page remembered in the private session hints")

    def status(self) -> dict:
        try:
            import playwright  # noqa: F401
            have_pw = True
        except ImportError:
            have_pw = False
        verified = bool(self.db.one("SELECT 1 AS x FROM store_items LIMIT 1"))
        ready = self.settings.store_enabled and have_pw and self.has_session() and bool(self.downloads_url())
        missing = []
        if not self.settings.store_enabled:
            missing.append("store_enabled=false")
        if not have_pw:
            missing.append("playwright not installed (pip install '.[store]' && playwright install chromium)")
        if not self.has_session():
            missing.append("not logged in: run `synty-mcp store-login` (browser) or `synty-mcp store-login --terminal`")
        if not self.downloads_url():
            missing.append("downloads page unknown: finish the login on My Downloads, or set store_downloads_url")
        return {
            "provider": "synty_store",
            "available": ready,
            "verified": verified,
            "method": "playwright_session" if ready else "manual_import_only",
            "missing": missing,
            "fallback": "drop archives into the inbox folder and call sync_owned_packs / scan_library",
        }

    # -- login (owner only) ---------------------------------------------------
    def downloads_url(self) -> str | None:
        """Configured My Downloads URL, else the page the owner was on when closing the login window."""
        if self.settings.store_downloads_url:
            return self.settings.store_downloads_url
        hint = _read_secret(self.hints_path).get("downloads_url") or ""
        return hint if "/apps/downloads" in hint else LIBRARY_URL

    def login_interactive(self, timeout_s: int = 900) -> Path:
        """Open Chrome on the dedicated profile; the owner logs in, opens My Downloads and closes the window.
        Nothing is polled while the window is open: the profile itself keeps the session."""
        from playwright.sync_api import sync_playwright

        seen: list[str] = []
        with sync_playwright() as pw:
            ctx = self._launch(pw, headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            def track(p):
                p.on("framenavigated", lambda f: seen.append(f.url) if f == p.main_frame else None)

            for p in ctx.pages:
                track(p)
            ctx.on("page", track)
            page.goto(self.downloads_url() or LOGIN_URL)
            print("Log in to the Synty Store in the browser window, open 'My Downloads', then CLOSE the window.")
            try:
                ctx.wait_for_event("close", timeout=timeout_s * 1000)
            except Exception:  # noqa: BLE001 - timeout: close it ourselves
                ctx.close()
        self._remember(seen)
        return self.profile_dir

    def login_terminal(self, email: str | None = None, timeout_s: int = 300) -> Path:
        """Headless login for servers without a display: email + one-time code typed in the terminal.
        Stops (does not try to bypass) when the site shows a CAPTCHA or another challenge."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = self._launch(pw, headless=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            seen: list[str] = []
            page.on("framenavigated", lambda f: seen.append(f.url) if f == page.main_frame else None)
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=90_000)

            def challenge() -> bool:
                return bool(page.query_selector("iframe[src*='captcha'], iframe[title*='captcha' i], "
                                                "[class*='captcha' i], iframe[src*='challenges']"))

            if challenge():
                ctx.close()
                raise StoreUnavailable("the store shows a CAPTCHA: log in on a machine with a browser "
                                       "(synty-mcp store-login) and copy data/secrets/browser-profile to the server")
            email = email or input("Synty Store email: ").strip()
            box = page.wait_for_selector("input[type=email], input[name*=email i]", timeout=30_000)
            box.fill(email)
            box.press("Enter")
            page.wait_for_load_state("networkidle")
            if challenge():
                ctx.close()
                raise StoreUnavailable("the store asked for a CAPTCHA after the email: use the browser login")
            code = input("Code received by email: ").strip()
            field = page.wait_for_selector("input[autocomplete='one-time-code'], input[name*=code i], "
                                           "input[inputmode=numeric]", timeout=60_000)
            field.fill(code)
            field.press("Enter")
            page.wait_for_load_state("networkidle", timeout=timeout_s * 1000)
            if "login" in page.url.lower() or "authentication" in page.url.lower():
                ctx.close()
                raise StoreUnavailable("login did not complete (wrong or expired code?)")
            if not self.downloads_url():
                link = page.query_selector("a:has-text('Downloads'), a:has-text('My Downloads')")
                if link:
                    link.click()
                    page.wait_for_load_state("networkidle")
            self._save_cookies(ctx)
            ctx.close()
        self._remember(seen)
        return self.profile_dir

    # -- listing -----------------------------------------------------------
    def list_purchases(self, log=print, progress=None) -> list[dict]:
        try:
            return self._list_purchases(log=log, progress=progress)
        except StoreUnavailable:
            raise
        except Exception as exc:
            # Browser exceptions include navigation URLs with account identifiers.
            raise StoreUnavailable(f"store listing failed ({type(exc).__name__}); retry or log in again") from None

    def _list_purchases(self, log=print, progress=None) -> list[dict]:
        """Walk the Sky Pilot library (paginated), open every purchased pack and record each file:
        name, variant (Unity_2022_3 / SourceFiles / Unreal / Godot ...), version, size. The download
        hrefs (which embed the account e-mail) are stored in secrets only."""
        st = self.status()
        if not st["available"]:
            raise StoreUnavailable("; ".join(st["missing"]))
        from playwright.sync_api import sync_playwright

        found: list[dict] = []
        links = _read_secret(self.links_path)
        with sync_playwright() as pw:
            ctx = self._launch(pw, headless=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            items: list[tuple[str, str]] = []
            url, visited = self.downloads_url(), set()
            while url and url not in visited:
                visited.add(url)
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                if "login" in page.url.lower() or "authentication" in page.url.lower():
                    ctx.close()
                    raise StoreUnavailable("session expired: run `synty-mcp store-login` again")
                try:
                    page.wait_for_selector("a[href*='/order_items/']", timeout=30_000)
                except Exception as exc:  # noqa: BLE001
                    ctx.close()
                    raise StoreUnavailable(f"no purchases found on {urlparse(url).path} (page layout changed?)") from exc
                for title, href in page.eval_on_selector_all(
                        "a[href*='/order_items/']", "els => els.map(e => [e.innerText.trim(), e.href])"):
                    if title and (title, href) not in items:
                        items.append((title, href))
                nxt = page.query_selector("a:has-text('Next')")
                url = nxt.get_attribute("href") if nxt else None
                if url and url.startswith("/"):
                    url = "https://syntystore.com" + url
            log(f"store: {len(items)} purchased packs")
            for i, (pack_title, href) in enumerate(items):
                if progress:
                    progress(i / max(1, len(items)), pack_title)
                page.goto(href, wait_until="domcontentloaded", timeout=60_000)
                try:
                    page.wait_for_selector(DOWNLOAD_SEL, timeout=30_000)
                except Exception:  # noqa: BLE001 - a pack without files
                    log(f"store: no files listed for {pack_title}")
                    continue
                hrefs = page.eval_on_selector_all(DOWNLOAD_SEL, "els => els.map(e => e.href)")
                text = page.inner_text("main") if page.query_selector("main") else page.inner_text("body")
                blocks = parse_file_blocks(text)
                if len(blocks) != len(hrefs):
                    log(f"store: {pack_title}: {len(hrefs)} links but {len(blocks)} file descriptions; using link order")
                for j, dl in enumerate(hrefs):
                    meta = blocks[j] if j < len(blocks) else {"name": pack_title, "variant": "", "size": None}
                    filename = "_".join(x for x in (meta["name"], re.sub(r"[^A-Za-z0-9]+", "_", meta["variant"]).strip("_")) if x)
                    kind = file_kind(meta["name"], meta["variant"])
                    slug = (packs.ensure_pack(self.db, meta["name"], source="synty_store")
                            or packs.ensure_pack(self.db, pack_title, source="synty_store"))
                    ref = hashlib.sha256(dl.encode()).hexdigest()[:16]
                    links[ref] = dl
                    self.db.x(
                        "INSERT INTO store_items(title, filename, link_ref, pack_slug, kind, discovered_at, variant,"
                        " size_bytes) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(link_ref) DO UPDATE SET"
                        " title=excluded.title, pack_slug=excluded.pack_slug, kind=excluded.kind,"
                        " variant=excluded.variant, size_bytes=excluded.size_bytes",
                        (pack_title, filename, ref, slug, kind, now(), meta["variant"] or None, meta["size"]),
                    )
                    found.append({"link_ref": ref, "pack": slug, "title": pack_title, "file": filename,
                                  "kind": kind, "variant": meta["variant"], "size_bytes": meta["size"]})
            self._save_cookies(ctx)
            ctx.close()
        _write_secret(self.links_path, links)
        log(f"store: {len(found)} downloadable files")
        return found

    # -- download ------------------------------------------------------------
    def download(self, link_ref: str, dest_dir: Path, progress=None, log=print) -> Path:
        href = _read_secret(self.links_path).get(link_ref)
        if not href:
            raise StoreUnavailable(f"unknown link_ref {link_ref}: run sync_owned_packs first")
        dest_dir.mkdir(parents=True, exist_ok=True)
        cookies = _read_secret(self.state_path).get("cookies") or []
        try:
            return self._download_httpx(href, cookies, dest_dir, progress, log)
        except StoreUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - fall back to the browser's own downloader
            log(f"direct download failed ({type(exc).__name__}); retrying through Playwright")
            try:
                return self._download_playwright(href, dest_dir, log)
            except Exception as browser_exc:
                raise StoreUnavailable(f"store download failed ({type(browser_exc).__name__}); retry or log in again") from None

    def _download_httpx(self, href, cookies, dest_dir, progress, log) -> Path:
        import httpx

        jar = httpx.Cookies()
        for c in cookies:
            jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        with httpx.Client(cookies=jar, follow_redirects=True, timeout=httpx.Timeout(60, read=600)) as client:
            with client.stream("GET", href, headers={"Range": "bytes=0-0"}) as head:
                final_url = str(head.url)
                ctype = head.headers.get("content-type", "")
                cd = head.headers.get("content-disposition", "")
                if "text/html" in ctype:
                    raise RuntimeError("got an HTML page instead of a file (session or page layout changed)")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
            name = unquote(m.group(1)) if m else unquote(Path(urlparse(final_url).path).name) or f"{int(time.time())}.zip"
            name = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name)
            target = dest_dir / name
            part = target.with_name(target.name + ".part")
            start = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            with client.stream("GET", final_url, headers=headers) as r:
                if r.status_code == 416:
                    pass  # already complete
                elif r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                else:
                    mode = "ab" if r.status_code == 206 else "wb"
                    total = int(r.headers.get("content-length") or 0) + (start if r.status_code == 206 else 0)
                    done = start if r.status_code == 206 else 0
                    with open(part, mode) as fh:
                        for chunk in r.iter_bytes(1024 * 1024):
                            fh.write(chunk)
                            done += len(chunk)
                            if progress and total:
                                progress(done / total)
            part.replace(target)
            log(f"downloaded {target.name} ({target.stat().st_size} bytes)")
            return target

    def _download_playwright(self, href, dest_dir, log) -> Path:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = self._launch(pw, headless=True)
            page = ctx.new_page()
            with page.expect_download(timeout=600_000) as info:
                try:
                    page.goto(href)
                except Exception:  # noqa: BLE001 - navigation aborts when a download starts
                    pass
            dl = info.value
            target = dest_dir / re.sub(r"[^A-Za-z0-9._\- ]+", "_", dl.suggested_filename)
            dl.save_as(target)
            ctx.close()
        log(f"downloaded {target.name} via browser")
        return target
