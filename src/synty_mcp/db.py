"""SQLite state: packs, archives, extracted trees, per-asset file resolution,
conversions and jobs. The synty-inventory JSON catalogs stay the source of
record for asset *semantics*; this database records what is on disk and what
the server has done with it."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS packs(
  slug TEXT PRIMARY KEY,
  line TEXT NOT NULL,
  name TEXT NOT NULL,
  pack_id TEXT NOT NULL,          -- Synty id; a guess until an archive confirms it
  pack_id_confirmed INTEGER NOT NULL DEFAULT 0,
  content TEXT,
  owned INTEGER NOT NULL DEFAULT 1,
  seed TEXT NOT NULL,             -- JSON of the seed entry (match/hints)
  status TEXT NOT NULL DEFAULT 'owned_not_downloaded',
  asset_count INTEGER,
  indexed_at REAL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS archives(
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  path TEXT NOT NULL,
  size INTEGER NOT NULL,
  pack_slug TEXT REFERENCES packs(slug),
  kind TEXT,                      -- unity | source | godot | unreal | unknown
  version TEXT,
  origin TEXT NOT NULL,           -- manual | store | local
  verified TEXT,                  -- ok | failed: <reason>
  imported_at REAL NOT NULL,
  extracted_path TEXT,
  extracted_at REAL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS trees(
  pack_slug TEXT NOT NULL REFERENCES packs(slug),
  kind TEXT NOT NULL,             -- unity | source | godot
  path TEXT NOT NULL,
  archive_id INTEGER,
  external INTEGER NOT NULL DEFAULT 0, -- 1 = a read-only source root scanned in place
  added_at REAL NOT NULL,
  PRIMARY KEY(pack_slug, kind, path)
);
CREATE TABLE IF NOT EXISTS guids(
  pack_slug TEXT NOT NULL,
  guid TEXT NOT NULL,
  path TEXT NOT NULL,
  PRIMARY KEY(pack_slug, guid)
);
CREATE TABLE IF NOT EXISTS asset_files(
  pack_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  pack_slug TEXT,
  kind TEXT,
  type TEXT,
  mesh_path TEXT,                 -- absolute source model (fbx/obj/gltf/glb)
  mesh_node TEXT,                 -- object to keep when the model is a bundle
  prefab_path TEXT,
  textures TEXT,                  -- JSON {"slots": [...], "by_material": {...}, "fallback": ...}
  preview_path TEXT,
  extra TEXT,
  PRIMARY KEY(pack_id, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_asset_files_asset ON asset_files(asset_id);
CREATE TABLE IF NOT EXISTS conversions(
  key TEXT PRIMARY KEY,
  pack_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  preset TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_fingerprint TEXT NOT NULL,
  output_path TEXT,
  status TEXT NOT NULL,           -- ok | failed
  info TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_asset ON conversions(pack_id, asset_id);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  dedupe_key TEXT,
  params TEXT,
  status TEXT NOT NULL,           -- queued | running | done | failed
  progress REAL,
  message TEXT,
  result TEXT,
  error TEXT,
  log TEXT NOT NULL DEFAULT '',
  pid INTEGER,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key, status);
CREATE TABLE IF NOT EXISTS store_items(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  filename TEXT,
  link_ref TEXT NOT NULL UNIQUE,  -- opaque reference; the href itself is in secrets
  pack_slug TEXT,
  kind TEXT,
  discovered_at REAL NOT NULL,
  archive_id INTEGER
);
"""


class StateDB:
    """Thread-safe wrapper: one connection per thread, WAL mode."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._con().executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.q("PRAGMA table_info(jobs)")}
        if "pid" not in cols:
            self._con().execute("ALTER TABLE jobs ADD COLUMN pid INTEGER")

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=30000")
            self._local.con = con
        return con

    @contextmanager
    def tx(self):
        con = self._con()
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise

    def q(self, sql: str, args: tuple | list = ()) -> list[dict]:
        return [dict(r) for r in self._con().execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict | None:
        row = self._con().execute(sql, args).fetchone()
        return dict(row) if row else None

    def x(self, sql: str, args: tuple | list = ()) -> int:
        with self.tx() as con:
            cur = con.execute(sql, args)
            return cur.lastrowid


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def now() -> float:
    return time.time()
