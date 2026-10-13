"""Settings: one YAML file plus SYNTY_MCP_* environment overrides.

Every directory the server reads or writes is derived from ``data_dir`` (or
set explicitly) and every file operation is confined to those roots by
:mod:`synty_mcp.safepaths`. No machine path is hardcoded here.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ENV_PREFIX = "SYNTY_MCP_"
CONFIG_ENV = "SYNTY_MCP_CONFIG"


@dataclass
class Settings:
    data_dir: Path
    # Layout under data_dir (overridable one by one).
    archives_dir: Path = None  # immutable original downloads (zip / unitypackage)
    inbox_dir: Path = None  # manual drop folder, imported then left untouched
    library_dir: Path = None  # extracted trees: <slug>/{unity,source,...}
    catalogs_dir: Path = None  # synty-inventory catalogs + search.db
    converted_dir: Path = None  # conversion cache: <preset>/<pack_id>/...
    previews_dir: Path = None  # rendered thumbnails
    collections_dir: Path = None  # prepared per-project collections
    secrets_dir: Path = None  # store session state; never served
    tmp_dir: Path = None
    state_db: Path = None

    # Extra roots that collections may be exported into (local stdio use:
    # e.g. a game project folder). Empty = only collections_dir.
    export_roots: list[Path] = field(default_factory=list)
    # Extra read-only roots that may be scanned in place (e.g. an existing
    # extracted Source_Files folder). Never written to.
    source_roots: list[Path] = field(default_factory=list)

    # Your owned-packs list (local, not committed). Default: <data_dir>/owned_packs.yaml,
    # falling back to the packaged example list.
    owned_packs_file: Path | None = None

    blender: str | None = None
    blender_timeout_s: int = 600
    workers: int = 2

    # HTTP
    host: str = "127.0.0.1"
    port: int = 8765
    public_base_url: str | None = None
    tokens: list[str] = field(default_factory=list)  # plain or "sha256:<hex>"
    allowed_hosts: list[str] = field(default_factory=list)  # Host headers accepted (DNS-rebinding guard)
    allow_unauthenticated_localhost: bool = False

    # Licence posture: preview *images* are not pushed into an agent's
    # context (which would send Synty artwork to an external AI service)
    # unless the owner explicitly opts in.
    allow_preview_images_to_agent: bool = False

    # Extraction guards (zip-bomb / runaway archives).
    max_extract_bytes: int = 40 * 1024**3
    max_extract_files: int = 400_000

    # Optional providers
    syntydex_enabled: bool = False
    syntydex_export_file: Path | None = None
    store_enabled: bool = False
    store_downloads_url: str | None = None
    store_link_selector: str = "a[href]"
    store_link_text_regex: str = r"(?i)download|\.zip|\.unitypackage"

    def __post_init__(self) -> None:
        d = Path(self.data_dir).expanduser().resolve()
        self.data_dir = d
        defaults = {
            "archives_dir": d / "archives",
            "inbox_dir": d / "inbox",
            "library_dir": d / "library",
            "catalogs_dir": d / "catalogs",
            "converted_dir": d / "converted",
            "previews_dir": d / "previews",
            "collections_dir": d / "collections",
            "secrets_dir": d / "secrets",
            "tmp_dir": d / "tmp",
            "state_db": d / "state.sqlite3",
        }
        for key, val in defaults.items():
            cur = getattr(self, key)
            setattr(self, key, Path(cur).expanduser().resolve() if cur else val)
        self.export_roots = [Path(p).expanduser().resolve() for p in self.export_roots]
        self.source_roots = [Path(p).expanduser().resolve() for p in self.source_roots]
        if self.owned_packs_file:
            self.owned_packs_file = Path(self.owned_packs_file).expanduser().resolve()
        if self.syntydex_export_file:
            self.syntydex_export_file = Path(self.syntydex_export_file).expanduser().resolve()

    # Roots ---------------------------------------------------------------
    @property
    def write_roots(self) -> list[Path]:
        return [
            self.archives_dir,
            self.inbox_dir,
            self.library_dir,
            self.catalogs_dir,
            self.converted_dir,
            self.previews_dir,
            self.collections_dir,
            self.secrets_dir,
            self.tmp_dir,
            *self.export_roots,
        ]

    @property
    def read_roots(self) -> list[Path]:
        """Roots whose files may be *served* or handed to agents. secrets_dir is excluded."""
        return [
            self.archives_dir,
            self.inbox_dir,
            self.library_dir,
            self.catalogs_dir,
            self.converted_dir,
            self.previews_dir,
            self.collections_dir,
            *self.source_roots,
            *self.export_roots,
        ]

    def ensure_dirs(self) -> None:
        for p in (
            self.archives_dir,
            self.inbox_dir,
            self.library_dir,
            self.catalogs_dir,
            self.converted_dir,
            self.previews_dir,
            self.collections_dir,
            self.tmp_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.secrets_dir, 0o700)
        except OSError:
            pass

    def owned_packs_path(self) -> Path | None:
        """The owner's optional pack list (None = discover packs from the store / imported archives)."""
        if self.owned_packs_file:
            if not self.owned_packs_file.is_file():
                raise FileNotFoundError(f"owned_packs_file not found: {self.owned_packs_file}")
            return self.owned_packs_file
        local = self.data_dir / "owned_packs.yaml"
        return local if local.is_file() else None

    def blender_executable(self) -> str | None:
        cand = self.blender or os.environ.get("BLENDER")
        if cand:
            p = Path(cand).expanduser()
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
            found = shutil.which(cand)
            return found
        return shutil.which("blender")

    def public_dict(self) -> dict:
        """Configuration as shown to agents: no tokens, no secrets path."""
        return {
            "data_dir": str(self.data_dir),
            "library_dir": str(self.library_dir),
            "catalogs_dir": str(self.catalogs_dir),
            "converted_dir": str(self.converted_dir),
            "collections_dir": str(self.collections_dir),
            "inbox_dir": str(self.inbox_dir),
            "export_roots": [str(p) for p in self.export_roots],
            "source_roots": [str(p) for p in self.source_roots],
            "blender": self.blender_executable(),
            "public_base_url": self.public_base_url,
            "syntydex_enabled": self.syntydex_enabled,
            "store_enabled": self.store_enabled,
            "allow_preview_images_to_agent": self.allow_preview_images_to_agent,
        }


