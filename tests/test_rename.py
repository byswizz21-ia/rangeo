"""Renommage en masse : aperçu, conflits, exécution et annulation."""

import dataset
from conftest import snapshot
from rangeo.core import rename
from rangeo.core.classify import ClassifyOptions, classify
from rangeo.core.models import TaskControl
from rangeo.core.scanner import ScanOptions, scan


def entries_of(folder):
    res = scan([str(folder)], ScanOptions(), TaskControl())
    classify(res.entries, ClassifyOptions(), TaskControl())
    return res.entries


def test_transformations():
    o = rename.RenameOptions(separators="spaces", case="title")
    assert rename.transform_stem("Copie de mon_super.fichier (1)", o) == "Mon Super Fichier"
    o = rename.RenameOptions(remove_accents=True, case="lower", clean_copies=False)
    assert rename.transform_stem("Été À Noël (1)", o) == "ete a noel (1)"
    o = rename.RenameOptions(find=r"\d+", replace="#", regex=True)
    assert rename.transform_stem("photo 123 456", o) == "photo # #"
    o = rename.RenameOptions(prefix="[2024] ", suffix=" - final")
    assert rename.transform_stem("rapport", o) == "[2024] rapport - final"


def test_template_numbering_and_conflicts(tmp_path):
    d = tmp_path / "photos"
    d.mkdir()
    (d / "IMG_20240312_143200.jpg").write_bytes(dataset.jpeg_bytes(date="2024:03:12 14:32:00"))
    (d / "IMG_20240313_090000.JPG").write_bytes(dataset.jpeg_bytes(date="2024:03:13 09:00:00", color=(1, 2, 3)))
    (d / "sans date.jpg").write_bytes(dataset.png_bytes())
    (d / "2024-03-12 14h32.jpg").write_bytes(b"deja la")  # nom déjà pris
    entries = [e for e in entries_of(d) if e.name != "2024-03-12 14h32.jpg"]
    opts = rename.RenameOptions(template="{annee}-{mois}-{jour} {heure}h{minute}", sort_by="date")
    items = {i.entry.name: i for i in rename.preview(entries, opts)}
    assert items["IMG_20240312_143200.jpg"].new_name == "2024-03-12 14h32 (2).jpg"
    assert items["IMG_20240312_143200.jpg"].status == "renamed_conflict"
    assert items["IMG_20240313_090000.JPG"].new_name == "2024-03-13 09h00.jpg"
    opts = rename.RenameOptions(numbering="prefix", padding=2, num_sep=" - ")
    names = sorted(i.new_name for i in rename.preview(entries, opts))
    assert names[0].startswith("01 - ") and names[1].startswith("02 - ")


def test_rename_execute_and_undo(engine, tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    for name in ("Copie de rapport.pdf", "facture_EDF (1).PDF", "Notes.TXT"):
        (d / name).write_bytes(name.encode())
    before = snapshot(d)
    items = rename.preview(entries_of(d), rename.RenameOptions(separators="spaces", case="lower"))
    plan = rename.to_plan(items)
    result = engine.execute(plan, TaskControl(), kind="rename", cleanup=False)
    assert result.failed == 0
    assert sorted(p.name for p in d.iterdir()) == ["facture edf.pdf", "notes.txt", "rapport.pdf"]
    engine.undo(result.session_id, TaskControl())
    assert snapshot(d) == before
