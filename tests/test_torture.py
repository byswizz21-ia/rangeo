"""Tests de torture : arborescences aléatoires, noms piégeux, coupures à des moments aléatoires.

Invariant vérifié à chaque fois : aucun octet perdu, aucun fichier dupliqué, et l'annulation remet
exactement l'état de départ.
"""

import os
import random

import pytest

import dataset
from conftest import snapshot
from rangeo.core import fileops
from rangeo.core.engine import SortRequest
from rangeo.core.executor import ExecOptions, Executor
from rangeo.core.models import TaskControl

NASTY = [
    "été à Noël", "naïve café", "日本語の写真", "emoji 🎉🌞", "espaces   multiples", " début espace",
    "point.final.", "tiret - long – —", "apostrophe l'été", "guillemets «ok»", "(parenthèses) [crochets]",
    "pourcent 100%", "dièse #1", "esperluette & co", "plus+moins", "virgule, point-virgule;", "a" * 120,
    "Copie de photo", "photo (1)", "photo - Copie", "MAJUSCULES", "minuscules", "CamelCase",
]
EXTS = ["jpg", "png", "pdf", "docx", "txt", "mp3", "mp4", "zip", "xyz", "", "JPG", "Pdf", "tar.gz", "heic"]


def lp(path) -> str:
    path = os.path.abspath(str(path))
    return "\\\\?\\" + path


def build(root, seed: int, n_files: int = 120) -> None:
    rnd = random.Random(seed)
    dirs = [root]
    for _ in range(15):
        parent = rnd.choice(dirs)
        name = rnd.choice(NASTY).strip(" .") or "x"
        d = os.path.join(parent, name + (f" {rnd.randint(0, 9)}" if rnd.random() < 0.5 else ""))
        os.makedirs(lp(d), exist_ok=True)
        dirs.append(d)
    payloads = {
        "jpg": lambda i: dataset.jpeg_bytes(color=(i % 255, 40, 90), date=f"20{10 + i % 14:02d}:0{1 + i % 9}:1{i % 9} 10:00:00"),
        "png": lambda i: dataset.png_bytes((8 + i % 5, 8)),
        "pdf": lambda i: dataset.pdf_bytes(str(i)),
        "mp3": lambda i: dataset.mp3_bytes(2),
    }
    for i in range(n_files):
        folder = rnd.choice(dirs)
        stem = rnd.choice(NASTY).strip() or "f"
        ext = rnd.choice(EXTS)
        name = f"{stem}{i % 7 if rnd.random() < 0.3 else ''}" + (f".{ext}" if ext else "")
        path = os.path.join(folder, name)
        if os.path.exists(lp(path)):
            continue
        data = payloads.get(ext.lower(), lambda i: os.urandom(64 + i))(i) if rnd.random() < 0.8 else b"meme contenu"
        with open(lp(path), "wb") as fh:
            fh.write(data)
        if rnd.random() < 0.05:
            os.chmod(lp(path), 0o444)


def all_content(*roots) -> list:
    out = []
    for r in roots:
        if os.path.exists(r):
            out += list(snapshot(r).values())
    return sorted(out)


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("cross_volume", [False, True])
def test_sort_undo_roundtrip(engine, tmp_path, monkeypatch, seed, cross_volume):
    if cross_volume:
        monkeypatch.setattr(Executor, "_same_volume", lambda self, a, b: False)
    src, dst = tmp_path / "src", tmp_path / "dst"
    build(str(src), seed)
    before = snapshot(src)
    req = SortRequest(sources=[str(src)], profile=engine.config.profile("general"), destination=str(dst))
    _scan, plan = engine.analyze(req, TaskControl())
    result = engine.execute(plan, TaskControl())
    assert result.failed == 0, result.errors
    assert all_content(src, dst) == sorted(before.values())
    undo = engine.undo(result.session_id, TaskControl())
    assert undo.failed == 0, undo.errors
    assert snapshot(src) == before
    assert not os.path.exists(dst) or snapshot(dst) == {}


@pytest.mark.parametrize("seed", [11, 12, 13, 14])
def test_random_crash_then_resume_or_undo(engine, tmp_path, monkeypatch, seed):
    rnd = random.Random(seed)
    cross = rnd.random() < 0.5
    if cross:
        monkeypatch.setattr(Executor, "_same_volume", lambda self, a, b: False)
    src, dst = tmp_path / "src", tmp_path / "dst"
    build(str(src), seed, 80)
    before = snapshot(src)
    req = SortRequest(sources=[str(src)], profile=engine.config.profile("general"), destination=str(dst))
    _scan, plan = engine.analyze(req, TaskControl())
    n_ops = len(plan.active_ops())
    crash_at = rnd.randint(1, max(1, n_ops - 1))
    target = rnd.choice(["move_file", "remove_file", "copy_file_verified"])
    real = getattr(fileops, target)
    calls = {"n": 0}

    def crashing(*a, **kw):
        calls["n"] += 1
        if calls["n"] == crash_at:
            engine.journal._pending.clear()  # écritures du journal perdues dans la coupure
            raise KeyboardInterrupt("coupure")
        return real(*a, **kw)

    monkeypatch.setattr(fileops, target, crashing)
    try:
        Executor(engine.journal, TaskControl(), ExecOptions(threads=1)).run_plan(plan)
    except KeyboardInterrupt:
        pass
    monkeypatch.setattr(fileops, target, real)
    # aucune perte, même au milieu d'une coupure (un fichier peut exister en 2 exemplaires
    # identiques uniquement pendant une copie entre disques, avant suppression de l'original)
    assert set(all_content(src, dst)) == set(before.values())
    unfinished = engine.journal.unfinished_sessions()
    if not unfinished:
        return
    sid = unfinished[0].id
    if rnd.random() < 0.5:
        result = engine.resume(sid, TaskControl())
        assert result.failed == 0, result.errors
        assert all_content(src, dst) == sorted(before.values())
    else:
        undo = engine.undo(sid, TaskControl())
        assert undo.failed == 0, undo.errors
        assert snapshot(src) == before


def test_in_place_twice_then_undo_both(engine, tmp_path):
    src = tmp_path / "src"
    build(str(src), 42)
    before = snapshot(src)
    req = SortRequest(sources=[str(src)], profile=engine.config.profile("general"), in_place=True)
    _s, plan1 = engine.analyze(req, TaskControl())
    r1 = engine.execute(plan1, TaskControl())
    _s, plan2 = engine.analyze(req, TaskControl())
    r2 = engine.execute(plan2, TaskControl())
    assert r1.failed == 0 and r2.failed == 0
    assert all_content(src) == sorted(before.values())
    engine.undo(r2.session_id, TaskControl())
    engine.undo(r1.session_id, TaskControl())
    assert snapshot(src) == before
