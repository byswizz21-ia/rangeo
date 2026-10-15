"""Index persistant (SQLite) : évite de relire ce qui n'a pas changé.

Pour chaque fichier déjà analysé on garde sa taille et sa date de
modification ; si elles n'ont pas bougé, les métadonnées (EXIF, tags) et les
empreintes sont reprises du cache au lieu de relire le fichier.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from . import appdirs
from .fsutil import nkey

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    key TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime INTEGER NOT NULL,
    real_ext TEXT,
    meta TEXT,
    phash TEXT,
    fhash TEXT,
    seen REAL NOT NULL
) WITHOUT ROWID;
"""


def _mt(mtime: float) -> int:
    return int(round(mtime * 1_000_000))


class MetaIndex:
    BATCH = 500

    def __init__(self, path: str | Path | None = None):
        self.path = str(path or appdirs.data_dir() / "index.db")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(files)")}
        if "sig" not in columns:  # V2 : empreintes des doublons ressemblants
            self._conn.execute("ALTER TABLE files ADD COLUMN sig TEXT")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def lookup(self, items: list[tuple[str, int, float]]) -> dict[str, tuple]:
        """items = [(chemin, taille, mtime)] → {clé: (real_ext, meta, phash, fhash)} pour les entrées à jour."""
        out: dict[str, tuple] = {}
        wanted = {nkey(p): (s, _mt(m)) for p, s, m in items}
        keys = list(wanted)
        with self._lock:
            for i in range(0, len(keys), self.BATCH):
                chunk = keys[i:i + self.BATCH]
                marks = ",".join("?" * len(chunk))
                rows = self._conn.execute(
                    f"SELECT key, size, mtime, real_ext, meta, phash, fhash FROM files WHERE key IN ({marks})", chunk
                ).fetchall()
                for key, size, mtime, real_ext, meta, phash, fhash in rows:
                    if wanted.get(key) == (size, mtime):
                        try:
                            meta_obj = json.loads(meta) if meta else None
                        except ValueError:
                            meta_obj = None
                        out[key] = (real_ext, meta_obj, phash, fhash)
        return out

    def store_meta(self, rows: list[tuple[str, int, float, str | None, dict | None]]) -> None:
        """rows = [(chemin, taille, mtime, real_ext, meta)] (les empreintes existantes sont conservées si inchangé)."""
        now = time.time()
        data = [
            (nkey(p), s, _mt(m), real_ext or "", json.dumps(meta, ensure_ascii=False) if meta is not None else None, now)
            for p, s, m, real_ext, meta in rows
        ]
        with self._lock:
            with self._conn:
                self._conn.executemany(
                    """INSERT INTO files(key, size, mtime, real_ext, meta, seen) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                         phash = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.phash END,
                         fhash = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.fhash END,
                         sig = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.sig END,
                         size = excluded.size, mtime = excluded.mtime, real_ext = excluded.real_ext,
                         meta = excluded.meta, seen = excluded.seen""",
                    data,
                )

    def store_hashes(self, rows: list[tuple[str, int, float, str | None, str | None]]) -> None:
        """rows = [(chemin, taille, mtime, phash, fhash)]."""
        now = time.time()
        data = [(nkey(p), s, _mt(m), ph, fh, now) for p, s, m, ph, fh in rows]
        with self._lock:
            with self._conn:
                self._conn.executemany(
                    """INSERT INTO files(key, size, mtime, phash, fhash, seen) VALUES (?,?,?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                         meta = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.meta END,
                         sig = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.sig END,
                         real_ext = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime
                                         THEN files.real_ext END,
                         phash = COALESCE(excluded.phash, CASE WHEN files.size = excluded.size
                                          AND files.mtime = excluded.mtime THEN files.phash END),
                         fhash = COALESCE(excluded.fhash, CASE WHEN files.size = excluded.size
                                          AND files.mtime = excluded.mtime THEN files.fhash END),
                         size = excluded.size, mtime = excluded.mtime, seen = excluded.seen""",
                    data,
                )

    def lookup_sigs(self, items: list[tuple[str, int, float]]) -> dict[str, str]:
        """Empreintes de ressemblance déjà calculées pour des fichiers inchangés."""
        out: dict[str, str] = {}
        wanted = {nkey(p): (s, _mt(m)) for p, s, m in items}
        keys = list(wanted)
        with self._lock:
            for i in range(0, len(keys), self.BATCH):
                chunk = keys[i:i + self.BATCH]
                marks = ",".join("?" * len(chunk))
                for key, size, mtime, sig in self._conn.execute(
                        f"SELECT key, size, mtime, sig FROM files WHERE key IN ({marks})", chunk):
                    if sig and wanted.get(key) == (size, mtime):
                        out[key] = sig
        return out

    def store_sigs(self, rows: list[tuple[str, int, float, str]]) -> None:
        now = time.time()
        data = [(nkey(p), s, _mt(m), sig, now) for p, s, m, sig in rows]
        with self._lock:
            with self._conn:
                self._conn.executemany(
                    """INSERT INTO files(key, size, mtime, sig, seen) VALUES (?,?,?,?,?)
                       ON CONFLICT(key) DO UPDATE SET
                         meta = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.meta END,
                         real_ext = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime
                                         THEN files.real_ext END,
                         phash = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.phash END,
                         fhash = CASE WHEN files.size = excluded.size AND files.mtime = excluded.mtime THEN files.fhash END,
                         sig = excluded.sig, size = excluded.size, mtime = excluded.mtime, seen = excluded.seen""",
                    data,
                )

    def forget(self, paths: list[str]) -> None:
        with self._lock:
            with self._conn:
                self._conn.executemany("DELETE FROM files WHERE key = ?", [(nkey(p),) for p in paths])

    def rename(self, pairs: list[tuple[str, str]]) -> None:
        """Après un déplacement, la clé change mais le contenu est le même."""
        with self._lock:
            with self._conn:
                for old, new in pairs:
                    self._conn.execute("DELETE FROM files WHERE key = ?", (nkey(new),))
                    self._conn.execute("UPDATE files SET key = ? WHERE key = ?", (nkey(new), nkey(old)))

    def prune(self, older_than_days: int = 180) -> int:
        limit = time.time() - older_than_days * 86400
        with self._lock:
            with self._conn:
                cur = self._conn.execute("DELETE FROM files WHERE seen < ?", (limit,))
                return cur.rowcount

    def clear(self) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM files")
            self._conn.execute("VACUUM")
