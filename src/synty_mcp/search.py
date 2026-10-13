"""Single-asset search, scene requests, similar assets.

Candidates and their base score come from synty-inventory's own query engine
(``query.search_assets``: rule/VLM semantics + FTS5/BM25). This module adds
what a bilingual, multi-item agent request needs on top:

* Italian/English concept parsing (``lexicon``),
* a re-rank that requires the requested *head noun* and the right asset class
  (a "goblin character" must not return a goblin tent),
* availability (source file present, GLB cached, preview),
* "not found" handling with alternatives and *unindexed* owned packs that
  probably contain the item — always labelled as unverified.

Nothing here invents assets: every row is a catalog record.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from synty_inventory import query as si_query

from . import lexicon
from .db import loads

_SPLIT = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

STYLE_PACKS = {
    "medieval": ("fantasy", "medieval", "knights", "kingdom", "castle", "viking"),
    "fantasy": ("fantasy", "kingdom", "dungeon", "elven", "dragon"),
    "viking": ("viking",),
    "modern": ("city", "suburb", "heist", "town"),
    "scifi": ("scifi", "sci_fi", "space"),
    "snow": ("snow", "arctic", "winter"),
    "pirate": ("pirate",),
}


def id_tokens(text: str) -> set[str]:
    out: set[str] = set()
    for chunk in re.split(r"[^A-Za-z0-9]+", text or ""):
        for t in _SPLIT.findall(chunk):
            out.add(t.lower())
    return out


def asset_class(asset: dict) -> str:
    t = (asset.get("type") or "").lower()
    k = (asset.get("kind") or "").lower()
    if t.startswith("character"):
        return "character"
    if t == "animation" or k == "animation":
        return "animation"
    if t == "fx" or k == "fx":
        return "effect"
    if t.startswith("ui") or k == "ui":
        return "ui"
    if t == "skybox" or k in ("material", "texture"):
        return "texture"
    if k == "prefab":
        return "prefab"
    return "model"


def _haystack(asset: dict) -> tuple[set[str], set[str], str]:
    strong = id_tokens(asset.get("id", "")) | id_tokens(asset.get("name", ""))
    strong |= {t.lower() for t in asset.get("tags") or []} | {c.lower() for c in asset.get("category") or []}
    module = asset.get("module") or {}
    weak = id_tokens(module.get("family") or "") | id_tokens(asset.get("semantic_role") or "")
    weak |= id_tokens(asset.get("semantic_detail") or "") | id_tokens(asset.get("type") or "")
    desc = (asset.get("description") or "").lower()
    return strong, weak, desc


def score_asset(item: dict, pack_id: str, asset: dict, base: float = 0.0) -> tuple[float, list[str]]:
    """Relevance of one catalog asset for one parsed request item. <= 0 = reject."""
    strong, weak, desc = _haystack(asset)
    why: list[str] = []
    s = 0.0
    head = item.get("concept")
    if head:
        terms = lexicon.CONCEPTS.get(head, [head])
        canon = terms[0]
        if canon in strong or canon.rstrip("s") in strong or canon + "s" in strong:
            s += 40
            why.append(f"name:{canon}")
        elif any(t in strong for t in terms[1:]):
            hit = next(t for t in terms[1:] if t in strong)
            s += 24
            why.append(f"synonym:{hit}")
        elif any(t in weak for t in terms):
            s += 10
            why.append("role/kit")
        elif any(re.search(rf"\b{re.escape(t)}", desc) for t in terms[:3]):
            s += 6
            why.append("description")
        elif canon in id_tokens(pack_id) and (
                not item.get("wants_character") or asset_class(asset) == "character"):
            # "goblin" assets of POLYGON_Goblin_War_Camp are SM_Chr_Archer_01 etc.:
            # the pack name carries the meaning.
            s += 22
            why.append(f"pack:{canon}")
        else:
            return 0.0, []
    for q in item.get("qualifiers") or []:
        qt = lexicon.CONCEPTS.get(q, [q])
        if any(t in strong for t in qt[:3]):
            s += 12
            why.append(f"+{q}")
        elif q in lexicon.CHARACTER_CONCEPTS and asset_class(asset) == "character":
            s += 4
    pid = pack_id.lower()
    for m in item.get("modifiers") or []:
        if m in strong or m in desc.split():
            s += 10
            why.append(f"+{m}")
        elif any(p in pid for p in STYLE_PACKS.get(m, ())):
            s += 6
            why.append(f"style:{m}")
    for u in item.get("unknown") or []:
        if u in strong:
            s += 14
            why.append(f"+{u}")
    cls = asset_class(asset)
    if item.get("wants_character"):
        s += 18 if cls == "character" else -30
    elif cls == "character" and head:
        s -= 30
    if item.get("wants_animation"):
        s += 18 if cls == "animation" else -20
    elif cls == "animation":
        s -= 25
    if item.get("wants_fx"):
        s += 10 if cls == "effect" else 0
    if item.get("wants_ui"):
        s += 10 if cls == "ui" else -10
    elif cls == "ui":
        s -= 25
    if asset.get("placeable") is False and not (item.get("wants_animation") or item.get("wants_ui") or item.get("wants_fx")):
        s -= 15
    if (asset.get("module") or {}).get("role") and head in ("house", "building", "shop", "tavern", "castle"):
        # kit pieces are components, not a finished building
        role = asset["module"]["role"]
        if role in ("corner", "door", "roof", "stairs", "floor", "base", "window", "wall", "trim"):
            s -= 12
            why.append(f"kit_piece:{role}")
    if (asset.get("bounds") or {}).get("source") == "measured":
        s += 2
    s += min(10.0, math.log1p(max(0.0, base)) * 2)
    return s, why


class Searcher:
    def __init__(self, ctx):
        self.ctx = ctx

    # -- helpers ---------------------------------------------------------
    def _candidates(self, item: dict, pack: str | None, limit: int) -> list[tuple[str, dict, float]]:
        cache = self.ctx.cache
        cache.refresh()
        terms = item["terms"] or [item["text"]]
        rows = si_query.search_assets(
            self.ctx.settings.catalogs_dir, " ".join(terms), pack=pack, limit=limit, include_nonplaceable=True
        )
        out = []
        seen = set()
        for r in rows:
            a = cache.get(r["pack"], r["id"])
            if a is not None:
                out.append((r["pack"], a, float(r.get("score") or 0)))
                seen.add((r["pack"], r["id"]))
        # synty-inventory's FTS keys on English prose; make sure exact token
        # hits on ids are never lost (e.g. very short catalogs).
        if len(out) < 10:
            want = set(terms)
            for pid, a in cache.iter_assets():
                if pack and pack.lower() not in pid.lower():
                    continue
                if (pid, a.get("id")) in seen:
                    continue
                if want & id_tokens(a.get("id", "")):
                    out.append((pid, a, 0.0))
        return out

    def bundles(self) -> set[tuple[str, str]]:
        rows = self.ctx.db.q("SELECT pack_id, asset_id FROM asset_files WHERE extra LIKE '%\"bundle\": true%'")
        return {(r["pack_id"], r["asset_id"]) for r in rows}

    def availability(self, pack_id: str, asset_id: str) -> dict:
        db = self.ctx.db
        f = db.one("SELECT mesh_path, mesh_node, preview_path, prefab_path FROM asset_files WHERE pack_id=? AND asset_id=?",
                   (pack_id, asset_id))
        conv = db.q("SELECT preset, output_path, status FROM conversions WHERE pack_id=? AND asset_id=? AND status='ok'",
                    (pack_id, asset_id))
        source = bool(f and f["mesh_path"] and Path(f["mesh_path"]).is_file())
        glb = {c["preset"]: True for c in conv if c["output_path"] and Path(c["output_path"]).is_file()}
        return {
            "source_file": source,
            "convertible": source and Path(f["mesh_path"]).suffix.lower() in (".fbx", ".obj", ".gltf", ".glb", ".blend"),
            "glb_cached": sorted(glb),
            "preview": bool(f and f["preview_path"] and Path(f["preview_path"]).is_file()),
            "unity_prefab": bool(f and f["prefab_path"]),
        }

    def row(self, pack_id: str, asset: dict, score: float | None = None, why: list[str] | None = None) -> dict:
        # Our conversion measurement wins: synty-inventory's GLB measure ignores skinning.
        bounds = {}
        conv = self.ctx.db.one("SELECT info FROM conversions WHERE pack_id=? AND asset_id=? AND status='ok' "
                               "ORDER BY created_at DESC", (pack_id, asset["id"]))
        gb = (loads(conv["info"], {}).get("glb") or {}).get("bounds") if conv else None
        if gb:
            bounds = {"size": gb["size"], "source": "converted_glb"}
        if not bounds.get("size"):
            bounds = asset.get("bounds") or {}
        pack = self.ctx.db.one("SELECT slug, line, name FROM packs WHERE pack_id=?", (pack_id,))
        out = {
            "ref": f"{pack_id}/{asset['id']}",
            "id": asset["id"],
            "name": asset.get("name"),
            "pack_id": pack_id,
            "pack": f"{pack['line']} {pack['name']}" if pack else pack_id,
            "class": asset_class(asset),
            "type": asset.get("type"),
            "kind": asset.get("kind"),
            "placeable": asset.get("placeable"),
            "size_m": [round(float(v), 3) for v in bounds["size"]] if bounds.get("size") else None,
            "size_source": bounds.get("source") if bounds else None,
            "available": self.availability(pack_id, asset["id"]),
        }
        kit = asset.get("module") or {}
        if kit.get("family"):
            out["kit"] = {"family": kit.get("family"), "role": kit.get("role")}
        if score is not None:
            out["score"] = round(score, 1)
        if why:
            out["match"] = why
        return out

    # -- tools -----------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        pack: str | None = None,
        asset_class_filter: str | None = None,
        available_only: bool = False,
        include_nonplaceable: bool = False,
        limit: int = 10,
        item: dict | None = None,
    ) -> dict:
        item = item or lexicon.analyse_phrase(query)
        if asset_class_filter in ("animation", "ui", "effect", "texture"):
            include_nonplaceable = True
        scored = []
        bundles = self.bundles()
        for pid, a, base in self._candidates(item, pack, limit=max(200, limit * 10)):
            if not include_nonplaceable and a.get("placeable") is False and not (
                item["wants_animation"] or item["wants_ui"] or item["wants_fx"]
            ):
                continue
            if asset_class_filter and asset_class(a) != asset_class_filter:
                continue
            if not item.get("concept") and not item.get("unknown"):
                continue
            s, why = score_asset(item, pid, a, base)
            if (pid, a["id"]) in bundles:
                s -= 45
                why = why + ["bundle_file"]
            if not item.get("concept"):
                # untranslated query: fall back to synty-inventory's own score
                s = base if base > 0 else s
            if s <= 0:
                continue
            scored.append((s, pid, a, why))
        scored.sort(key=lambda r: (-r[0], r[1], r[2]["id"]))
        if not scored and item.get("qualifiers"):
            # "guardia cavaliere": nothing is a "guard", but the qualifier ("knight") may be.
            q0, rest = item["qualifiers"][0], item["qualifiers"][1:]
            alt = dict(item, concept=q0, qualifiers=rest,
                       wants_character=q0 in lexicon.CHARACTER_CONCEPTS or item.get("wants_character"))
            res = self.search(query, pack=pack, asset_class_filter=asset_class_filter, available_only=available_only,
                              include_nonplaceable=include_nonplaceable, limit=limit, item=alt)
            res["understood"]["fallback_concept"] = q0
            return res
        rows = []
        for s, pid, a, why in scored:
            r = self.row(pid, a, s, why)
            if available_only and not r["available"]["source_file"]:
                continue
            rows.append(r)
            if len(rows) >= limit:
                break
        return {
            "query": query,
            "understood": {k: item[k] for k in ("qty", "concept", "qualifiers", "modifiers", "unknown", "terms")},
            "results": rows,
            "total_considered": len(scored),
        }

    def unindexed_pack_hints(self, item: dict) -> list[dict]:
        """Owned packs not yet indexed whose seed hints mention the concept, best first."""
        head = set(lexicon.CONCEPTS.get(item.get("concept") or "", [])[:2]) | {item.get("concept") or ""}
        rest = (set(item.get("terms") or []) | set(item.get("modifiers") or [])) - head
        ranked = []
        for p in self.ctx.db.q("SELECT slug, line, name, status, seed FROM packs WHERE status != 'indexed'"):
            seed = loads(p["seed"], {})
            hints = set(seed.get("hints") or []) | {t.lower() for t in seed.get("match") or []}
            h, r = head & hints, rest & hints
            if not (h or r):
                continue
            ranked.append((3 * len(h) + len(r), p, sorted(h | r)))
        ranked.sort(key=lambda x: (-x[0], x[1]["slug"]))
        return [{
            "pack": p["slug"], "name": f"{p['line']} {p['name']}", "status": p["status"],
            "because": hit, "verified": False,
            "note": "owned but not indexed: content expected from the pack description, not verified",
        } for _s, p, hit in ranked[:5]]

    def suggest_for_scene(self, request: str, *, per_item_alternatives: int = 4, available_only: bool = False) -> dict:
        items = lexicon.parse_request(request)
        out_items = []
        packs_needed: dict[str, int] = {}
        for item in items:
            res = self.search(item["text"], item=item, limit=item["qty"] + per_item_alternatives,
                              available_only=available_only)
            rows = res["results"]
            picks = rows[: item["qty"]]
            entry = {
                "request": item["text"],
                "qty": item["qty"],
                "understood": res["understood"],
                "status": "found" if len(picks) >= item["qty"] else ("partial" if picks else "not_found"),
                "selected": picks,
                "alternatives": rows[item["qty"]:],
            }
            if len(picks) < item["qty"] and picks:
                entry["note"] = (f"only {len(picks)} distinct matching asset(s) indexed; reuse with variation "
                                 f"or pick from alternatives")
            if not picks:
                relaxed = None
                if item["modifiers"] or item["qualifiers"]:
                    relaxed_item = dict(item, modifiers=[], qualifiers=[], wants_character=item["concept"] in lexicon.CHARACTER_CONCEPTS)
                    relaxed = self.search(item["concept"] or item["text"], item=relaxed_item, limit=per_item_alternatives)
                entry["alternatives"] = (relaxed or {}).get("results", [])
                entry["owned_unindexed_candidates"] = self.unindexed_pack_hints(item)
            for r in picks:
                packs_needed[r["pack_id"]] = packs_needed.get(r["pack_id"], 0) + 1
            out_items.append(entry)
        recipes = []
        try:
            indexed = set(self.ctx.cache.docs)
            recipes = [r for r in si_query.suggest_recipes_for(self.ctx.settings.catalogs_dir, request, limit=10)
                       if set(r.get("packs") or []) & indexed][:3]
        except Exception:  # noqa: BLE001 - recipes are a bonus
            recipes = []
        return {
            "request": request,
            "items": out_items,
            "packs_used": packs_needed,
            "missing": [e["request"] for e in out_items if e["status"] == "not_found"],
            "recipes": [{"id": r.get("id"), "label": r.get("label"), "kind": r.get("kind")} for r in recipes],
            "next_step": "prepare_asset_collection(refs=[...selected refs...], preset='threejs_web')",
        }

    def similar(self, ref: str, limit: int = 10) -> dict:
        hits = self.ctx.cache.resolve(ref)
        if not hits:
            return {"error": f"asset not found: {ref}"}
        pid, a = hits[0]
        base_tokens = id_tokens(a["id"]) - {"sm", "sk", "01", "02", "03"}
        base_tokens = {t for t in base_tokens if not t.isdigit()}
        btype = a.get("type")
        fam = (a.get("module") or {}).get("family")
        size = (a.get("bounds") or {}).get("size")
        scored = []
        for p2, b in self.ctx.cache.iter_assets():
            if b is a:
                continue
            t2 = {t for t in id_tokens(b["id"]) if not t.isdigit()} - {"sm", "sk"}
            if not t2:
                continue
            jac = len(base_tokens & t2) / len(base_tokens | t2)
            s = jac * 60
            if b.get("type") == btype:
                s += 15
            if fam and (b.get("module") or {}).get("family") == fam:
                s += 15
            if p2 == pid:
                s += 5
            s2 = (b.get("bounds") or {}).get("size")
            if size and s2:
                ratio = max(size[1], 1e-3) / max(s2[1], 1e-3)
                s += 10 * (1 - min(1.0, abs(math.log(ratio))))
            if s >= 25:
                scored.append((s, p2, b))
        scored.sort(key=lambda r: (-r[0], r[1], r[2]["id"]))
        return {"asset": self.row(pid, a), "similar": [self.row(p2, b, s) for s, p2, b in scored[:limit]]}

    def list_pack(self, pack_id: str, *, asset_class_filter: str | None = None, offset: int = 0, limit: int = 100,
                  query: str | None = None) -> dict:
        rows = []
        q = (query or "").lower()
        for pid, a in sorted(self.ctx.cache.iter_assets(pack_id), key=lambda r: r[1]["id"]):
            if asset_class_filter and asset_class(a) != asset_class_filter:
                continue
            if q and q not in a["id"].lower() and q not in (a.get("name") or "").lower():
                continue
            rows.append((pid, a))
        page = rows[offset: offset + limit]
        return {
            "pack_id": pack_id,
            "total": len(rows),
            "offset": offset,
            "assets": [self.row(pid, a) for pid, a in page],
        }
