# Manual Synty Store login (optional)

The Synty Store publishes no download API for customers. This procedure lets the server list and download your
purchases **with your own session**, after a login you perform by hand. Credentials, cookies and signed links stay
in the `secrets/` folder and are never returned to agents.

> Status: implemented, **not yet verified** on a real account. If something does not work, manual import is always
> available.

## 1. Install Playwright

```bash
pip install -e ".[store]"
playwright install chromium
```

Docker: `WITH_PLAYWRIGHT=1 docker compose build`.

## 2. Log in (on a machine with a display)

```bash
synty-mcp store-login
```

Chromium opens. Sign in to syntystore.com (2FA included), open **My Downloads** and press Enter in the terminal.
The session is saved to `data/secrets/synty_store_state.json` (0600 permissions).

On a headless VPS: log in on your PC and copy the file into the secrets volume:

```bash
docker compose cp data/secrets/synty_store_state.json synty-mcp:/data/secrets/
docker compose exec synty-mcp chmod 600 /data/secrets/synty_store_state.json
```

## 3. Configure

In the config file (or `.env`):

```yaml
store_enabled: true
store_downloads_url: "<URL of the My Downloads page, copied from the browser after logging in>"
# if the download links do not say "Download", adapt these two:
store_link_selector: "a[href]"
store_link_text_regex: "(?i)download|\\.zip|\\.unitypackage"
```

## 4. Use

```
sync_owned_packs           → lists downloadable files and matches them to your owned packs
download_pack polygon-city → downloads (with resume), verifies, deduplicates, extracts and indexes
```

If the session expires, `sync_owned_packs` says so (`saved session expired`): repeat step 2.

## Always-available fallback

Download the files in your browser and put them in `inbox/` (or use `scripts/import_packs.sh`), then
`sync_owned_packs` / `synty-mcp inbox` and `synty-mcp scan`.
