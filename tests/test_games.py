"""Les jeux, logiciels et collections de ROMs ne doivent jamais être éparpillés."""

import os

import dataset
from conftest import snapshot
from rangeo.core.engine import SortRequest
from rangeo.core.models import TaskControl


def w(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def game_bin_data(root):
    """Jeu typique : bin/ (exe + dll) et data/ avec images et musiques du jeu."""
    w(os.path.join(root, "bin", "game.exe"), dataset.exe_bytes())
    w(os.path.join(root, "bin", "engine.dll"), dataset.exe_bytes(dll=True))
    for i in range(6):
        w(os.path.join(root, "data", "textures", f"tex{i}.png"), dataset.png_bytes((8, 8)))
        w(os.path.join(root, "data", "music", f"theme{i}.mp3"), dataset.mp3_bytes(2))


def analyze(engine, sources, dst, **kw):
    req = SortRequest(sources=[str(s) for s in sources], profile=engine.config.profile("general"),
                      destination=str(dst), **kw)
    return engine.analyze(req, TaskControl())


def moved_paths(plan):
    return [op.src for op in plan.active_ops()]


def test_source_is_a_game_folder(engine, tmp_path):
    """On choisit directement le dossier d'un jeu : rien ne doit bouger."""
    game = tmp_path / "MonJeu"
    w(game / "MonJeu.exe", dataset.exe_bytes())
    w(game / "UnityPlayer_like.dll", dataset.exe_bytes(dll=True))
    for i in range(5):
        w(game / f"image{i}.png", dataset.png_bytes((8, 8)))
    scan, plan = analyze(engine, [game], tmp_path / "dst")
    assert plan.active_ops() == []
    assert any(k == "scan.root_is_software" for k, _ in plan.warnings)


def test_source_game_with_bin_and_data(engine, tmp_path):
    game = tmp_path / "MonJeu"
    game_bin_data(game)
    scan, plan = analyze(engine, [game], tmp_path / "dst")
    assert plan.active_ops() == []
    assert any(k == "scan.root_is_software" for k, _ in plan.warnings)


def test_game_without_dll(engine, tmp_path):
    """Vieux jeu : un .exe et un dossier data/ (pas de .dll)."""
    disk = tmp_path / "Disque"
    w(disk / "Jeux" / "VieuxJeu" / "jeu.exe", dataset.exe_bytes())
    for i in range(5):
        w(disk / "Jeux" / "VieuxJeu" / "data" / f"sprite{i}.png", dataset.png_bytes((8, 8)))
    w(disk / "Unity" / "Super.exe", dataset.exe_bytes())
    w(disk / "Unity" / "Super_Data" / "level1.png", dataset.png_bytes((8, 8)))
    for i in range(3):
        w(disk / "Photos" / f"IMG_2020010{i}_120000.jpg", dataset.jpeg_bytes(date="2020:01:01 12:00:00"))
    scan, plan = analyze(engine, [disk], tmp_path / "dst")
    moved = moved_paths(plan)
    assert not any("VieuxJeu" in p for p in moved)
    assert not any("Unity" in p for p in moved)
    assert sum("Photos" in p for p in moved) == 3


def test_game_with_soundtrack_inside_user_disk(engine, tmp_path):
    """Un jeu avec ses musiques (bin/ + data/ + Soundtrack/) au milieu d'un disque personnel."""
    disk = tmp_path / "Vieux disque"
    game_bin_data(disk / "MonJeu")
    for i in range(12):
        w(disk / "MonJeu" / "Soundtrack" / f"{i:02d} - OST.mp3", dataset.mp3_bytes(2))
    for i in range(4):
        w(disk / "Vacances" / f"DSC0{i}.JPG", dataset.jpeg_bytes(date="2019:08:01 10:00:00", make="SONY"))
    scan, plan = analyze(engine, [disk], tmp_path / "dst")
    moved = moved_paths(plan)
    assert not any("MonJeu" in p for p in moved), moved
    assert sum("Vacances" in p for p in moved) == 4
    bundles = [e for e in scan.entries if e.is_bundle]
    assert any(e.name == "MonJeu" and e.kind == "software" for e in bundles)


def test_games_folder_with_several_games(engine, tmp_path):
    disk = tmp_path / "Disque"
    game_bin_data(disk / "Jeux" / "Jeu1")
    game_bin_data(disk / "Jeux" / "Jeu2")
    w(disk / "Docs" / "CV.docx", dataset.docx_bytes())
    w(disk / "Docs" / "Lettre.docx", dataset.docx_bytes() + b"1")
    w(disk / "Docs" / "Contrat.pdf", dataset.pdf_bytes())
    scan, plan = analyze(engine, [disk], tmp_path / "dst")
    moved = moved_paths(plan)
    assert not any(os.sep + "Jeux" + os.sep in p for p in moved)
    assert sum("Docs" in p for p in moved) == 3


def test_downloads_with_installer_is_still_sorted(engine, tmp_path):
    """Un dossier Téléchargements avec un setup.exe et une .dll reste rangeable."""
    dl = tmp_path / "Téléchargements"
    w(dl / "setup.exe", dataset.exe_bytes())
    w(dl / "msvcp140.dll", dataset.exe_bytes(dll=True))
    for i in range(6):
        w(dl / f"facture{i}.pdf", dataset.pdf_bytes(str(i)))
    w(dl / "photo.jpg", dataset.jpeg_bytes())
    scan, plan = analyze(engine, [dl], tmp_path / "dst")
    moved = moved_paths(plan)
    assert sum(p.endswith(".pdf") for p in moved) == 6
    assert not plan.warnings


def test_rom_collection_and_loose_rom(engine, tmp_path):
    disk = tmp_path / "Disque"
    for i in range(5):
        w(disk / "Emulation" / "GBA" / f"jeu{i}.gba", b"rom%d" % i)
    w(disk / "Telechargements" / "Pokemon.gba", b"rom-solo")
    w(disk / "Telechargements" / "notes.txt", b"x")
    scan, plan = analyze(engine, [disk], tmp_path / "dst")
    moved = moved_paths(plan)
    assert not any("Emulation" in p for p in moved)
    rom = [op for op in plan.active_ops() if op.src.endswith("Pokemon.gba")][0]
    assert rom.dst.endswith(os.path.join("Jeux", "ROMs", "Game Boy Advance", "Pokemon.gba"))


def test_open_up_lets_user_sort_a_protected_folder(engine, tmp_path):
    disk = tmp_path / "Disque"
    w(disk / "Divers" / "outil.exe", dataset.exe_bytes())
    w(disk / "Divers" / "outil.dll", dataset.exe_bytes(dll=True))
    w(disk / "Divers" / "rapport.pdf", dataset.pdf_bytes())
    scan, plan = analyze(engine, [disk], tmp_path / "dst")
    assert not any("rapport" in p for p in moved_paths(plan))
    scan, plan = analyze(engine, [disk], tmp_path / "dst", open_up=[str(disk / "Divers")])
    assert any("rapport" in p for p in moved_paths(plan))


def test_dataset_still_sorted_correctly(engine, tmp_path):
    src = tmp_path / "src"
    info = dataset.make_dataset(src)
    before = snapshot(info["game"])
    scan, plan = analyze(engine, [src], tmp_path / "dst")
    moved = moved_paths(plan)
    assert not any("SuperJeu" in p for p in moved)
    assert any("Vacances" in p for p in moved)
    engine.execute(plan, TaskControl())
    assert snapshot(info["game"]) == before
