"""Opérations élémentaires sûres : déplacement, copie vérifiée, lien physique.

Règle d'or : on ne supprime JAMAIS l'original d'un déplacement entre deux
disques avant d'avoir vérifié (empreinte) que la copie est identique et
physiquement écrite sur le disque.
"""

from __future__ import annotations

import logging
import os

import xxhash

from . import hashing
from .fsutil import clear_readonly, copy_metadata, nkey, unique_name, xp
from .models import Cancelled, TaskControl

log = logging.getLogger(__name__)

CHUNK = 4 * 1024 * 1024


class VerifyError(OSError):
    """La copie ne correspond pas à l'original."""


def temp_name(folder: str, tag: str) -> str:
    return os.path.join(folder, f"~rangeo-{tag}.part")


def free_name(path: str) -> str:
    """Si ``path`` existe déjà, renvoie « nom (2).ext » etc."""
    if not os.path.lexists(xp(path)):
        return path
    folder, name = os.path.split(path)
    try:
        with os.scandir(xp(folder)) as it:
            taken = {nkey(de.name) for de in it}
    except OSError:
        taken = {nkey(name)}
    return os.path.join(folder, unique_name(name, taken))


def _copy_ads_zone(src: str, dst: str) -> None:
    """Recopie la marque « téléchargé depuis Internet » (sécurité SmartScreen)."""
    try:
        with open(xp(src) + ":Zone.Identifier", "rb") as fh:
            data = fh.read(4096)
    except OSError:
        return
    try:
        with open(xp(dst) + ":Zone.Identifier", "wb") as fh:
            fh.write(data)
    except OSError:
        pass


def copy_file_verified(src: str, dst: str, tag: str, control: TaskControl | None = None) -> str:
    """Copie ``src`` vers ``dst`` (qui ne doit pas exister) et vérifie le contenu.

    La copie est écrite dans un fichier temporaire, synchronisée sur disque,
    relue et comparée, puis renommée en ``dst``. Renvoie l'empreinte.
    """
    folder = os.path.dirname(dst)
    tmp = temp_name(folder, tag)
    if os.path.lexists(xp(tmp)):
        os.remove(xp(tmp))
    st = os.stat(xp(src))
    src_hash = xxhash.xxh3_128()
    buf = bytearray(CHUNK)
    view = memoryview(buf)
    try:
        with open(xp(src), "rb", buffering=0) as fin, open(xp(tmp), "xb", buffering=0) as fout:
            while True:
                if control is not None:
                    control.checkpoint()
                n = fin.readinto(buf)
                if not n:
                    break
                chunk = view[:n]
                src_hash.update(chunk)
                written = 0
                while written < n:
                    written += fout.write(chunk[written:])
                if control is not None:
                    control.add_bytes(n)
            os.fsync(fout.fileno())
        digest = src_hash.hexdigest()
        if hashing.full_hash(tmp) != digest:
            raise VerifyError("verify_failed")
        # le fichier source a-t-il changé pendant la copie ?
        st2 = os.stat(xp(src))
        if st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns:
            raise VerifyError("source_changed")
        copy_metadata(st, tmp)
        _copy_ads_zone(src, tmp)
        os.rename(xp(tmp), xp(dst))  # échoue si dst existe : jamais d'écrasement
        return digest
    except BaseException:
        try:
            if os.path.lexists(xp(tmp)):
                clear_readonly(tmp)
                os.remove(xp(tmp))
        except OSError:
            log.warning("Impossible de supprimer le fichier temporaire %s", tmp)
        raise


def remove_file(path: str) -> None:
    clear_readonly(path)
    os.remove(xp(path))


def move_file(src: str, dst: str, same_volume: bool, tag: str, control: TaskControl | None = None) -> dict:
    """Déplace un fichier. ``dst`` ne doit pas exister."""
    if same_volume:
        os.rename(xp(src), xp(dst))
        return {"mode": "rename"}
    digest = copy_file_verified(src, dst, tag, control)
    remove_file(src)
    return {"mode": "copy", "hash": digest}


def _walk_files(root: str):
    """(dossier relatif, [noms de fichiers]) – sans suivre les liens."""
    stack = [""]
    while stack:
        rel = stack.pop()
        current = os.path.join(root, rel) if rel else root
        files = []
        with os.scandir(xp(current)) as it:
            for de in it:
                if de.is_dir(follow_symlinks=False) and not de.is_symlink():
                    stack.append(os.path.join(rel, de.name) if rel else de.name)
                else:
                    files.append(de.name)
        yield rel, files


