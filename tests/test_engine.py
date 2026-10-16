"""Tests de bout en bout : rangement réel, annulation, reprise après crash, conflits, doublons."""

import os

import pytest

import dataset
from conftest import all_files, snapshot
from rangeo.core import fileops, journal as jn
from rangeo.core.engine import SortRequest
from rangeo.core.executor import ExecOptions, Executor
from rangeo.core.models import TaskControl


def _request(engine, src, dst, **kw):
    profile = engine.config.profile("general")
    return SortRequest(sources=[str(src)], profile=profile, destination=str(dst), **kw)


def _sort(engine, src, dst, **kw):
    req = _request(engine, src, dst, **kw)
    assert engine.check_request(req) == []
    scan, plan = engine.analyze(req, TaskControl())
    result = engine.execute(plan, TaskControl())
    return scan, plan, result


def test_full_sort_then_undo_restores_everything(engine, tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    info = dataset.make_dataset(src)
    before = snapshot(src)

    scan, plan, result = _sort(engine, src, dst)
    assert result.failed == 0, result.errors
    assert result.status == jn.S_COMPLETED

    after_dst = all_files(dst)
    # quelques vérifications du rangement
    assert os.path.join("Musique", "Daft Punk", "Discovery", "01 - One More Time.mp3") in after_dst
    assert os.path.join("Vidéos", "Films", "Inception (2010)", "Inception.2010.1080p.BluRay.x264.mkv") in after_dst
    assert os.path.join("Vidéos", "Séries", "Breaking Bad", "Saison 01",
                        "Breaking.Bad.S01E02.720p.HDTV.fr.srt") in after_dst
    assert os.path.join("Images", "Photos", "2019", "2019-08 Août", "DSC00040.JPG") in after_dst
    assert os.path.join("Images", "Captures d'écran", "2024", "Screenshot 2024-03-12 143200.png") in after_dst
    assert os.path.join("Documents", "PDF", "2024", "Facture EDF mars 2024.pdf") in after_dst
    assert os.path.join("Logiciels", "Installateurs", "Vlc", "setup_vlc.exe") in after_dst
    # projet de code déplacé EN BLOC (avec son .git)
    assert os.path.join("Projets de code", "mon-site", ".git", "HEAD") in after_dst
    assert os.path.join("Projets de code", "mon-site", "src", "logo.png") in after_dst
    # copie identique « (1) » → doublons
    assert any(p.startswith("_Doublons") and p.endswith("IMG_20230710_102230 (1).jpg") for p in after_dst)
    # jeu, sauvegardes et dossier « ne pas éclater » laissés en place
    assert os.path.isfile(info["game"] / "bin" / "game.exe")
    assert os.path.isfile(info["game"] / "data" / "texture.png")
    assert os.path.isfile(info["saves"] / "slot0.sav")
    assert os.path.isfile(info["kept"] / "photo1.jpg")
    # fichiers ignorés : téléchargement en cours, fichier caché
    assert os.path.isfile(src / "Téléchargements" / "film_en_cours.mp4.crdownload")
    assert os.path.isfile(src / "Téléchargements" / "cache_cache.dat")
    # le contenu n'a pas changé : mêmes empreintes, même nombre de fichiers
    total_after = snapshot(src)
    total_after.update({"dst:" + k: v for k, v in snapshot(dst).items()})
    assert sorted(v for v in before.values()) == sorted(v for v in total_after.values())
    # dossiers vides nettoyés
    assert not (src / "Vieux disque" / "Vacances").exists()

    # --- annulation complète ---
    undo = engine.undo(result.session_id, TaskControl())
    assert undo.failed == 0, undo.errors
    assert snapshot(src) == before
    assert all_files(dst) == []
    assert engine.journal.session(result.session_id).status == jn.S_UNDONE


def test_resort_in_place_is_idempotent(engine, tmp_path):
    src = tmp_path / "src"
    dataset.make_dataset(src)
    req = SortRequest(sources=[str(src)], profile=engine.config.profile("general"), in_place=True)
    scan, plan = engine.analyze(req, TaskControl())
    result = engine.execute(plan, TaskControl())
    assert result.failed == 0
    # deuxième passage : tout est déjà rangé
    scan2, plan2 = engine.analyze(req, TaskControl())
    assert [op for op in plan2.ops if op.enabled] == []


def test_cross_volume_copies_are_verified(engine, tmp_path, monkeypatch):
    """Simule deux disques différents : copie + vérification + suppression de l'original."""
    monkeypatch.setattr(Executor, "_same_volume", lambda self, a, b: False)
    src, dst = tmp_path / "src", tmp_path / "dst"
    dataset.make_dataset(src)
    before = snapshot(src)
    scan, plan, result = _sort(engine, src, dst)
    assert result.failed == 0, result.errors
    assert not list(dst.rglob("~rangeo-*"))  # aucun fichier temporaire oublié
    moved = snapshot(dst)
    assert len(moved) > 30
    # dates de modification conservées
    st = os.stat(dst / "Documents" / "PDF" / "2024" / "Facture EDF mars 2024.pdf")
    assert abs(st.st_mtime - dataset.ts(2024, 3, 20)) < 2
    # lecture seule conservée
    ro = dst / "Documents" / "PDF" / "2023" / "contrat_lecture_seule.pdf"
    assert not os.access(ro, os.W_OK)
    undo = engine.undo(result.session_id, TaskControl())
    assert undo.failed == 0, undo.errors
    assert snapshot(src) == before


def test_crash_then_resume(engine, tmp_path, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    dataset.make_dataset(src)
    before = snapshot(src)
    req = _request(engine, src, dst)
    scan, plan = engine.analyze(req, TaskControl())

    real_move = fileops.move_file
    calls = {"n": 0}

    def crashing_move(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 12:
            raise KeyboardInterrupt("coupure de courant simulée")
        return real_move(*a, **kw)

    monkeypatch.setattr(fileops, "move_file", crashing_move)
    with pytest.raises(KeyboardInterrupt):
        Executor(engine.journal, TaskControl(), ExecOptions(threads=1)).run_plan(plan)
    monkeypatch.setattr(fileops, "move_file", real_move)

    unfinished = engine.journal.unfinished_sessions()
    assert len(unfinished) == 1
    result = engine.resume(unfinished[0].id, TaskControl())
    assert result.failed == 0, result.errors
    assert result.status == jn.S_COMPLETED
    total = snapshot(src)
    total.update({"dst:" + k: v for k, v in snapshot(dst).items()})
    assert sorted(before.values()) == sorted(total.values())


def test_crash_between_copy_and_delete(engine, tmp_path, monkeypatch):
    """Coupure juste après la copie vérifiée, avant la suppression de l'original."""
    monkeypatch.setattr(Executor, "_same_volume", lambda self, a, b: False)
    src, dst = tmp_path / "src", tmp_path / "dst"
    dataset.make_dataset(src)
    before = snapshot(src)
    scan, plan = engine.analyze(_request(engine, src, dst), TaskControl())
    real_remove = fileops.remove_file
    calls = {"n": 0}

    def crashing_remove(path):
        calls["n"] += 1
        if calls["n"] == 5:
            raise KeyboardInterrupt()
        return real_remove(path)

    monkeypatch.setattr(fileops, "remove_file", crashing_remove)
    with pytest.raises(KeyboardInterrupt):
        Executor(engine.journal, TaskControl(), ExecOptions(threads=1)).run_plan(plan)
    monkeypatch.setattr(fileops, "remove_file", real_remove)
    sid = engine.journal.unfinished_sessions()[0].id
    result = engine.resume(sid, TaskControl())
    assert result.failed == 0, result.errors
    total = snapshot(src)
    total.update({"dst:" + k: v for k, v in snapshot(dst).items()})
    assert sorted(before.values()) == sorted(total.values())  # ni perte, ni doublon


def test_conflicts(engine, tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src).mkdir()
    (src / "notes.txt").write_text("nouveau contenu")
    (src / "pareil.txt").write_text("identique")
    target = dst / "Documents" / "Textes"
    target.mkdir(parents=True)
    (target / "notes.txt").write_text("ancien contenu")
    (target / "pareil.txt").write_text("identique")
    scan, plan, result = _sort(engine, src, dst)
    assert result.failed == 0
    assert (target / "notes.txt").read_text() == "ancien contenu"
    assert (target / "notes (2).txt").read_text() == "nouveau contenu"
    dup = [p for p in all_files(dst) if p.startswith("_Doublons")]
    assert len(dup) == 1 and dup[0].endswith("pareil.txt")


def test_overwrite_strategy_and_undo(engine, tmp_path):
    engine.config.set("conflict", "overwrite", save=False)
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "notes.txt").write_text("nouveau")
    target = dst / "Documents" / "Textes"
    target.mkdir(parents=True)
    (target / "notes.txt").write_text("ancien")
    scan, plan, result = _sort(engine, src, dst)
    assert result.failed == 0
    assert (target / "notes.txt").read_text() == "nouveau"
    engine.undo(result.session_id, TaskControl())
    assert (src / "notes.txt").read_text() == "nouveau"
    assert (target / "notes.txt").read_text() == "ancien"  # l'ancien fichier est revenu de la corbeille


def test_locked_file_is_reported_not_lost(engine, tmp_path):
    import win32file

    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "libre.txt").write_text("a")
    locked = src / "verrouille.txt"
    locked.write_text("b")
    handle = win32file.CreateFile(str(locked), win32file.GENERIC_READ, 0, None, win32file.OPEN_EXISTING, 0, None)
    try:
        scan, plan = engine.analyze(_request(engine, src, dst), TaskControl())
        ex = Executor(engine.journal, TaskControl(), ExecOptions(lock_retries=1, lock_delay=0.1))
        result = ex.run_plan(plan)
    finally:
        handle.Close()
    assert (dst / "Documents" / "Textes" / "libre.txt").exists()
    assert result.failed == 1
    assert result.errors[0][1] == "locked"
    assert locked.read_text() == "b"


def test_cancel_and_pause(engine, tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    dataset.make_dataset(src)
    scan, plan = engine.analyze(_request(engine, src, dst), TaskControl())
    control = TaskControl()
    control.cancel()
    result = engine.execute(plan, control)
    assert result.status == jn.S_CANCELLED
    # rien n'est perdu, la session peut être reprise
    result2 = engine.resume(result.session_id, TaskControl())
    assert result2.failed == 0


def test_duplicates_find_and_hardlink(engine, tmp_path):
    root = tmp_path / "dups"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    data = os.urandom(300_000)
    (root / "a" / "video.bin").write_bytes(data)
    (root / "b" / "video - Copie.bin").write_bytes(data)
    (root / "b" / "autre.bin").write_bytes(os.urandom(300_000))
    (root / "a" / "small.txt").write_text("x")
    (root / "b" / "small (1).txt").write_text("x")
    scan, groups = engine.find_duplicates([str(root)], TaskControl())
    assert len(groups) == 2
    big = max(groups, key=lambda g: g.size)
    assert big.keep.name == "video.bin"  # nom le plus « propre »
    plan = engine.duplicate_plan(groups, "hardlink")
    result = engine.execute(plan, TaskControl(), kind="duplicates")
    assert result.failed == 0
    assert fileops.is_same_file(str(root / "a" / "video.bin"), str(root / "b" / "video - Copie.bin"))
    engine.undo(result.session_id, TaskControl())
    assert not fileops.is_same_file(str(root / "a" / "video.bin"), str(root / "b" / "video - Copie.bin"))
    assert (root / "b" / "video - Copie.bin").read_bytes() == data


def test_duplicates_move_and_undo(engine, tmp_path):
    root = tmp_path / "dups"
    (root / "x").mkdir(parents=True)
    (root / "x" / "a.jpg").write_bytes(b"1" * 5000)
    (root / "a.jpg").write_bytes(b"1" * 5000)
    before = snapshot(root)
    scan, groups = engine.find_duplicates([str(root)], TaskControl())
    plan = engine.duplicate_plan(groups, "move")
    result = engine.execute(plan, TaskControl(), kind="duplicates")
    assert result.failed == 0
    assert any("_Doublons" in p for p in all_files(root))
    engine.undo(result.session_id, TaskControl())
    assert snapshot(root) == before


def test_refuses_dangerous_requests(engine, tmp_path):
    windir = os.environ.get("WINDIR", r"C:\Windows")
    req = SortRequest(sources=[windir], profile=engine.config.profile("general"), destination=str(tmp_path))
    assert any(key == "protect.system_folder" for key, _ in engine.check_request(req))
    req = SortRequest(sources=[str(tmp_path)], profile=engine.config.profile("general"),
                      destination=os.environ["APPDATA"])
    assert engine.check_request(req)
    inner = tmp_path / "a"
    inner.mkdir()
    req = SortRequest(sources=[str(inner)], profile=engine.config.profile("general"), destination=str(tmp_path))
    assert any(key == "check.source_inside_destination" for key, _ in engine.check_request(req))


@pytest.mark.realtrash
def test_real_recycle_bin_roundtrip(tmp_path):
    from rangeo.core import trash

    f = tmp_path / "rangeo_test_corbeille.txt"
    f.write_text("à restaurer")
    info = trash.trash(str(f), 0, f.stat().st_size)
    assert not f.exists()
    if info["method"] == "recycle":
        restored = trash.restore(str(f), info)
        assert restored == str(f)
    else:
        trash.restore(str(f), info)
    assert f.read_text() == "à restaurer"


def test_crash_then_undo_without_resume(engine, tmp_path, monkeypatch):
    """Coupure en plein rangement, puis « Tout annuler » : tout doit revenir, même les opérations non notées."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    dataset.make_dataset(src)
    before = snapshot(src)
    scan, plan = engine.analyze(_request(engine, src, dst), TaskControl())
    real_move = fileops.move_file
    calls = {"n": 0}

    def crashing_move(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 20:
            engine.journal._pending.clear()  # les dernières écritures du journal sont perdues
            raise KeyboardInterrupt()
        return real_move(*a, **kw)

    monkeypatch.setattr(fileops, "move_file", crashing_move)
    with pytest.raises(KeyboardInterrupt):
        Executor(engine.journal, TaskControl(), ExecOptions(threads=1)).run_plan(plan)
    monkeypatch.setattr(fileops, "move_file", real_move)
    sid = engine.journal.unfinished_sessions()[0].id
    undo = engine.undo(sid, TaskControl())
    assert undo.failed == 0, undo.errors
    assert snapshot(src) == before


def test_reports_export(engine, tmp_path):
    from rangeo.core.report import export_csv, export_html
    from rangeo.ui.i18n import set_language, tr

    set_language("fr")
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "a.pdf").write_bytes(b"%PDF-1.4")
    (src / "b.txt").write_text("x")
    _scan, _plan, result = _sort(engine, src, dst)
    html_path, csv_path = tmp_path / "r.html", tmp_path / "r.csv"
    export_html(engine.journal, result.session_id, str(html_path), tr, "fr")
    export_csv(engine.journal, result.session_id, str(csv_path), tr)
    html = html_path.read_text(encoding="utf-8")
    assert "Rapport Rangeo" in html and "a.pdf" in html
    assert "Déplacement" in csv_path.read_text(encoding="utf-8-sig")


def test_session_of_live_process_is_not_offered_for_recovery(engine, tmp_path):
    """Un rangement planifié en cours dans un autre processus ne doit pas être « repris » en double."""
    import subprocess
    import sys

    sid = engine.journal.create_session("scheduled", "test", [str(tmp_path)], "")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with engine.journal._lock, engine.journal._conn:
            engine.journal._conn.execute("UPDATE sessions SET pid = ? WHERE id = ?", (proc.pid, sid))
        assert engine.journal.owner_alive(sid)
        assert sid not in [s.id for s in engine.journal.unfinished_sessions()]
        with pytest.raises(RuntimeError):
            engine.resume(sid, TaskControl())
    finally:
        proc.kill()
        proc.wait()
    assert sid in [s.id for s in engine.journal.unfinished_sessions()]


def test_cross_volume_locked_source_leaves_no_copy(engine, tmp_path, monkeypatch):
    """Autre disque, fichier ouvert par un programme (lisible mais pas supprimable) :
    pas de copie fantôme laissée à la destination, l'original reste intact."""
    import win32file

    monkeypatch.setattr(Executor, "_same_volume", lambda self, a, b: False)
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "libre.txt").write_text("a")
    locked = src / "ouvert.txt"
    locked.write_text("b")
    handle = win32file.CreateFile(str(locked), win32file.GENERIC_READ, win32file.FILE_SHARE_READ, None,
                                  win32file.OPEN_EXISTING, 0, None)
    try:
        scan, plan = engine.analyze(_request(engine, src, dst), TaskControl())
        ex = Executor(engine.journal, TaskControl(), ExecOptions(lock_retries=2, lock_delay=0.1))
        result = ex.run_plan(plan)
    finally:
        handle.Close()
    assert result.failed == 1
    assert locked.read_text() == "b"
    names = [os.path.basename(f) for f in all_files(dst)]
    assert names == ["libre.txt"], names
    engine.undo(result.session_id, TaskControl())
    assert sorted(os.listdir(src)) == ["libre.txt", "ouvert.txt"]


def test_overwrite_failure_puts_existing_file_back(engine, tmp_path, monkeypatch):
    """« Remplacer » : si le déplacement échoue, le fichier qui devait être remplacé revient à sa place."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "notes.txt").write_text("nouveau")
    target = dst / "Documents" / "Textes"
    target.mkdir(parents=True)
    (target / "notes.txt").write_text("ancien")

    def broken(*a, **k):
        raise PermissionError(5, "Accès refusé")

    monkeypatch.setattr(fileops, "move_file", broken)
    req = _request(engine, src, dst)
    req.profile["conflict"] = "overwrite"
    engine.config.set("conflict", "overwrite", save=False)
    scan, plan = engine.analyze(req, TaskControl())
    assert any(op.replaces for op in plan.ops)
    result = engine.execute(plan, TaskControl())
    assert result.failed == 1
    assert (target / "notes.txt").read_text() == "ancien"
    assert (src / "notes.txt").read_text() == "nouveau"


def test_undo_finds_files_moved_again_later(engine, tmp_path):
    """Session A range src -> dst, puis session B déplace dst -> dst2 : annuler A ramène quand même
    les fichiers à leur place d'origine (on suit le déplacement fait par B)."""
    src, dst, dst2 = tmp_path / "src", tmp_path / "dst", tmp_path / "dst2"
    src.mkdir()
    (src / "facture.pdf").write_bytes(b"%PDF-1.4 facture")
    (src / "notes.txt").write_text("notes")
    _scan, _plan, a = _sort(engine, src, dst)
    assert a.failed == 0
    _scan, _plan, b = _sort(engine, dst, dst2)
    assert b.failed == 0 and not list(dst.rglob("*.*"))
    undo = engine.undo(a.session_id, TaskControl())
    assert undo.failed == 0, undo.errors
    assert sorted(os.listdir(src)) == ["facture.pdf", "notes.txt"]


def test_undo_can_be_retried_after_locked_file(engine, tmp_path):
    """Annulation partielle (fichier ouvert dans un programme) : on peut relancer l'annulation ensuite."""
    import win32file

    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "b.txt").write_text("b")
    _scan, _plan, result = _sort(engine, src, dst)
    sorted_b = next(dst.rglob("b.txt"))
    handle = win32file.CreateFile(str(sorted_b), win32file.GENERIC_READ, 0, None, win32file.OPEN_EXISTING, 0, None)
    try:
        first = engine.undo(result.session_id, TaskControl())
    finally:
        handle.Close()
    assert first.failed == 1 and first.errors[0][1] == "locked"
    assert first.status == jn.S_PARTIAL_UNDO
    second = engine.undo(result.session_id, TaskControl())
    assert second.failed == 0 and second.status == jn.S_UNDONE
    assert sorted(os.listdir(src)) == ["a.txt", "b.txt"]


