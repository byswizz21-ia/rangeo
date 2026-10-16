"""Journal transactionnel (SQLite) : chaque opération est enregistrée AVANT d'être exécutée.

Il sert à :
  * annuler une session complète, même après redémarrage ;
  * reprendre une session interrompue (plantage, coupure de courant) ;
  * afficher l'historique et produire des rapports.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import appdirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created REAL NOT NULL,
    finished REAL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    profile TEXT,
    sources TEXT,
    destination TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    op TEXT NOT NULL,
    src TEXT,
    dst TEXT,
    size INTEGER DEFAULT 0,
    is_dir INTEGER DEFAULT 0,
    state TEXT NOT NULL,
    error TEXT,
    rule TEXT,
    extra TEXT,
    updated REAL
);
CREATE INDEX IF NOT EXISTS ops_session ON operations(session_id, seq);
CREATE INDEX IF NOT EXISTS ops_state ON operations(session_id, state);
"""

# statuts de session
S_RUNNING = "running"
S_PAUSED = "paused"
S_COMPLETED = "completed"
S_COMPLETED_ERRORS = "completed_errors"
S_CANCELLED = "cancelled"
S_INTERRUPTED = "interrupted"
S_UNDONE = "undone"
S_PARTIAL_UNDO = "partially_undone"


@dataclass
class OpRow:
    id: int
    session_id: int
    seq: int
    op: str
    src: str | None
    dst: str | None
    size: int
    is_dir: bool
    state: str
    error: str | None
    rule: str | None
    extra: dict

    @classmethod
    def from_row(cls, row) -> OpRow:
        extra = {}
        if row[11]:
            try:
                extra = json.loads(row[11])
            except ValueError:
                extra = {}
        return cls(row[0], row[1], row[2], row[3], row[4], row[5], row[6] or 0, bool(row[7]), row[8], row[9],
                   row[10], extra)


@dataclass
class SessionRow:
    id: int
    created: float
    finished: float | None
    kind: str
    status: str
    profile: str | None
    sources: list[str]
    destination: str | None
    summary: dict


_OP_COLUMNS = "id, session_id, seq, op, src, dst, size, is_dir, state, error, rule, extra"


