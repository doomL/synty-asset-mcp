"""Path confinement. Every read/write the server performs on behalf of an
agent goes through one of these helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath


class PathViolation(PermissionError):
    """A path escaped its allowed roots (traversal, absolute member, symlink)."""


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def confine(path: str | Path, roots: list[Path], *, must_exist: bool = False) -> Path:
    """Resolve ``path`` (following symlinks) and require it to sit inside one of ``roots``."""
    p = Path(path).expanduser()
    resolved = p.resolve()
    if not any(is_within(resolved, r) for r in roots):
        raise PathViolation(f"path outside allowed roots: {p}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(str(p))
    return resolved


_WIN_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_member_path(dest_root: Path, member_name: str) -> Path:
    """Map an archive member name to a path strictly inside ``dest_root``.

    Rejects absolute names, drive letters, ``..`` components, NUL bytes and
    anything that still resolves outside ``dest_root`` (e.g. through a
    pre-existing symlink).
    """
    if "\x00" in member_name:
        raise PathViolation(f"NUL byte in archive member: {member_name!r}")
    name = member_name.replace("\\", "/")
    if name.startswith("/") or _WIN_DRIVE.match(name):
        raise PathViolation(f"absolute archive member: {member_name!r}")
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise PathViolation(f"unsafe archive member: {member_name!r}")
    root = dest_root.resolve()
    target = root.joinpath(*parts)
    # Resolve the parent (the leaf may not exist yet) to catch symlinked dirs.
    parent = target.parent.resolve() if target.parent.exists() else target.parent
    if not is_within(parent, root) and parent != root:
        raise PathViolation(f"archive member escapes destination: {member_name!r}")
    if target.is_symlink():
        raise PathViolation(f"refusing to overwrite symlink: {member_name!r}")
    return target


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]+")


def safe_name(text: str, max_len: int = 96) -> str:
    """A filesystem-safe single path component."""
    out = _SAFE_NAME.sub("_", text).strip("._") or "unnamed"
    return out[:max_len]


def relpath_posix(path: Path, root: Path) -> str:
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()