def test_overwrite_interrupted_by_power_cut_is_undone(engine, tmp_path, monkeypatch):
    """Coupure juste après la mise à la corbeille du fichier à remplacer : l'annulation le remet."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "notes.txt").write_text("nouveau")
    target = dst / "Documents" / "Textes"
    target.mkdir(parents=True)
    (target / "notes.txt").write_text("ancien")
    engine.config.set("conflict", "overwrite", save=False)
    req = _request(engine, src, dst)
    req.profile["conflict"] = "overwrite"
    _scan, plan = engine.analyze(req, TaskControl())

    def power_cut(*a, **k):
        engine.journal._pending.clear()
        raise KeyboardInterrupt("coupure")

    monkeypatch.setattr(fileops, "move_file", power_cut)
    monkeypatch.setattr(Executor, "_put_back_replaced", lambda self, row, replaced: None)  # pas le temps
    with pytest.raises(KeyboardInterrupt):
        Executor(engine.journal, TaskControl(), ExecOptions(threads=1)).run_plan(plan)
    assert not (target / "notes.txt").exists()  # l'ancien est à la corbeille
    sid = engine.journal.unfinished_sessions()[0].id
    undo = engine.undo(sid, TaskControl())
    assert undo.failed == 0, undo.errors
    assert (target / "notes.txt").read_text() == "ancien"
    assert (src / "notes.txt").read_text() == "nouveau"


def test_destination_equal_to_source_sorts_in_place(engine, tmp_path):
    """Choisir le même dossier comme source et destination range sur place (avant : rien ne bougeait)."""
    src = tmp_path / "Bazar"
    src.mkdir()
    (src / "facture.pdf").write_bytes(b"%PDF-1.4 facture")
    (src / "notes.txt").write_text("notes")
    _scan, plan, result = _sort(engine, src, src)
    assert result.failed == 0 and result.files == 2
    assert not (src / "facture.pdf").exists()
    assert len([p for p in src.rglob("*") if p.is_file()]) == 2


def test_online_only_file_is_not_moved_out_of_onedrive(engine, tmp_path, monkeypatch):
    """Fichier OneDrive « en ligne seulement » : le sortir du dossier OneDrive le téléchargerait."""
    from rangeo.core import classify, fsutil

    onedrive = tmp_path / "OneDrive"
    onedrive.mkdir()
    (onedrive / "rapport.pdf").write_bytes(b"%PDF-1.4 x")
    monkeypatch.setenv("OneDrive", str(onedrive))
    monkeypatch.setattr(classify, "is_cloud_placeholder", lambda attrs: True)
    # destination hors de OneDrive : décoché
    scan, plan = engine.analyze(_request(engine, onedrive, tmp_path / "dst"), TaskControl())
    op = next(o for o in plan.ops if o.src.endswith("rapport.pdf"))
    assert not op.enabled and op.note == "plan.cloud_only"
    # rangement sur place, à l'intérieur de OneDrive : autorisé
    scan, plan = engine.analyze(_request(engine, onedrive, "", in_place=True), TaskControl())
    op = next(o for o in plan.ops if o.src.endswith("rapport.pdf"))
    assert op.enabled, op.note
    assert fsutil.cloud_root_of(str(onedrive / "a" / "b.txt")) == fsutil.norm(str(onedrive))


def test_duplicate_not_trashed_if_kept_file_vanished_or_changed(engine, tmp_path):
    """Entre l'analyse des doublons et le clic, le fichier conservé est supprimé ou modifié :
    la copie n'est pas mise à la corbeille."""
    from rangeo.core import duplicates as dup_mod

    folder = tmp_path / "photos"
    folder.mkdir()
    for name in ("a.jpg", "a (2).jpg", "b.jpg", "b (2).jpg"):
        (folder / name).write_bytes((b"A" if name.startswith("a") else b"B") * 5000)
    _scan, groups = engine.find_duplicates([str(folder)], TaskControl())
    assert len(groups) == 2
    plan = dup_mod.build_dup_plan(groups, "trash", None, "_Doublons")
    keeps = {g.keep.name for g in groups}
    assert keeps == {"a.jpg", "b.jpg"}
    (folder / "a.jpg").unlink()  # conservé puis supprimé par l'utilisateur
    (folder / "b.jpg").write_bytes(b"C" * 5000)  # conservé puis modifié
    result = engine.execute(plan, TaskControl(), kind="duplicates")
    assert result.failed == 2
    assert {code for _p, code, _d in result.errors} == {"kept_missing", "not_identical"}
    assert (folder / "a (2).jpg").exists() and (folder / "b (2).jpg").exists()
