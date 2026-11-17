"""
Where tender files live. NOT in the database: the database only keeps the path.

Files are named by their SHA-256 fingerprint, for example
    documents/ab/ab12cd...ef.pdf
so the same PDF used by 50 tenders is stored only once.

Today: a local folder (DOCUMENTS_DIR, default backend/documents).
Later (AWS): replace LocalStorage with an S3 version that has the same 4 methods.
"""
import hashlib
import os
from pathlib import Path

from ..db import BACKEND_DIR

_EXT_BY_TYPE = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/x-rar-compressed": ".rar",
    "application/vnd.rar": ".rar",
}


def guess_ext(filename=None, content_type=None, content=b""):
    if content.startswith(b"%PDF"):
        return ".pdf"
    if content.startswith(b"PK\x03\x04"):
        name_ext = Path(filename or "").suffix.lower()
        return name_ext if name_ext in {".xlsx", ".docx", ".zip"} else ".zip"
    if content.startswith(b"\xd0\xcf\x11\xe0"):           # old Office (xls/doc)
        return Path(filename or "").suffix.lower() or ".xls"
    if content.startswith(b"Rar!"):
        return ".rar"
    ext = Path(filename or "").suffix.lower()
    if ext and len(ext) <= 5:
        return ext
    return _EXT_BY_TYPE.get((content_type or "").split(";")[0].strip(), ".bin")


class LocalStorage:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv("DOCUMENTS_DIR") or BACKEND_DIR / "documents")
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, content: bytes, ext: str):
        """Store bytes, return (relative_path, sha256). Skips writing if already stored."""
        digest = hashlib.sha256(content).hexdigest()
        rel = f"{digest[:2]}/{digest}{ext}"
        path = self.root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(content)
            tmp.replace(path)                    # never leave half-written files
        return rel, digest

    def path(self, rel):
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:  # block "../../" tricks
            raise ValueError("bad path")
        return p

    def exists(self, rel):
        return bool(rel) and self.path(rel).exists()

    def delete(self, rel):
        p = self.path(rel)
        if p.exists():
            p.unlink()
            return True
        return False

    def total_bytes(self):
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage


def set_storage(storage):
    """Used by tests (and later to plug in S3)."""
    global _storage
    _storage = storage