class Journal:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or appdirs.data_dir() / "journal.db")
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # FULL : chaque validation est écrite physiquement (résiste aux coupures de courant)
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._pending: list[tuple] = []
        self._last_flush = time.monotonic()

    def close(self) -> None:
        with self._lock:
            self.flush()
            self._conn.close()

    # -- sessions ------------------------------------------------------------------
    def create_session(self, kind: str, profile: str = "", sources: list[str] | None = None,
                       destination: str = "") -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO sessions(created, kind, status, profile, sources, destination, summary) "
                "VALUES (?,?,?,?,?,?,?)",
                (time.time(), kind, S_RUNNING, profile, json.dumps(sources or [], ensure_ascii=False), destination,
                 "{}"),
            )
            return cur.lastrowid

    def set_status(self, session_id: int, status: str, summary: dict | None = None, finished: bool = False) -> None:
        with self._lock:
            self.flush()
            with self._conn:
                if summary is not None:
                    self._conn.execute("UPDATE sessions SET summary = ? WHERE id = ?",
                                       (json.dumps(summary, ensure_ascii=False), session_id))
                self._conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
                if finished:
                    self._conn.execute("UPDATE sessions SET finished = ? WHERE id = ?", (time.time(), session_id))

    def session(self, session_id: int) -> SessionRow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, created, finished, kind, status, profile, sources, destination, summary "
                "FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._session_row(row) if row else None

    @staticmethod
    def _session_row(row) -> SessionRow:
        try:
            sources = json.loads(row[6] or "[]")
        except ValueError:
            sources = []
        try:
            summary = json.loads(row[8] or "{}")
        except ValueError:
            summary = {}
        return SessionRow(row[0], row[1], row[2], row[3], row[4], row[5], sources, row[7], summary)

    def sessions(self, limit: int = 500) -> list[SessionRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created, finished, kind, status, profile, sources, destination, summary "
                "FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._session_row(r) for r in rows]

    def unfinished_sessions(self) -> list[SessionRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created, finished, kind, status, profile, sources, destination, summary "
                "FROM sessions WHERE status IN (?, ?) ORDER BY id", (S_RUNNING, S_PAUSED)
            ).fetchall()
        return [self._session_row(r) for r in rows]

    def delete_session(self, session_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM operations WHERE session_id = ?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # -- opérations ------------------------------------------------------------------
    def add_ops(self, session_id: int, ops: list[tuple]) -> list[int]:
        """ops = [(seq, op, src, dst, size, is_dir, rule, extra_dict)] – écrites en une transaction."""
        now = time.time()
        rows = [
            (session_id, seq, op, src, dst, size, int(is_dir), "planned", rule,
             json.dumps(extra, ensure_ascii=False) if extra else None, now)
            for seq, op, src, dst, size, is_dir, rule, extra in ops
        ]
        with self._lock, self._conn:
            first = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM operations").fetchone()[0]
            self._conn.executemany(
                "INSERT INTO operations(session_id, seq, op, src, dst, size, is_dir, state, rule, extra, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows,
            )
            ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM operations WHERE session_id = ? AND id > ? ORDER BY id", (session_id, first)
            )]
        return ids

    def add_op(self, session_id: int, seq: int, op: str, src: str | None, dst: str | None, size: int = 0,
               is_dir: bool = False, rule: str = "", extra: dict | None = None, state: str = "planned") -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO operations(session_id, seq, op, src, dst, size, is_dir, state, rule, extra, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, seq, op, src, dst, size, int(is_dir), state, rule,
                 json.dumps(extra, ensure_ascii=False) if extra else None, time.time()),
            )
            return cur.lastrowid

    def mark(self, op_id: int, state: str, error: str | None = None, extra: dict | None = None,
             dst: str | None = None, immediate: bool = False) -> None:
        """Change l'état d'une opération. Les écritures sont groupées (flush régulier)."""
        with self._lock:
            self._pending.append((op_id, state, error, json.dumps(extra, ensure_ascii=False) if extra else None,
                                  dst, time.time()))
            # écritures groupées : le plan complet est déjà enregistré de façon sûre, et la reprise
            # vérifie de toute façon l'état réel sur le disque
            if immediate or len(self._pending) >= 2000 or time.monotonic() - self._last_flush > 1.0:
                self.flush(durable=immediate)

    def flush(self, durable: bool = True) -> None:
        with self._lock:
            self._last_flush = time.monotonic()
            if not self._pending:
                return
            pending, self._pending = self._pending, []
            if not durable:
                self._conn.execute("PRAGMA synchronous=NORMAL")
            try:
                with self._conn:
                    self._conn.executemany(
                        "UPDATE operations SET state = ?, error = ?, extra = COALESCE(?, extra), "
                        "dst = COALESCE(?, dst), updated = ? WHERE id = ?",
                        [(s, e, x, d, t, i) for i, s, e, x, d, t in pending],
                    )
            finally:
                if not durable:
                    self._conn.execute("PRAGMA synchronous=FULL")

    def ops(self, session_id: int, states: tuple[str, ...] | None = None) -> list[OpRow]:
        with self._lock:
            self.flush()
            if states:
                marks = ",".join("?" * len(states))
                rows = self._conn.execute(
                    f"SELECT {_OP_COLUMNS} FROM operations WHERE session_id = ? AND state IN ({marks}) ORDER BY seq, id",
                    (session_id, *states),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"SELECT {_OP_COLUMNS} FROM operations WHERE session_id = ? ORDER BY seq, id", (session_id,)
                ).fetchall()
        return [OpRow.from_row(r) for r in rows]

    def counts(self, session_id: int) -> dict[str, int]:
        with self._lock:
            self.flush()
            rows = self._conn.execute(
                "SELECT state, COUNT(*) FROM operations WHERE session_id = ? GROUP BY state", (session_id,)
            ).fetchall()
        return {state: n for state, n in rows}

    def file_count(self, session_id: int) -> int:
        """Nombre d'opérations terminées portant sur des fichiers (hors création de dossiers)."""
        with self._lock:
            self.flush()
            return self._conn.execute(
                "SELECT COUNT(*) FROM operations WHERE session_id = ? AND state = 'done' "
                "AND op IN ('move','copy','trash','hardlink')", (session_id,)
            ).fetchone()[0]

    def done_bytes(self, session_id: int) -> int:
        with self._lock:
            self.flush()
            return self._conn.execute(
                "SELECT COALESCE(SUM(size), 0) FROM operations WHERE session_id = ? AND state = 'done' "
                "AND op IN ('move','copy','trash','hardlink')", (session_id,)
            ).fetchone()[0]

    def monthly(self, months: int = 12) -> list[tuple[str, int, int, int]]:
        """[(« AAAA-MM », fichiers rangés, octets rangés, octets libérés)] des derniers mois."""
        with self._lock:
            self.flush()
            rows = self._conn.execute(
                """SELECT strftime('%Y-%m', s.created, 'unixepoch', 'localtime') AS month,
                          SUM(CASE WHEN o.op IN ('move','copy') AND s.kind != 'duplicates' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN o.op IN ('move','copy') AND s.kind != 'duplicates' THEN o.size ELSE 0 END),
                          SUM(CASE WHEN s.kind IN ('duplicates','cleanup') THEN o.size ELSE 0 END)
                   FROM operations o JOIN sessions s ON s.id = o.session_id
                   WHERE o.state = 'done' AND o.op IN ('move','copy','trash','hardlink')
                   GROUP BY month ORDER BY month DESC LIMIT ?""", (months,)
            ).fetchall()
        return [(m, int(f or 0), int(b or 0), int(fr or 0)) for m, f, b, fr in reversed(rows)]

    def stats(self) -> dict:
        """Totaux pour la page d'accueil."""
        with self._lock:
            self.flush()
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM operations WHERE state = 'done' AND op IN ('move','copy')"
            ).fetchone()
            sessions = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            freed = self._conn.execute(
                "SELECT COALESCE(SUM(size), 0) FROM operations WHERE state = 'done' AND op IN ('trash','hardlink') "
                "AND rule = 'duplicate'"
            ).fetchone()[0]
        return {"files": row[0], "bytes": row[1], "sessions": sessions, "freed": freed}
