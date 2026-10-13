# Synty Store login (optional)

The Synty Store publishes no download API for customers. This procedure lets the server list and download your
purchases **with your own session**, after a login you perform yourself. The session lives in a **dedicated browser
profile** in `data/secrets/browser-profile/` (0700); your personal Chrome profile is never used, and nothing from
it is ever returned to agents.

> Status: login, purchase listing and download verified on a real account on 2026-10-13. Manual import is always available.

## 1. Install Playwright

```bash
pip install -e ".[store]"
playwright install chromium        # only needed when Google Chrome is not installed
```

Google Chrome is used when installed (`store_browser: auto`), otherwise Playwright's Chromium.
Docker: `WITH_PLAYWRIGHT=1 docker compose build`.

## 2a. Log in on a machine with a display

```bash
synty-mcp store-login
```

Chrome opens on the dedicated profile. Sign in to syntystore.com, open **My Downloads**, then **close the window**.
The last page you were on is remembered as the downloads page (or set `store_downloads_url` yourself).

## 2b. Log in on a server without a display (VPS)

Option 1 — terminal login (email + one-time code):

```bash
synty-mcp store-login --terminal --email you@example.com
# enter the code you receive by email when asked
```

The browser runs headless on the server and fills in what you type. If the store shows a CAPTCHA or another
challenge, the command stops: it does not try to bypass it. Use option 2 instead.

Option 2 — log in on your PC, then copy the profile to the server:

```bash
synty-mcp store-login                                      # on the PC (2a)
tar -C data/secrets -czf - browser-profile synty_store_hints.json | ssh vps 'tar -C /srv/synty/secrets -xzf -'
# Docker: copy into the synty_secrets volume, e.g.
docker compose cp data/secrets/browser-profile synty-mcp:/data/secrets/
```

## 3. Configure

```yaml
store_enabled: true
# optional, otherwise the page you finished the login on is used:
store_downloads_url: "<URL of the My Downloads page>"
# Listing uses the verified Sky Pilot purchase pages and per-file download links.
```

## 4. Use

```
sync_owned_packs           → lists downloadable files; every purchase becomes an owned pack
download_pack polygon-city → downloads (with resume), verifies, deduplicates, extracts and indexes
```

If the session expires, `sync_owned_packs` says so: log in again.

## Always-available fallback

Download the files in your browser and put them in `inbox/` (or use `scripts/import_packs.sh`), then
`sync_owned_packs` / `synty-mcp inbox` and `synty-mcp scan`.
