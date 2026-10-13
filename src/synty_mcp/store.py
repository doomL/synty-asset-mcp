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
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import packs
from .db import now

STATE_FILE = "synty_store_state.json"
LINKS_FILE = "synty_store_links.json"
LOGIN_URL = "https://syntystore.com/account/login"


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

    def status(self) -> dict:
        try:
            import playwright  # noqa: F401
            have_pw = True
        except ImportError:
            have_pw = False
        verified = bool(self.db.one("SELECT 1 AS x FROM store_items LIMIT 1"))
        ready = self.settings.store_enabled and have_pw and self.state_path.is_file() and bool(
            self.settings.store_downloads_url)
        missing = []
        if not self.settings.store_enabled:
            missing.append("store_enabled=false")
        if not have_pw:
            missing.append("playwright not installed (pip install '.[store]' && playwright install chromium)")
        if not self.state_path.is_file():
            missing.append("no saved session: run `synty-mcp store-login` on a machine with a display")
        if not self.settings.store_downloads_url:
            missing.append("store_downloads_url not configured (copy it from your browser's My Downloads page)")
        return {
            "provider": "synty_store",
            "available": ready,
            "verified": verified,
            "method": "playwright_session" if ready else "manual_import_only",
            "missing": missing,
            "fallback": "drop archives into the inbox folder and call sync_owned_packs / scan_library",
        }

    # -- login (interactive, owner only) ----------------------------------
    def login_interactive(self) -> Path:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.goto(self.settings.store_downloads_url or LOGIN_URL)
            input("Log in to the Synty Store in the opened window, open 'My Downloads', then press Enter here... ")
            state = ctx.storage_state()
            if page.url and not self.settings.store_downloads_url:
                print(f"current page: {page.url}  (set store_downloads_url to this if it is My Downloads)")
            browser.close()
        _write_secret(self.state_path, state)
        return self.state_path

    # -- listing -----------------------------------------------------------
    def list_purchases(self, log=print) -> list[dict]:
        st = self.status()
        if not st["available"]:
            raise StoreUnavailable("; ".join(st["missing"]))
        from playwright.sync_api import sync_playwright

        rx = re.compile(self.settings.store_link_text_regex)
        found: list[dict] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(storage_state=str(self.state_path))
            page = ctx.new_page()
            page.goto(self.settings.store_downloads_url, wait_until="networkidle", timeout=90_000)
            if "login" in page.url.lower():
                browser.close()
                raise StoreUnavailable("saved session expired: run `synty-mcp store-login` again")
            anchors = page.eval_on_selector_all(
                self.settings.store_link_selector,
                "els => els.map(e => ({href: e.href, text: (e.innerText||e.textContent||'').trim(),"
                " ctx: (e.closest('tr,li,article,section,div')||e).innerText.slice(0,300)}))",
            )
            _write_secret(self.state_path, ctx.storage_state())  # keep the session fresh
            browser.close()
        links = _read_secret(self.links_path)
        for a in anchors:
            href = a.get("href") or ""
            label = " ".join((a.get("text") or "").split())
            if not href.startswith("http") or not (rx.search(label) or rx.search(href)):
                continue
            fname = unquote(Path(urlparse(href).path).name)
            title = " ".join((a.get("ctx") or label).split())[:200]
            ref = hashlib.sha256(href.encode()).hexdigest()[:16]
            slug, _score = packs.match_pack(self.db, fname or title)
            if not slug:
                slug, _score = packs.match_pack(self.db, title)
            if not slug:  # a purchase the local list does not know: the store is the source of truth
                slug = packs.ensure_pack(self.db, fname or title, source="synty_store") or packs.ensure_pack(
                    self.db, title, source="synty_store")
            links[ref] = href
            self.db.x(
                "INSERT INTO store_items(title, filename, link_ref, pack_slug, kind, discovered_at) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(link_ref) DO UPDATE SET title=excluded.title, pack_slug=excluded.pack_slug",
                (title, fname or None, ref, slug, packs.guess_kind(fname or title), now()),
            )
            found.append({"link_ref": ref, "title": title, "filename": fname or None, "pack": slug})
        _write_secret(self.links_path, links)
        log(f"store: {len(found)} download links found")
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
            log(f"direct download failed ({exc}); retrying through Playwright")
            return self._download_playwright(href, dest_dir, log)

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
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(storage_state=str(self.state_path), accept_downloads=True)
            page = ctx.new_page()
            with page.expect_download(timeout=600_000) as info:
                try:
                    page.goto(href)
                except Exception:  # noqa: BLE001 - navigation aborts when a download starts
                    pass
            dl = info.value
            target = dest_dir / re.sub(r"[^A-Za-z0-9._\- ]+", "_", dl.suggested_filename)
            dl.save_as(target)
            browser.close()
        log(f"downloaded {target.name} via browser")
        return target
