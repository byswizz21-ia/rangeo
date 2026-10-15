"""Empreintes de fichiers (xxHash3 128 bits : très rapide, collisions improbables)."""

from __future__ import annotations

import xxhash

from .fsutil import xp
from .models import TaskControl

PARTIAL_BLOCK = 64 * 1024
CHUNK = 4 * 1024 * 1024


def full_hash(path: str, control: TaskControl | None = None, count_bytes: bool = False) -> str:
    h = xxhash.xxh3_128()
    buf = bytearray(CHUNK)
    view = memoryview(buf)
    with open(xp(path), "rb", buffering=0) as fh:
        while True:
            if control is not None:
                control.checkpoint()
            n = fh.readinto(buf)
            if not n:
                break
            h.update(view[:n])
            if control is not None and count_bytes:
                control.add_bytes(n)
    return h.hexdigest()


def partial_hash(path: str, size: int) -> str:
    """Empreinte rapide : début + milieu + fin du fichier (et sa taille)."""
    if size <= PARTIAL_BLOCK * 3:
        return "f:" + full_hash(path)
    h = xxhash.xxh3_128()
    h.update(size.to_bytes(8, "little"))
    with open(xp(path), "rb", buffering=0) as fh:
        for offset in (0, size // 2 - PARTIAL_BLOCK // 2, size - PARTIAL_BLOCK):
            fh.seek(offset)
            h.update(fh.read(PARTIAL_BLOCK))
    return "p:" + h.hexdigest()


def hash_bytes(data: bytes) -> str:
    return xxhash.xxh3_128(data).hexdigest()
