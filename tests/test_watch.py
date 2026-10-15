"""Surveillance : un nouveau fichier est rangé automatiquement une fois complètement écrit."""

import time

from rangeo.core import watcher


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
