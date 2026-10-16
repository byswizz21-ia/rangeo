"""Renommage en masse : aperçu, conflits, exécution et annulation."""

import os

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


def test_renumbering_and_swaps_without_conflicts(engine, tmp_path):
    """Renuméroter des fichiers déjà numérotés, ou échanger deux noms, ne crée pas de « (2) » ;
    l'annulation remet exactement les noms d'avant."""
    from conftest import snapshot
    from rangeo.core import rename as rn
    from rangeo.core.engine import SortRequest
    from rangeo.core.models import TaskControl

    folder = tmp_path / "Vacances"
    folder.mkdir()
    # 3 photos déjà numérotées + une nouvelle à insérer au milieu (tri par date)
    for i, (name, ts) in enumerate([("Vacances 001.jpg", 10), ("Vacances 002.jpg", 30),
                                    ("Vacances 003.jpg", 40), ("IMG_9999.jpg", 20)]):
        p = folder / name
        p.write_bytes(name.encode() * 10)
        os.utime(p, (1_600_000_000 + ts, 1_600_000_000 + ts))
    before = snapshot(folder)
    scan = engine.scan_and_classify(SortRequest(sources=[str(folder)], profile=engine.config.profile(None),
                                                in_place=True), TaskControl())
    opt = rn.RenameOptions(template="Vacances {compteur}", sort_by="date", clean_copies=False)
    for e in scan.entries:
        e.date = e.mtime
    items = rn.preview(scan.entries, opt)
    names = sorted(i.new_name for i in items)
    assert names == ["Vacances 001.jpg", "Vacances 002.jpg", "Vacances 003.jpg", "Vacances 004.jpg"], names
    assert all(i.status != "renamed_conflict" for i in items)
    result = engine.execute(rn.to_plan(items), TaskControl(), kind="rename")
    assert result.failed == 0, result.errors
    assert sorted(os.listdir(folder)) == names
    assert (folder / "Vacances 002.jpg").read_bytes() == b"IMG_9999.jpg" * 10
    assert (folder / "Vacances 004.jpg").read_bytes() == b"Vacances 003.jpg" * 10
    engine.undo(result.session_id, TaskControl())
    assert snapshot(folder) == before

    # échange de deux noms (cycle) : passe par un nom temporaire
    a, b = folder / "Vacances 001.jpg", folder / "Vacances 002.jpg"
    items = [rn.RenameItem(e, "Vacances 002.jpg" if e.name == "Vacances 001.jpg" else "Vacances 001.jpg")
             for e in scan.entries if e.name in ("Vacances 001.jpg", "Vacances 002.jpg")]
    plan = rn.to_plan(items)
    assert len(plan.ops) == 3 and plan.ops[0].rule == rn.TEMP_RULE
    content_a, content_b = a.read_bytes(), b.read_bytes()
    result = engine.execute(plan, TaskControl(), kind="rename")
    assert result.failed == 0 and result.files == 2
    assert a.read_bytes() == content_b and b.read_bytes() == content_a
    assert not [n for n in os.listdir(folder) if n.startswith("~rangeo")]
    engine.undo(result.session_id, TaskControl())
    assert snapshot(folder) == before
