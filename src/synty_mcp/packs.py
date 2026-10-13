"""Owned-pack registry: seeding from data/owned_packs.yaml and attaching
archive filenames / folder names to the right pack."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import yaml

from .db import StateDB, dumps, loads

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
NOISE = {
    "unity", "unreal", "godot", "source", "sourcefiles", "files", "file", "fbx", "urp", "hdrp", "brp", "srp",
    "builtin", "built", "in", "ue", "ue4", "ue5", "v", "ver", "version", "zip", "unitypackage", "package",
    "the", "and", "update", "final", "copy",
}
VERSION_RE = re.compile(r"(?i)(?:^|[_\-\s.])v?(\d+(?:[._]\d+){1,3})(?=$|[_\-\s.])")


def tokens(text: str) -> list[str]:
    stem = Path(text).name
    for ext in (".unitypackage", ".zip", ".7z", ".tar.gz", ".tgz", ".rar"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
    out: list[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", stem):
        for tok in _CAMEL.findall(chunk):
            out.append(tok.lower())
    # "SciFi" splits into sci/fi; also keep the joined form.
    joined = []
    for a, b in zip(out, out[1:]):
        if (a, b) == ("sci", "fi"):
            joined.append("scifi")
    return out + joined


def meaningful(toks: list[str]) -> set[str]:
    return {t for t in toks if t not in NOISE and not t.isdigit()}


def guess_version(filename: str) -> str | None:
    m = VERSION_RE.search(Path(filename).stem)
    return m.group(1).replace("_", ".") if m else None


def guess_kind(filename: str) -> str:
    low = filename.lower()
    toks = set(tokens(filename))
    if low.endswith(".unitypackage"):
        return "unity"
    if "sourcefiles" in toks or ("source" in toks and "files" in toks) or "fbx" in toks or "source" in toks:
        return "source"
    if "unreal" in toks or "ue4" in toks or "ue5" in toks:
        return "unreal"
    if "godot" in toks:
        return "godot"
    if "unity" in toks:
        return "unity"
    return "unknown"


EXAMPLE_SEED = "data/owned_packs.example.yaml"


def load_seed(path: Path | None = None) -> list[dict]:
    """The owner's pack list from ``path``, else the packaged example list."""
    if path is not None:
        text = Path(path).read_text(encoding="utf-8")
    else:
        text = resources.files("synty_mcp").joinpath(EXAMPLE_SEED).read_text(encoding="utf-8")
    return yaml.safe_load(text)["packs"]


def seed_packs(db: StateDB, path: Path | None = None) -> int:
    """Insert seed packs that are not yet known. Never overwrites learned state."""
    added = 0
    for p in load_seed(path):
        if db.one("SELECT slug FROM packs WHERE slug=?", (p["slug"],)):
            db.x("UPDATE packs SET seed=? WHERE slug=?", (dumps(p), p["slug"]))
            continue
        db.x(
            "INSERT INTO packs(slug, line, name, pack_id, content, seed) VALUES(?,?,?,?,?,?)",
            (p["slug"], p["line"], p["name"], p["pack_id"], p.get("content"), dumps(p)),
        )
        added += 1
    return added


def match_pack(db: StateDB, name: str) -> tuple[str | None, int]:
    """Best owned pack for an archive filename or folder name -> (slug, score)."""
    have = meaningful(tokens(name))
    best: tuple[str | None, int] = (None, 0)
    for row in db.q("SELECT slug, seed, pack_id FROM packs"):
        seed = loads(row["seed"], {})
        if any(x in have for x in seed.get("exclude", [])):
            continue
        sets = [seed.get("match") or []] + list(seed.get("alt_match") or [])
        # A confirmed / guessed pack_id is also a valid token set.
        sets.append(sorted(meaningful(tokens(row["pack_id"]))))
        for s in sets:
            req = {t.lower() for t in s}
            if req and req <= have:
                score = len(req) * 10 - len(have - req)
                if score > best[1]:
                    best = (row["slug"], score)
    return best


def pack_id_from_filename(filename: str) -> str | None:
    """Synty's own id from names like ``POLYGON_City_Unity_2022_v1_2.zip`` /
    ``POLYGON_City_SourceFiles_v2.zip``: everything before the first engine/
    noise token."""
    stem = Path(filename).name
    for ext in (".unitypackage", ".zip"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
    parts = [p for p in re.split(r"[_\-\s]+", stem) if p]
    keep: list[str] = []
    for p in parts:
        low = p.lower()
        if low in NOISE or low.startswith(("unity", "unreal", "godot", "source", "ue4", "ue5")) or re.fullmatch(
            r"v?\d+(\.\d+)*", low
        ):
            break
        keep.append(p)
    if len(keep) < 2 or keep[0].upper() not in {"POLYGON", "SIMPLE", "SIDEKICK", "ANIMATION", "INTERFACE", "POLYGONMINI"}:
        return None
    return "_".join(keep)


def pack_rows(db: StateDB) -> list[dict]:
    rows = db.q("SELECT * FROM packs ORDER BY line, name")
    for r in rows:
        r["seed"] = loads(r["seed"], {})
    return rows


def get_pack(db: StateDB, ref: str) -> dict | None:
    """Resolve slug, pack_id or a free-text name to a pack row."""
    r = db.one("SELECT * FROM packs WHERE slug=? OR lower(pack_id)=lower(?)", (ref, ref))
    if r:
        r["seed"] = loads(r["seed"], {})
        return r
    slug, score = match_pack(db, ref)
    if slug:
        r = db.one("SELECT * FROM packs WHERE slug=?", (slug,))
        r["seed"] = loads(r["seed"], {})
        return r
    return None


LINES = {"POLYGON", "SIMPLE", "SIDEKICK", "ANIMATION", "INTERFACE"}


def ensure_pack(db: StateDB, name: str, *, source: str) -> str | None:
    """Register an owned pack discovered from a store download or an archive
    name (``POLYGON_City_Unity_2022.zip`` -> ``polygon-city``), so the pack
    list builds itself from what the owner actually has. Returns the slug, or
    None when the name does not look like a Synty pack."""
    slug, _score = match_pack(db, name)
    if slug:
        return slug
    pack_id = pack_id_from_filename(name.replace(" - ", "_").replace(" ", "_"))
    if not pack_id:
        return None
    parts = pack_id.split("_")
    line = parts[0].upper()
    rest = parts[1:]
    if line == "POLYGONMINI" or (line == "POLYGON" and rest and rest[0].lower() == "mini"):
        line, rest = "POLYGON MINI", rest[1:] if line == "POLYGON" else rest
    if line.split()[0] not in LINES or not rest:
        return None
    slug = "-".join([line.lower().replace(" ", "-")] + [r.lower() for r in rest])
    seed = {"slug": slug, "line": line, "name": " ".join(rest), "pack_id": pack_id,
            "match": sorted(meaningful(tokens(pack_id)) - {"polygon"}) or [rest[0].lower()],
            "hints": [], "discovered_from": source}
    db.x("INSERT OR IGNORE INTO packs(slug, line, name, pack_id, pack_id_confirmed, content, seed) "
         "VALUES(?,?,?,?,1,?,?)", (slug, line, seed["name"], pack_id, None, dumps(seed)))
    return slug
