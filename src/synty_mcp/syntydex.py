"""Optional SyntyDex provider.

Verified 2026-10-13 (see docs/INTEGRATIONS.md): https://dex.syntystore.com/ is
a Unity WebGL application. Its catalogue ships inside the compiled Unity data
bundle; there is no documented public API, no robots.txt and no published
search endpoint. This module therefore does **not** query the site and does
not reverse-engineer the WebGL bundle.

What it does support:

* ``disabled`` (default): every call says so explicitly.
* ``local export``: a file the owner fills by hand from SyntyDex (the Dex UI
  copies asset names to the clipboard) — one name per line, optionally
  ``Name<TAB>Pack``, or JSON ``[{"name": ..., "pack": ...}]``. Searched
  locally, labelled as user-provided.
* ``match_to_local`` works offline in every mode: it maps any SyntyDex/asset
  name onto the local catalog.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import unity
from .search import id_tokens

STATUS = {
    "provider": "syntydex",
    "site": "https://dex.syntystore.com/",
    "integration": "no documented API; live querying not implemented by design",
}


def _load_export(path: Path | None) -> list[dict]:
    if not path or not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        return [d if isinstance(d, dict) else {"name": str(d)} for d in data]
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, pack = line.partition("\t")
        rows.append({"name": name.strip(), "pack": pack.strip() or None})
    return rows


class SyntyDexProvider:
    def __init__(self, settings, ctx):
        self.settings = settings
        self.ctx = ctx

    def status(self) -> dict:
        if not self.settings.syntydex_enabled:
            return {**STATUS, "available": False, "mode": "disabled",
                    "reason": "SyntyDex provider is disabled (syntydex_enabled=false)."}
        path = self.settings.syntydex_export_file
        if not path or not path.is_file():
            return {**STATUS, "available": False, "mode": "local_export",
                    "reason": f"enabled, but the local export file is missing: {path}"}
        return {**STATUS, "available": True, "mode": "local_export", "export_file": str(path),
                "entries": len(_load_export(path))}

    def search(self, query: str, limit: int = 20) -> dict:
        st = self.status()
        if not st["available"]:
            return {**st, "results": []}
        q = {t for t in id_tokens(query)} | set(re.findall(r"[a-z0-9]+", query.lower()))
        rows = []
        for e in _load_export(self.settings.syntydex_export_file):
            toks = id_tokens(e.get("name", "")) | set(re.findall(r"[a-z0-9]+", (e.get("name") or "").lower()))
            hit = len(q & toks)
            if hit:
                rows.append((hit, e))
        rows.sort(key=lambda r: (-r[0], r[1].get("name") or ""))
        return {**st, "source": "user-provided SyntyDex export (not live)",
                "results": [dict(e, local=self.match_to_local(e["name"], e.get("pack"))["matches"][:1])
                            for _, e in rows[:limit]]}

    def match_to_local(self, name: str, pack: str | None = None, limit: int = 5) -> dict:
        """Map a SyntyDex / asset name to local catalog assets (offline)."""
        cache = self.ctx.cache
        cache.refresh()
        name = (name or "").strip()
        out = []
        exact = cache.resolve(name)
        for pid, a in exact:
            out.append((100, pid, a, "exact_id"))
        if not out:
            key = unity.norm_name(name.replace(" ", "_"))
            want = id_tokens(name) - {"sm", "sk"}
            for pid, a in cache.iter_assets():
                if pack and pack.lower().replace(" ", "_") not in pid.lower():
                    continue
                if unity.norm_name(a["id"]) == key:
                    out.append((90, pid, a, "normalized_id"))
                    continue
                toks = id_tokens(a["id"]) | id_tokens(a.get("name") or "")
                if want and want <= toks:
                    out.append((60 + len(want), pid, a, "all_tokens"))
        out.sort(key=lambda r: (-r[0], r[1], r[2]["id"]))
        searcher = self.ctx.searcher
        return {
            "query": name,
            "matches": [dict(searcher.row(pid, a), match=how, confidence=s) for s, pid, a, how in out[:limit]],
            "note": None if out else "no local asset matches; the pack may not be indexed or not owned",
        }
