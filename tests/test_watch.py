"""Surveillance : un nouveau fichier est rangé automatiquement une fois complètement écrit."""

import time

from rangeo.core import watcher
from rangeo.core.models import TaskControl


def test_new_file_is_sorted(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STABLE_SECONDS", 0.5)
    folder = tmp_path / "Telechargements"
    folder.mkdir()
    events = []
    w = watcher.FolderWatcher(engine, str(folder), "downloads", recursive=False, delay=2, on_event=events.append)
    w.start()
    try:
        (folder / "Facture.pdf").write_bytes(b"%PDF-1.4 test")
        (folder / "film.mp4.crdownload").write_bytes(b"en cours")  # téléchargement inachevé : ignoré
        deadline = time.time() + 15
        while time.time() < deadline and not events:
            time.sleep(0.2)
    finally:
        w.stop()
    assert events and events[0].moved == 1
    assert list(folder.rglob("Facture.pdf"))[0].parent.name.isdigit()  # Documents/PDF/<année>
    assert (folder / "film.mp4.crdownload").exists()


def test_undo_of_auto_sort_is_respected(engine, tmp_path, monkeypatch):
    """Après « Annuler », la surveillance ne doit pas re-ranger aussitôt les fichiers revenus."""
    monkeypatch.setattr(watcher, "STABLE_SECONDS", 0.5)
    folder = tmp_path / "Telechargements"
    folder.mkdir()
    doc = folder / "Facture.pdf"
    doc.write_bytes(b"%PDF-1.4 test")
    w = watcher.FolderWatcher(engine, str(folder), "downloads", recursive=False, delay=2)
    w._process([str(doc)])
    assert not doc.exists()
    session = engine.journal.sessions(1)[0]
    engine.undo(session.id, TaskControl())
    assert doc.exists()
    w._process([str(doc)])  # la surveillance revoit le fichier
    assert doc.exists(), "le fichier restauré a été re-rangé"
    # s'il est modifié ensuite, il redevient un fichier « normal »
    doc.write_bytes(b"%PDF-1.4 test modifie")
    w._process([str(doc)])
    assert not doc.exists()


def test_locked_file_is_retried_later(engine, tmp_path, monkeypatch):
    """Fichier encore ouvert au moment du rangement automatique : retenté plus tard, pas oublié."""
    import win32file

    from rangeo.core.executor import ExecOptions

    monkeypatch.setattr(watcher, "RETRY_DELAY", 0.0)
    monkeypatch.setattr(engine, "exec_options", lambda cleanup=None: ExecOptions(cleanup_empty=False,
                                                                                lock_retries=0))
    folder = tmp_path / "Telechargements"
    folder.mkdir()
    doc = folder / "Facture.pdf"
    doc.write_bytes(b"%PDF-1.4 test")
    w = watcher.FolderWatcher(engine, str(folder), "downloads", recursive=False, delay=2)
    handle = win32file.CreateFile(str(doc), win32file.GENERIC_READ, 0, None, win32file.OPEN_EXISTING, 0, None)
    try:
        w._process([str(doc)])
    finally:
        handle.Close()
    assert doc.exists()
    assert str(doc) in w._pending and w._retries[str(doc)] == 1
    w._process([str(doc)])  # nouvel essai : le fichier est libre
    assert not doc.exists()
    assert str(doc) not in w._retries


def test_watch_waits_during_manual_operation(engine, tmp_path, monkeypatch):
    """Pendant un rangement manuel, la surveillance attend ; elle range ensuite les fichiers arrivés."""
    monkeypatch.setattr(watcher, "STABLE_SECONDS", 0.5)
    folder = tmp_path / "Telechargements"
    folder.mkdir()
    manager = watcher.WatchManager(engine)
    w = watcher.FolderWatcher(engine, str(folder), "downloads", recursive=False, delay=2)
    manager.watchers["x"] = w
    w.start()
    try:
        manager.hold(True)
        (folder / "Facture.pdf").write_bytes(b"%PDF-1.4 test")
        time.sleep(4)
        assert (folder / "Facture.pdf").exists()
        manager.hold(False)
        deadline = time.time() + 15
        while time.time() < deadline and (folder / "Facture.pdf").exists():
            time.sleep(0.2)
    finally:
        w.stop()
    assert not (folder / "Facture.pdf").exists()
