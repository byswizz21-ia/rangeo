"""Annulation d'une session (même après redémarrage de l'ordinateur)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from . import fileops, journal as jn, trash as trash_mod
from .fsutil import existing_ancestor, volume_id, xp
from .models import (
    OP_COPY,
    OP_HARDLINK,
    OP_MKDIR,
    OP_MOVE,
    OP_RMDIR,
    OP_TRASH,
    ST_DONE,
    ST_UNDO_FAILED,
    ST_UNDONE,
    Cancelled,
    TaskControl,
)

log = logging.getLogger(__name__)


@dataclass
class UndoResult:
    undone: int = 0
    failed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    status: str = jn.S_UNDONE


_vol_cache: dict[str, int | None] = {}
_ready: set[str] = set()


def _volume(folder: str, existing: bool) -> int | None:
    key = ("e:" if existing else "d:") + os.path.normcase(folder)
    if key not in _vol_cache:
        try:
            _vol_cache[key] = volume_id(folder if existing else existing_ancestor(folder))
        except OSError:
            _vol_cache[key] = None
    return _vol_cache[key]


def _same_volume(a: str, b_folder: str) -> bool:
    va = _volume(os.path.dirname(a), True)
    return va is not None and va == _volume(b_folder, False)


def _move_back(current: str, original: str, is_dir: bool, tag: str, control: TaskControl) -> str:
    """Remet un élément à sa place d'origine (renommé « (2) » si la place est prise)."""
    folder = os.path.dirname(original)
    key = os.path.normcase(folder)
    if key not in _ready:
        os.makedirs(xp(folder), exist_ok=True)
        _ready.add(key)
    same = _same_volume(current, folder)
    target = original if same else fileops.free_name(original)
    for _ in range(50):
        try:
            if is_dir:
                fileops.move_tree(current, target, same, tag, control)
            else:
                fileops.move_file(current, target, same, tag, control)
            return target
        except FileExistsError:
            target = fileops.free_name(original)
    raise FileExistsError(17, "target_exists", original)


def undo_session(journal: jn.Journal, session_id: int, control: TaskControl | None = None,
                 only_ids: set[int] | None = None) -> UndoResult:
    """Annule les opérations terminées d'une session, de la plus récente à la plus ancienne."""
    control = control or TaskControl()
    result = UndoResult()
    _vol_cache.clear()
    _ready.clear()
    rows = journal.ops(session_id, (ST_DONE,)) + _effectively_done(journal, session_id)
    if only_ids is not None:
        rows = [r for r in rows if r.id in only_ids]
    rows.sort(key=lambda r: (r.seq, r.id), reverse=True)
    main = [r for r in rows if r.op not in (OP_MKDIR, OP_RMDIR)]
    mkdirs = [r for r in rows if r.op == OP_MKDIR]
    rmdirs = [r for r in rows if r.op == OP_RMDIR]
    control.set_stage("undo", total=len(main), bytes_total=0)

    # 1) dossiers supprimés car vides : on les recrée d'abord
    for r in rmdirs:
        try:
            os.makedirs(xp(r.src), exist_ok=True)
            journal.mark(r.id, ST_UNDONE)
        except OSError as exc:
            journal.mark(r.id, ST_UNDO_FAILED, error=str(exc))

    try:
        for r in main:
            control.checkpoint()
            control.advance(0, current=r.dst or r.src or "")
            tag = f"u{r.session_id}-{r.id}"
            try:
                _undo_one(r, tag, control)
                journal.mark(r.id, ST_UNDONE)
                result.undone += 1
            except Cancelled:
                raise
            except Exception as exc:
                code = getattr(exc, "strerror", None) or str(exc)
                log.warning("Annulation impossible (op %s) : %s", r.id, code)
                journal.mark(r.id, ST_UNDO_FAILED, error=code)
                result.failed += 1
                result.errors.append((r.src or r.dst or "", code))
                control.error()
            control.advance(1)
    except Cancelled:
        result.status = jn.S_PARTIAL_UNDO
        journal.flush()
        journal.set_status(session_id, jn.S_PARTIAL_UNDO)
        return result

    # 2) dossiers créés par la session : supprimés s'ils sont restés vides
    for r in sorted(mkdirs, key=lambda x: len(x.dst or ""), reverse=True):
        try:
            os.rmdir(xp(r.dst))
            journal.mark(r.id, ST_UNDONE)
        except OSError:
            journal.mark(r.id, ST_UNDONE, extra={**r.extra, "kept": "not_empty"})

    journal.flush()
    remaining = journal.counts(session_id).get(ST_DONE, 0)
    result.status = jn.S_UNDONE if result.failed == 0 and remaining == 0 else jn.S_PARTIAL_UNDO
    session = journal.session(session_id)
    summary = dict(session.summary) if session else {}
    summary["undo"] = {"undone": result.undone, "failed": result.failed}
    journal.set_status(session_id, result.status, summary)
    return result


def _effectively_done(journal: jn.Journal, session_id: int) -> list[jn.OpRow]:
    """Opérations notées « à faire » mais réellement effectuées (coupure avant l'écriture du journal)."""
    out = []
    for r in journal.ops(session_id, ("planned", "running")):
        dst = r.extra.get("dst") or r.dst
        if r.op == OP_MOVE and r.src and dst and not os.path.lexists(xp(r.src)) and os.path.lexists(xp(dst)):
            r.dst = dst
            journal.mark(r.id, ST_DONE, dst=dst)
            out.append(r)
        elif r.op == OP_TRASH and r.src and not os.path.lexists(xp(r.src)):
            r.extra.setdefault("method", "recycle")
            r.extra.setdefault("time", 0)
            journal.mark(r.id, ST_DONE, extra=r.extra)
            out.append(r)
    return out


def _undo_one(r: jn.OpRow, tag: str, control: TaskControl) -> None:
    if r.op == OP_MOVE:
        current = r.dst
        if not os.path.lexists(xp(current)):
            if os.path.lexists(xp(r.src)):
                return  # déjà revenu à sa place
            raise FileNotFoundError(2, "moved_file_missing", current)
        _move_back(current, r.src, r.is_dir, tag, control)
        replaced = r.extra.get("replaced")
        if replaced:
            trash_mod.restore(r.dst, replaced)
        return
    if r.op == OP_COPY:
        if not os.path.lexists(xp(r.dst)):
            return
        if os.path.lexists(xp(r.src)):
            trash_mod.trash(r.dst, r.session_id)  # la copie est redondante : à la corbeille
        else:
            _move_back(r.dst, r.src, r.is_dir, tag, control)
        return
    if r.op == OP_TRASH:
        if os.path.lexists(xp(r.src)) and r.extra.get("method") != "quarantine":
            return
        trash_mod.restore(r.src, r.extra)
        return
    if r.op == OP_HARDLINK:
        keep = r.extra.get("keep") or r.dst
        if fileops.is_same_file(r.src, keep):
            fileops.unlink_hardlink(r.src, keep, tag)
        return


def restore_paths_preview(journal: jn.Journal, session_id: int) -> list[tuple[str, str]]:
    """Pour l'interface : (emplacement actuel, emplacement d'origine)."""
    out = []
    for r in journal.ops(session_id, (ST_DONE,)):
        if r.op == OP_MOVE:
            out.append((r.dst, r.src))
        elif r.op == OP_TRASH:
            out.append((r.extra.get("qpath") or "Corbeille", r.src))
    return out


__all__ = ["undo_session", "UndoResult", "restore_paths_preview"]