def copy_tree_verified(src: str, dst: str, tag: str, control: TaskControl | None = None) -> int:
    """Copie un dossier complet en vérifiant chaque fichier. ``dst`` ne doit pas exister."""
    os.makedirs(xp(dst))
    count = 0
    try:
        for rel, files in _walk_files(src):
            target_dir = os.path.join(dst, rel) if rel else dst
            os.makedirs(xp(target_dir), exist_ok=True)
            for i, name in enumerate(files):
                s = os.path.join(src, rel, name) if rel else os.path.join(src, name)
                copy_file_verified(s, os.path.join(target_dir, name), f"{tag}-{count}", control)
                count += 1
        return count
    except BaseException:
        remove_tree(dst, ignore_errors=True)
        raise


def remove_tree(path: str, ignore_errors: bool = False) -> None:
    """Supprime un dossier (utilisé uniquement après une copie vérifiée)."""
    dirs: list[str] = []
    for rel, files in _walk_files(path):
        folder = os.path.join(path, rel) if rel else path
        dirs.append(folder)
        for name in files:
            try:
                remove_file(os.path.join(folder, name))
            except OSError:
                if not ignore_errors:
                    raise
    for folder in sorted(dirs, key=len, reverse=True):
        try:
            clear_readonly(folder)
            os.rmdir(xp(folder))
        except OSError:
            if not ignore_errors:
                raise


def copy_tree_to(src: str, dst: str, tag: str, control: TaskControl | None = None, on_copied=None) -> int:
    """Copie vérifiée d'un dossier dans un dossier temporaire, puis renommage final."""
    tmp = temp_name(os.path.dirname(dst), tag)
    if os.path.lexists(xp(tmp)):
        remove_tree(tmp, ignore_errors=True)
    count = copy_tree_verified(src, tmp, tag, control)
    if on_copied is not None:
        on_copied()  # le journal note que la copie est complète (reprise possible après coupure)
    os.rename(xp(tmp), xp(dst))
    return count


def move_tree(src: str, dst: str, same_volume: bool, tag: str, control: TaskControl | None = None,
              on_copied=None) -> dict:
    if same_volume:
        os.rename(xp(src), xp(dst))
        return {"mode": "rename"}
    count = copy_tree_to(src, dst, tag, control, on_copied)
    remove_tree(src)  # uniquement après la copie complète et vérifiée
    return {"mode": "copy", "files": count}


def replace_with_hardlink(dup: str, keep: str, tag: str) -> None:
    """Remplace ``dup`` par un lien physique vers ``keep`` (contenu identique vérifié juste avant)."""
    if os.stat(xp(dup)).st_size != os.stat(xp(keep)).st_size or hashing.full_hash(dup) != hashing.full_hash(keep):
        raise VerifyError("not_identical")
    folder = os.path.dirname(dup)
    tmp = os.path.join(folder, f"~rangeo-{tag}.lnk")
    if os.path.lexists(xp(tmp)):
        os.remove(xp(tmp))
    os.link(xp(keep), xp(tmp))
    try:
        clear_readonly(dup)
        os.replace(xp(tmp), xp(dup))
    except BaseException:
        try:
            os.remove(xp(tmp))
        except OSError:
            pass
        raise


def unlink_hardlink(path: str, source: str, tag: str) -> None:
    """Annule un lien physique : le remplace par une vraie copie indépendante."""
    folder = os.path.dirname(path)
    tmp_final = os.path.join(folder, f"~rangeo-{tag}.restore")
    if os.path.lexists(xp(tmp_final)):
        os.remove(xp(tmp_final))
    copy_file_verified(source, tmp_final, tag + "r")
    clear_readonly(path)
    os.replace(xp(tmp_final), xp(path))


def is_same_file(a: str, b: str) -> bool:
    try:
        sa, sb = os.stat(xp(a)), os.stat(xp(b))
    except OSError:
        return False
    return sa.st_ino == sb.st_ino and sa.st_dev == sb.st_dev and sa.st_ino != 0


__all__ = [
    "Cancelled",
    "VerifyError",
    "copy_file_verified",
    "copy_tree_verified",
    "free_name",
    "is_same_file",
    "move_file",
    "move_tree",
    "remove_file",
    "remove_tree",
    "replace_with_hardlink",
    "temp_name",
    "unlink_hardlink",
]