_BOOL = {"1": True, "true": True, "yes": True, "on": True, "0": False, "false": False, "no": False, "off": False}


def _coerce(name: str, raw: str, current):
    if isinstance(current, bool):
        return _BOOL.get(raw.strip().lower(), current)
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, list):
        return [p for p in (s.strip() for s in raw.replace(";", ",").split(",")) if p]
    return raw


def load_settings(config_file: str | Path | None = None, **overrides) -> Settings:
    """Precedence (highest first): keyword overrides, SYNTY_MCP_* env, YAML file, defaults."""
    path = config_file or os.environ.get(CONFIG_ENV)
    file_cfg: dict = {}
    base = Path.cwd()
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"config file not found: {p}")
        file_cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        base = p.parent
    cfg: dict = {}
    for k, v in file_cfg.items():
        if k.endswith(("_dir", "_db", "_file")) and isinstance(v, str) and not Path(v).expanduser().is_absolute():
            v = str((base / v).resolve())
        if k in ("export_roots", "source_roots") and isinstance(v, list):
            v = [str((base / x).resolve()) if not Path(x).expanduser().is_absolute() else x for x in v]
        cfg[k] = v

    probe = Settings(data_dir=cfg.get("data_dir") or overrides.get("data_dir") or "./data")
    for fname in probe.__dataclass_fields__:
        env = os.environ.get(ENV_PREFIX + fname.upper())
        if env is None:
            continue
        cur = cfg.get(fname, getattr(probe, fname))
        if fname in ("export_roots", "source_roots", "tokens", "allowed_hosts"):
            cur = []
        cfg[fname] = _coerce(fname, env, cur)
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    if "data_dir" not in cfg:
        cfg["data_dir"] = "./data"
    # Tokens may also come from a file (docker secret).
    tok_file = os.environ.get(ENV_PREFIX + "TOKENS_FILE")
    if tok_file and Path(tok_file).is_file():
        extra = [t.strip() for t in Path(tok_file).read_text().splitlines() if t.strip() and not t.startswith("#")]
        cfg["tokens"] = list(cfg.get("tokens") or []) + extra
    known = set(Settings.__dataclass_fields__)
    unknown = set(cfg) - known
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    return Settings(**cfg)
