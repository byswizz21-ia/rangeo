"""Exécution d'un plan : transactionnelle, reprenable, annulable."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import fileops, hashing, journal as jn, trash as trash_mod
from .fsutil import (
    disk_kind,
    existing_ancestor,
    free_space,
    is_device_gone_error,
    is_disk_full_error,
    is_locked_error,
    is_within,
    nkey,
    set_process_low_priority,
    set_thread_background,
    volume_id,
    volume_root,
    winerror,
    xp,
)
from .index import MetaIndex
from .models import (
    OP_COPY,
    OP_HARDLINK,
    OP_MKDIR,
    OP_MOVE,
    OP_RMDIR,
    OP_TRASH,
    ST_DONE,
    ST_FAILED,
    ST_PLANNED,
    ST_RUNNING,
    Cancelled,
    DeviceGone,
    Plan,
    TaskControl,
)

log = logging.getLogger(__name__)


class DiskFull(Exception):
    pass


def error_code(exc: BaseException) -> str:
    if isinstance(exc, fileops.VerifyError):
        return str(exc) or "verify_failed"
    if isinstance(exc, FileNotFoundError):
        return "source_missing"
    if isinstance(exc, FileExistsError):
        return "target_exists"
    if is_locked_error(exc):
        return "locked"
    if isinstance(exc, PermissionError) or winerror(exc) == 5:
        return "access_denied"
    if is_disk_full_error(exc):
        return "disk_full"
    if is_device_gone_error(exc):
        return "device_gone"
    if winerror(exc) in (206, 111) or getattr(exc, "errno", None) == 36:
        return "path_too_long"
    return "other"


@dataclass
class ExecOptions:
    cleanup_empty: bool = True
    low_priority: bool = False
    threads: int | None = None
    lock_retries: int = 3
    lock_delay: float = 3.0


@dataclass
class ExecResult:
    session_id: int
    status: str = jn.S_COMPLETED
    message: str = "exec.done"
    done: int = 0
    failed: int = 0
    skipped: int = 0
    bytes: int = 0
    duration: float = 0.0
    removed_dirs: int = 0
    files: int = 0  # fichiers traités (hors dossiers créés)
    errors: list[tuple[str, str, str]] = field(default_factory=list)  # (chemin, code, détail)

    def summary(self) -> dict:
        return {
            "done": self.done,
            "files": self.files,
            "failed": self.failed,
            "skipped": self.skipped,
            "bytes": self.bytes,
            "duration": round(self.duration, 1),
            "removed_dirs": self.removed_dirs,
            "message": self.message,
            "errors": [list(e) for e in self.errors[:500]],
        }


class Executor:
    def __init__(self, journal: jn.Journal, control: TaskControl | None = None,
                 options: ExecOptions | None = None, index: MetaIndex | None = None):
        self.journal = journal
        self.control = control or TaskControl()
        self.opt = options or ExecOptions()
        self.index = index
        self._dir_lock = threading.Lock()
        self._created: set[str] = set()
        self._ready_dirs: set[str] = set()
        self._vol_lock = threading.Lock()
        self._vol: dict[str, int | None] = {}
        self._result_lock = threading.Lock()
        self._abort: BaseException | None = None
        self._moved_pairs: list[tuple[str, str]] = []
        self._locked: list[jn.OpRow] = []
        self._session_id = 0
        self._seq_extra = 0

    # ------------------------------------------------------------------ helpers
    def _volume(self, folder: str) -> int | None:
        key = nkey(folder)
        with self._vol_lock:
            if key in self._vol:
                return self._vol[key]
        try:
            vol = volume_id(folder)
        except OSError:
            vol = None
        with self._vol_lock:
            self._vol[key] = vol
        return vol

    def _dst_volume(self, folder: str) -> int | None:
        """Volume d'un dossier de destination (qui n'existe peut-être pas encore), mis en cache."""
        key = "dst:" + nkey(folder)
        with self._vol_lock:
            if key in self._vol:
                return self._vol[key]
        try:
            vol = volume_id(existing_ancestor(folder))
        except OSError:
            vol = None
        with self._vol_lock:
            self._vol[key] = vol
        return vol

    def _same_volume(self, src: str, dst_folder: str) -> bool:
        a = self._volume(os.path.dirname(src))
        b = self._dst_volume(dst_folder)
        return a is not None and a == b

    def _ensure_dir(self, folder: str, seq: int) -> None:
        key = nkey(folder)
        if key in self._ready_dirs:
            return
        if os.path.isdir(xp(folder)):
            self._ready_dirs.add(key)
            return
        missing = []
        current = folder
        while not os.path.exists(xp(current)):
            missing.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        with self._dir_lock:
            for path in reversed(missing):
                key = nkey(path)
                if key in self._created:
                    continue
                try:
                    os.mkdir(xp(path))
                except FileExistsError:
                    continue
                self._created.add(key)
                self.journal.add_op(self._session_id, seq, OP_MKDIR, None, path, is_dir=True, state=ST_DONE)
            self._ready_dirs.add(nkey(folder))

    def _tag(self, row: jn.OpRow) -> str:
        return f"{row.session_id}-{row.id}"

    # ------------------------------------------------------------------ API
    def run_plan(self, plan: Plan, kind: str = "sort", profile: str = "") -> ExecResult:
        """Enregistre tout le plan dans le journal, puis l'exécute."""
        ops = plan.active_ops()
        session_id = self.journal.create_session(kind, profile, plan.sources, plan.dest_root)
        rows = []
        seq = 0
        for folder in plan.extra_dirs:
            rows.append((seq, OP_MKDIR, None, folder, 0, True, "", {"extra": True}))
        for op in ops:
            seq += 1
            extra = {}
            if op.replaces:
                extra["replaces"] = True
            if op.keep_ref:
                extra["keep"] = op.keep_ref
            if op.note:
                extra["note"] = op.note
            if op.entry is not None and op.entry.root:
                extra["root"] = op.entry.root
            rows.append((seq, op.op, op.src, op.dst, op.size, op.is_dir, op.rule, extra))
        self.journal.add_ops(session_id, rows)
        return self.execute_session(session_id)

    def execute_session(self, session_id: int, resume: bool = False, retry_failed: bool = False) -> ExecResult:
        self._session_id = session_id
        start = time.monotonic()
        result = ExecResult(session_id)
        if resume and self.journal.owner_alive(session_id):
            raise RuntimeError("session_in_use")  # un rangement planifié est en train de l'exécuter
        self.journal.claim(session_id)
        session = self.journal.session(session_id)
        states = (ST_PLANNED, ST_RUNNING) + ((ST_FAILED,) if retry_failed else ())
        rows = self.journal.ops(session_id, states)
        self.journal.set_status(session_id, jn.S_RUNNING)
        if self.opt.low_priority:
            set_process_low_priority(True)

        if resume:
            rows = self._reconcile_all(rows, result)

        mkdirs = [r for r in rows if r.op == OP_MKDIR]
        work = [r for r in rows if r.op != OP_MKDIR]
        total_bytes = sum(r.size for r in work if r.op in (OP_MOVE, OP_COPY))
        self.control.set_stage("execute", total=len(work), bytes_total=total_bytes)

        try:
            for r in mkdirs:
                try:
                    os.makedirs(xp(r.dst), exist_ok=True)
                    self.journal.mark(r.id, ST_DONE)
                except OSError as exc:
                    self._fail(r, exc, result)
            self._check_space(work)

            fast, slow = [], []
            for r in work:
                if r.op in (OP_MOVE,) and r.dst and self._same_volume(r.src, os.path.dirname(r.dst)):
                    fast.append(r)
                elif r.op in (OP_TRASH, OP_HARDLINK, OP_RMDIR):
                    fast.append(r)
                else:
                    slow.append(r)
            self._run_pool(fast, 1, result)
            self._run_pool(slow, self._copy_threads(slow), result)
            self._retry_locked(result)
            status, message = (jn.S_COMPLETED_ERRORS, "exec.done_errors") if result.failed else (
                jn.S_COMPLETED, "exec.done")
        except Cancelled:
            status, message = jn.S_CANCELLED, "exec.cancelled"
        except DeviceGone:
            status, message = jn.S_INTERRUPTED, "exec.device_gone"
        except DiskFull:
            status, message = jn.S_INTERRUPTED, "exec.disk_full"
        finally:
            if self.opt.low_priority:
                set_process_low_priority(False)

        if self.opt.cleanup_empty and status != jn.S_INTERRUPTED:
            try:
                result.removed_dirs = self._cleanup_empty(session, session_id)
            except Exception:
                log.exception("Nettoyage des dossiers vides")
        if self.index is not None and self._moved_pairs:
            try:
                self.index.rename(self._moved_pairs)
            except Exception:
                log.exception("Mise à jour de l'index")

        counts = self.journal.counts(session_id)
        result.done = counts.get(ST_DONE, 0)
        result.failed = counts.get(ST_FAILED, 0)
        result.skipped = counts.get("skipped", 0)
        result.files = self.journal.file_count(session_id)
        result.bytes = self.journal.done_bytes(session_id)
        result.status = status
        result.message = message
        result.duration = time.monotonic() - start
        self.journal.set_status(session_id, status, result.summary(), finished=True)
        log.info("Session %s : %s (%d faits, %d erreurs)", session_id, status, result.done, result.failed)
        return result

    # ------------------------------------------------------------------ exécution
    def _copy_threads(self, rows: list[jn.OpRow]) -> int:
        if self.opt.threads:
            return self.opt.threads
        if not rows:
            return 1
        kinds = set()
        for r in rows[:50]:
            for p in (r.src, r.dst):
                if p:
                    try:
                        kinds.add(disk_kind(existing_ancestor(os.path.dirname(p))))
                    except OSError:
                        pass
        if kinds & {"hdd", "removable", "unknown"}:
            return 1  # disque mécanique / USB : une seule copie à la fois (évite les allers-retours de la tête)
        if "network" in kinds:
            return 2
        return 3

    def _run_pool(self, rows: list[jn.OpRow], threads: int, result: ExecResult) -> None:
        if not rows:
            return
        if threads <= 1 or len(rows) == 1:
            for r in rows:
                self._exec(r, result)
            return

        def task(row):
            if self.opt.low_priority:
                set_thread_background(True)
            self._exec(row, result)

        pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="exec")
        try:
            futures = [pool.submit(task, r) for r in rows]
            for f in futures:
                f.result()
        except BaseException as exc:
            if self._abort is None:
                self._abort = exc
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    def _exec(self, row: jn.OpRow, result: ExecResult) -> None:
        if self._abort is not None:
            raise self._abort if isinstance(self._abort, (DeviceGone, DiskFull)) else Cancelled()
        self.control.checkpoint()
        self.control.advance(0, current=row.src or row.dst or "")
        try:
            if row.op == OP_MOVE:
                info, final = self._move(row)
            elif row.op == OP_COPY:
                info, final = self._copy(row)
            elif row.op == OP_TRASH:
                info, final = self._trash(row), None
            elif row.op == OP_HARDLINK:
                info, final = self._hardlink(row), None
            elif row.op == OP_RMDIR:
                os.rmdir(xp(row.src))  # échoue si le dossier n'est plus vide : sans risque
                info, final = {}, None
            else:
                info, final = {}, None
            extra = dict(row.extra)
            extra.update(info)
            self.journal.mark(row.id, ST_DONE, extra=extra, dst=final)
            nbytes = row.size if info.get("mode") != "copy" and row.op in (OP_MOVE,) else 0
            self.control.advance(1, nbytes)
        except Cancelled:
            raise
        except OSError as exc:
            if is_locked_error(exc):
                with self._result_lock:
                    self._locked.append(row)
                self.control.advance(1)
                return
            if is_disk_full_error(exc):
                self._fail(row, exc, result)
                self._abort = DiskFull()
                raise self._abort from exc
            if is_device_gone_error(exc) or not self._roots_alive(row):
                self._fail(row, exc, result)
                self._abort = DeviceGone()
                raise self._abort from exc
            self._fail(row, exc, result)
            self.control.advance(1)
        except Exception as exc:  # erreur inattendue : on la note et on continue
            log.exception("Opération %s en erreur", row.id)
            self._fail(row, exc, result)
            self.control.advance(1)

    def _roots_alive(self, row: jn.OpRow) -> bool:
        for p in (row.src, row.dst):
            if p:
                try:
                    if not os.path.exists(volume_root(p)):
                        return False
                except OSError:
                    return False
        return True

    def _fail(self, row: jn.OpRow, exc: BaseException, result: ExecResult) -> None:
        code = error_code(exc)
        detail = getattr(exc, "strerror", None) or str(exc)
        self.journal.mark(row.id, ST_FAILED, error=f"{code}|{detail}")
        self.control.error()
        with self._result_lock:
            result.errors.append((row.src or row.dst or "", code, detail))

    def _move(self, row: jn.OpRow) -> tuple[dict, str]:
        src, dst = row.src, row.dst
        info: dict = {}
        if row.extra.get("replaces") and not row.is_dir and os.path.isfile(xp(dst)):
            if not os.path.lexists(xp(src)):
                raise FileNotFoundError(2, "source_missing", src)
            info["replaced"] = trash_mod.trash(dst, row.session_id)
        folder = os.path.dirname(dst)
        self._ensure_dir(folder, row.seq)
        same = self._same_volume(src, folder)
        # même disque : on tente directement le renommage (Windows refuse d'écraser un fichier existant) ;
        # entre disques : on vérifie avant, pour ne pas copier inutilement
        final = dst if same else fileops.free_name(dst)
        for attempt in range(50):
            try:
                if row.is_dir:
                    def on_copied(final=final):
                        self.journal.mark(row.id, ST_RUNNING, extra={**row.extra, "phase": "copied", "dst": final},
                                          dst=final, immediate=True)
                    info.update(fileops.move_tree(src, final, same, self._tag(row), self.control, on_copied))
                else:
                    info.update(fileops.move_file(src, final, same, self._tag(row), self.control))
                break
            except FileExistsError:
                if attempt == 49:
                    raise
                final = fileops.free_name(dst)
            except FileNotFoundError:
                if not os.path.lexists(xp(src)):
                    raise FileNotFoundError(2, "source_missing", src) from None
                raise
        if final != dst:
            info["renamed_at_runtime"] = True
        if not row.is_dir:
            with self._result_lock:
                self._moved_pairs.append((src, final))
        return info, final

    def _copy(self, row: jn.OpRow) -> tuple[dict, str]:
        src, dst = row.src, row.dst
        if not os.path.lexists(xp(src)):
            raise FileNotFoundError(2, "source_missing", src)
        folder = os.path.dirname(dst)
        self._ensure_dir(folder, row.seq)
        final = fileops.free_name(dst)
        if row.is_dir:
            count = fileops.copy_tree_to(src, final, self._tag(row), self.control)
            return {"mode": "copy", "files": count}, final
        digest = fileops.copy_file_verified(src, final, self._tag(row), self.control)
        return {"mode": "copy", "hash": digest}, final

    def _trash(self, row: jn.OpRow) -> dict:
        if not os.path.lexists(xp(row.src)):
            raise FileNotFoundError(2, "source_missing", row.src)
        return trash_mod.trash(row.src, row.session_id, row.size if not row.is_dir else None)

    def _hardlink(self, row: jn.OpRow) -> dict:
        keep = row.extra.get("keep") or row.dst
        if fileops.is_same_file(row.src, keep):
            return {"note": "already_linked"}
        fileops.replace_with_hardlink(row.src, keep, self._tag(row))
        return {"mode": "hardlink"}

    def _retry_locked(self, result: ExecResult) -> None:
        for attempt in range(self.opt.lock_retries):
            if not self._locked:
                return
            waiting, self._locked = self._locked, []
            deadline = time.monotonic() + self.opt.lock_delay * (attempt + 1)
            while time.monotonic() < deadline:
                self.control.checkpoint()
                time.sleep(0.2)
            self.control.add_total(len(waiting))
            for row in waiting:
                self._exec(row, result)
        for row in self._locked:
            self.journal.mark(row.id, ST_FAILED, error="locked|in_use")
            self.control.error()
            result.errors.append((row.src or "", "locked", "in_use"))
        self._locked = []

    def _check_space(self, rows: list[jn.OpRow]) -> None:
        need: dict[int, tuple[int, str]] = {}
        for r in rows:
            if r.op not in (OP_MOVE, OP_COPY) or not r.dst:
                continue
            folder = os.path.dirname(r.dst)
            if r.op == OP_MOVE and self._same_volume(r.src, folder):
                continue
            vol = self._dst_volume(folder)
            if vol is None:
                continue
            total, path = need.get(vol, (0, folder))
            need[vol] = (total + r.size, path)
        for vol, (nbytes, path) in need.items():
            try:
                if nbytes + 256 * 1024 * 1024 > free_space(path):
                    raise DiskFull()
            except OSError:
                continue

    # ------------------------------------------------------------------ reprise
    def _reconcile_all(self, rows: list[jn.OpRow], result: ExecResult) -> list[jn.OpRow]:
        remaining = []
        for r in rows:
            try:
                if not self._reconcile(r):
                    remaining.append(r)
            except OSError as exc:
                self._fail(r, exc, result)
        self.journal.flush()
        return remaining

    def _reconcile(self, row: jn.OpRow) -> bool:
        """Vérifie sur le disque si une opération interrompue a déjà été faite (True = terminée)."""
        tag = self._tag(row)
        if row.dst:
            tmp = fileops.temp_name(os.path.dirname(row.dst), tag)
            phase_copied = row.extra.get("phase") == "copied"
            if os.path.lexists(xp(tmp)) and not (row.is_dir and phase_copied):
                if os.path.isdir(xp(tmp)):
                    fileops.remove_tree(tmp, ignore_errors=True)
                else:
                    fileops.remove_file(tmp)
        src_e = bool(row.src) and os.path.lexists(xp(row.src))
        dst = row.extra.get("dst") or row.dst
        dst_e = bool(dst) and os.path.lexists(xp(dst))
        if row.op == OP_MOVE:
            if row.is_dir and row.extra.get("phase") == "copied":
                tmp = fileops.temp_name(os.path.dirname(dst), tag)
                if os.path.lexists(xp(tmp)) and not dst_e:
                    os.rename(xp(tmp), xp(dst))
                    dst_e = True
                if dst_e and src_e:
                    fileops.remove_tree(row.src)
                if dst_e:
                    self.journal.mark(row.id, ST_DONE, extra={**row.extra, "note": "recovered"}, dst=dst)
                    return True
                return False
            if not src_e and dst_e:
                self.journal.mark(row.id, ST_DONE, extra={**row.extra, "note": "recovered"})
                return True
            if src_e and dst_e and not row.is_dir:
                st_s, st_d = os.stat(xp(row.src)), os.stat(xp(dst))
                if st_s.st_size == st_d.st_size and hashing.full_hash(row.src) == hashing.full_hash(dst) \
                        and not fileops.is_same_file(row.src, dst):
                    fileops.remove_file(row.src)  # la copie était complète et vérifiée
                    self.journal.mark(row.id, ST_DONE, extra={**row.extra, "note": "recovered"})
                    return True
            return False
        if row.op == OP_COPY:
            if src_e and dst_e and not row.is_dir:
                if hashing.full_hash(row.src) == hashing.full_hash(dst):
                    self.journal.mark(row.id, ST_DONE, extra={**row.extra, "note": "recovered"})
                    return True
            return False
        if row.op == OP_TRASH:
            if not src_e:
                self.journal.mark(row.id, ST_DONE, extra={**row.extra, "note": "recovered"})
                return True
            return False
        if row.op == OP_HARDLINK:
            keep = row.extra.get("keep") or row.dst
            if fileops.is_same_file(row.src, keep):
                self.journal.mark(row.id, ST_DONE, extra={**row.extra, "note": "recovered"})
                return True
            return False
        if row.op == OP_RMDIR:
            if not os.path.lexists(xp(row.src)):
                self.journal.mark(row.id, ST_DONE, extra={**row.extra, "note": "recovered"})
                return True
            return False
        return False

    # ------------------------------------------------------------------ nettoyage
    def _cleanup_empty(self, session: jn.SessionRow | None, session_id: int) -> int:
        rows = self.journal.ops(session_id, (ST_DONE,))
        roots = [r for r in (session.sources if session else []) if r]
        stops = {nkey(r) for r in roots}
        candidates: set[str] = set()
        for r in rows:
            if r.op in (OP_MOVE, OP_TRASH) and r.src:
                candidates.add(os.path.dirname(r.src))
        removed = 0
        seq = max((r.seq for r in rows), default=0) + 1
        for folder in sorted(candidates, key=len, reverse=True):
            current = folder
            while current and nkey(current) not in stops and any(is_within(current, root) for root in roots):
                try:
                    os.rmdir(xp(current))  # échoue si le dossier n'est pas vide : sans risque
                except OSError:
                    break
                self.journal.add_op(session_id, seq, OP_RMDIR, current, None, is_dir=True, state=ST_DONE)
                removed += 1
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent
        return removed
