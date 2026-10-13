"""Archive integrity and safe, streaming extraction (ZIP and .unitypackage).

Originals are never modified. Extraction writes only inside the destination
root: absolute names, ``..`` components, symlink members and size/count
overruns are rejected (zip-slip / tar-slip / zip-bomb guards).
"""

from __future__ import annotations

import gzip
import hashlib
import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .safepaths import PathViolation, safe_member_path

CHUNK = 1024 * 1024


class ExtractLimit(RuntimeError):
    pass


def sha256_file(path: Path, progress=None) -> str:
    h = hashlib.sha256()
    done = 0
    total = path.stat().st_size or 1
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(CHUNK)
            if not buf:
                break
            h.update(buf)
            done += len(buf)
            if progress and done % (64 * CHUNK) < CHUNK:
                progress(done / total)
    return h.hexdigest()


def verify_archive(path: Path) -> tuple[bool, str]:
    """Full read with CRC / gzip checks. Streams; never loads the archive in memory."""
    low = path.name.lower()
    try:
        if low.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
                if bad:
                    return False, f"CRC mismatch in member {bad}"
                return True, f"zip ok ({len(zf.infolist())} members)"
        if low.endswith(".unitypackage") or low.endswith((".tar.gz", ".tgz")):
            n = 0
            with gzip.open(path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
                for m in tar:
                    n += 1
                    if m.isfile():
                        fh = tar.extractfile(m)
                        if fh is not None:
                            while fh.read(CHUNK):
                                pass
            return True, f"tar.gz ok ({n} members)"
        return False, "unsupported archive type"
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


@dataclass
class ExtractReport:
    dest: str
    files: int = 0
    bytes: int = 0
    skipped: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    nested_unitypackages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dest": self.dest,
            "files": self.files,
            "bytes": self.bytes,
            "skipped": self.skipped[:50],
            "rejected": self.rejected[:50],
            "nested_unitypackages": self.nested_unitypackages,
        }


def _copy_limited(src, dst_path: Path, report: ExtractReport, max_bytes: int) -> None:
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_path.with_name(dst_path.name + ".part")
    with open(tmp, "wb") as out:
        while True:
            buf = src.read(CHUNK)
            if not buf:
                break
            report.bytes += len(buf)
            if report.bytes > max_bytes:
                out.close()
                tmp.unlink(missing_ok=True)
                raise ExtractLimit(f"extraction exceeds {max_bytes} bytes")
            out.write(buf)
    tmp.replace(dst_path)


def extract_zip(
    path: Path,
    dest: Path,
    *,
    max_bytes: int,
    max_files: int,
    divert_suffixes: tuple[str, ...] = (".unitypackage",),
    divert_dir: Path | None = None,
    progress=None,
) -> ExtractReport:
    """Extract ``path`` into ``dest``. Members ending in ``divert_suffixes``
    go to ``divert_dir`` instead (used to unpack nested .unitypackage files
    without keeping a second copy in the library)."""
    dest.mkdir(parents=True, exist_ok=True)
    report = ExtractReport(dest=str(dest))
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise ExtractLimit(f"{len(infos)} members > limit {max_files}")
        declared = sum(i.file_size for i in infos)
        if declared > max_bytes:
            raise ExtractLimit(f"declared size {declared} > limit {max_bytes}")
        for idx, info in enumerate(infos):
            name = info.filename
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                report.rejected.append(f"{name} (symlink)")
                continue
            if info.is_dir():
                continue
            if "__MACOSX/" in name or Path(name).name in (".DS_Store", "Thumbs.db"):
                report.skipped.append(name)
                continue
            target_root = dest
            if divert_dir is not None and name.lower().endswith(divert_suffixes):
                target_root = divert_dir
                target_root.mkdir(parents=True, exist_ok=True)
            try:
                target = safe_member_path(target_root, name)
            except PathViolation as exc:
                report.rejected.append(str(exc))
                continue
            with zf.open(info) as src:
                _copy_limited(src, target, report, max_bytes)
            if target_root is not dest:
                report.nested_unitypackages.append(str(target))
            else:
                report.files += 1
            if progress and idx % 200 == 0:
                progress(idx / max(1, len(infos)))
    return report


def _unitypackage_pathnames(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with gzip.open(path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        for m in tar:
            parts = [p for p in m.name.replace("\\", "/").lstrip("./").split("/") if p]
            if len(parts) == 2 and parts[1] == "pathname" and m.isfile():
                fh = tar.extractfile(m)
                if fh is not None:
                    text = fh.read(4096).decode("utf-8", "replace").splitlines()
                    if text:
                        out[parts[0]] = text[0].strip()
    return out


def extract_unitypackage(
    path: Path,
    dest: Path,
    previews_dest: Path,
    *,
    max_bytes: int,
    max_files: int,
    progress=None,
) -> ExtractReport:
    """Rebuild the Unity project tree (``Assets/...`` + ``.meta``) from a
    .unitypackage without the Unity Editor. ``preview.png`` thumbnails go to
    ``previews_dest/<asset stem>.png``."""
    dest.mkdir(parents=True, exist_ok=True)
    previews_dest.mkdir(parents=True, exist_ok=True)
    report = ExtractReport(dest=str(dest))
    names = _unitypackage_pathnames(path)
    if len(names) > max_files:
        raise ExtractLimit(f"{len(names)} assets > limit {max_files}")
    done = 0
    with gzip.open(path, "rb") as gz, tarfile.open(fileobj=gz, mode="r|") as tar:
        for m in tar:
            if not m.isfile():
                if m.issym() or m.islnk() or m.isdev():
                    report.rejected.append(f"{m.name} (link/device)")
                continue
            parts = [p for p in m.name.replace("\\", "/").lstrip("./").split("/") if p]
            if len(parts) != 2:
                continue
            guid, leaf = parts
            pathname = names.get(guid)
            if not pathname:
                continue
            try:
                if leaf == "asset":
                    target = safe_member_path(dest, pathname)
                elif leaf == "asset.meta":
                    target = safe_member_path(dest, pathname + ".meta")
                elif leaf.lower() == "preview.png":
                    stem = Path(pathname.replace("\\", "/")).stem
                    target = safe_member_path(previews_dest, f"{stem}.png")
                else:
                    continue
            except PathViolation as exc:
                report.rejected.append(str(exc))
                continue
            fh = tar.extractfile(m)
            if fh is None:
                continue
            _copy_limited(fh, target, report, max_bytes)
            if leaf == "asset":
                report.files += 1
            done += 1
            if progress and done % 500 == 0:
                progress(min(0.99, done / max(1, 3 * len(names))))
    return report


def classify_tree(root: Path) -> str:
    """unity | source | godot | unreal | other, by content."""
    counts = {"fbx": 0, "uasset": 0, "tscn": 0, "prefab": 0, "materiallist": 0}
    for i, p in enumerate(root.rglob("*")):
        if i > 200_000:
            break
        suf = p.suffix.lower()
        if suf == ".fbx":
            counts["fbx"] += 1
        elif suf == ".uasset":
            counts["uasset"] += 1
        elif suf in (".tscn", ".godot"):
            counts["tscn"] += 1
        elif suf == ".prefab":
            counts["prefab"] += 1
        elif p.name.startswith("MaterialList") and suf == ".txt":
            counts["materiallist"] += 1
    if counts["prefab"]:
        return "unity"
    if counts["uasset"]:
        return "unreal"
    if counts["tscn"]:
        return "godot"
    if counts["fbx"] or counts["materiallist"]:
        return "source"
    return "other"


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
