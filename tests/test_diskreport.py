"""Analyse du disque et nettoyage (corbeille, dossiers vides, fichiers abîmés, installateurs obsolètes)."""

import os
import time

import dataset
from conftest import snapshot
from rangeo.core import diskreport
from rangeo.core.models import TaskControl


def make_tree(base):
    root = base / "disque"
    (root / "Gros").mkdir(parents=True)
    (root / "Gros" / "gros.iso").write_bytes(os.urandom(3 * 1024 * 1024))
    vieux = root / "Archives" / "vieux.zip"
    vieux.parent.mkdir()
    vieux.write_bytes(dataset.zip_bytes({"a.txt": os.urandom(2 * 1024 * 1024)}))
    old = time.time() - 6 * 365 * 86400
    os.utime(vieux, (old, old))
    (root / "Temp").mkdir()
    (root / "Temp" / "Thumbs.db").write_bytes(b"x" * 100)
    (root / "Temp" / "travail.tmp").write_bytes(b"x" * 200)
    hier = time.time() - 2 * 86400
    os.utime(root / "Temp" / "travail.tmp", (hier, hier))
    (root / "Temp" / "en_cours.tmp").write_bytes(b"x" * 200)  # récent : peut-être utilisé, pas proposé
    (root / "Temp" / "._photo.jpg").write_bytes(b"x" * 100)
    (root / "Vide" / "Encore plus vide").mkdir(parents=True)
    (root / "Vide2").mkdir()
    photos = root / "Photos"
    photos.mkdir()
    good = dataset.jpeg_bytes((400, 300))
    (photos / "ok.jpg").write_bytes(good)
    (photos / "abimee.jpg").write_bytes(good[: len(good) // 2])
    (photos / "faux.pdf").write_bytes(b"%PDF-1.4 tronque")
    inst = root / "Telechargements"
    inst.mkdir()
    (inst / "vlc-3.0.1-win64.exe").write_bytes(dataset.exe_bytes())
    (inst / "vlc-3.0.20-win64.exe").write_bytes(dataset.exe_bytes())
    (inst / "7z2301-x64.exe").write_bytes(dataset.exe_bytes())
    return root


def test_analysis(tmp_path):
    tmp_path = make_tree(tmp_path)
    report = diskreport.analyze_disk(str(tmp_path), TaskControl())
    assert report.biggest[0].name == "gros.iso"
    assert [f.name for f in report.old] == ["vieux.zip"]
    assert sorted(f.name for f in report.temp) == ["._photo.jpg", "Thumbs.db", "travail.tmp"]
    assert sorted(os.path.basename(p) for p in report.empty_dirs) == ["Vide", "Vide2"]
    assert [f.name for f in report.installers] == ["vlc-3.0.1-win64.exe"]
    assert report.total_files == 12
    assert report.root.size == sum(c.size for c in report.root.children) + report.root.own_size
    assert report.by_group["archive"][0] == 2


def test_corrupt_files(tmp_path):
    tmp_path = make_tree(tmp_path)
    bad = diskreport.find_corrupt(str(tmp_path), TaskControl())
    assert sorted(f.name for f in bad) == ["abimee.jpg", "faux.pdf"]


def test_cleanup_and_undo(engine, tmp_path):
    tmp_path = make_tree(tmp_path)
    before = snapshot(tmp_path)
    dirs_before = sorted(p for p, _d, _f in os.walk(tmp_path))
    report = diskreport.analyze_disk(str(tmp_path), TaskControl())
    plan = diskreport.cleanup_plan(report.temp, report.all_empty_dirs, str(tmp_path))
    result = engine.execute(plan, TaskControl(), kind="cleanup", cleanup=False)
    assert result.failed == 0, result.errors
    assert not (tmp_path / "Temp" / "Thumbs.db").exists()
    assert not (tmp_path / "Vide").exists()
    engine.undo(result.session_id, TaskControl())
    assert snapshot(tmp_path) == before
    assert sorted(p for p, _d, _f in os.walk(tmp_path)) == dirs_before


def test_programs_and_games_are_never_cleaned(tmp_path):
    """Dans un jeu ou un logiciel : pas d'« installateur obsolète » ni de dossier vide proposé."""
    root = tmp_path / "Jeux"
    for game in ("Jeu A", "Jeu B"):
        folder = root / game
        (folder / "Jeu_Data").mkdir(parents=True)
        (folder / "Jeu.exe").write_bytes(dataset.exe_bytes())
        (folder / "UnityCrashHandler64 1.0.exe" if game == "Jeu A" else folder / "UnityCrashHandler64 2.0.exe") \
            .write_bytes(dataset.exe_bytes())
        (folder / "UnityPlayer.dll").write_bytes(b"MZ")
        (folder / "GameAssembly.dll").write_bytes(b"MZ")
        (folder / "Saves").mkdir()  # vide, mais nécessaire au jeu
    dl = tmp_path / "Telechargements"
    dl.mkdir()
    (dl / "Outil 1.0 setup.exe").write_bytes(dataset.exe_bytes())
    (dl / "Outil 1.0 setup (1).exe").write_bytes(dataset.exe_bytes())  # même version : une copie, pas obsolète
    (dl / "Outil 2.0 setup.exe").write_bytes(dataset.exe_bytes())
    report = diskreport.analyze_disk(str(tmp_path), TaskControl())
    assert sorted(f.name for f in report.installers) == ["Outil 1.0 setup (1).exe", "Outil 1.0 setup.exe"]
    assert report.empty_dirs == []
