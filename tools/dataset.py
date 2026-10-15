"""Génère un faux dossier « en désordre » réaliste pour tester Rangeo sans risque."""

from __future__ import annotations

import io
import os
import random
import struct
import time
import zipfile
from pathlib import Path


def _write(path: Path, data: bytes, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    long_path = "\\\\?\\" + str(path) if len(str(path)) > 240 else str(path)
    with open(long_path, "wb") as fh:
        fh.write(data)
    if mtime:
        os.utime(long_path, (mtime, mtime))
    return path


def jpeg_bytes(size=(64, 48), color=(200, 100, 50), date: str | None = None, make=None, model=None) -> bytes:
    from PIL import Image

    im = Image.new("RGB", size, color)
    exif = Image.Exif()
    if make:
        exif[271] = make
    if model:
        exif[272] = model
    if date:
        exif[306] = date
        exif.get_ifd(0x8769)[36867] = date
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes() if (date or make or model) else b"", quality=70)
    return buf.getvalue()


def png_bytes(size=(64, 48), color=(10, 120, 200)) -> bytes:
    from PIL import Image

    im = Image.new("RGB", size, color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def mp4_bytes(created: float, extra: int = 2000) -> bytes:
    """MP4 minimal avec une boîte mvhd contenant la date de création."""
    ct = int(created) + 2082844800
    mvhd_body = struct.pack(">B3xIIII", 0, ct, ct, 1000, 60000) + b"\x00" * 80
    mvhd = struct.pack(">I4s", 8 + len(mvhd_body), b"mvhd") + mvhd_body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 512)
    mdat = struct.pack(">I4s", 8 + extra, b"mdat") + os.urandom(extra)
    return ftyp + mdat + moov


def mkv_bytes(extra: int = 4000) -> bytes:
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 28 + b"matroska" + os.urandom(extra)


def mp3_bytes(frames: int = 30) -> bytes:
    header = b"\xff\xfb\x90\x64"
    frame = header + b"\x00" * (417 - 4)
    return frame * frames


def tagged_mp3(path: Path, artist: str, album: str, title: str, track: int, mtime: float | None = None) -> Path:
    from mutagen.id3 import ID3, TALB, TIT2, TPE1, TRCK

    _write(path, mp3_bytes())
    tags = ID3()
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text=album))
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TRCK(encoding=3, text=f"{track}/12"))
    tags.save(str(path))
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def docx_bytes() -> bytes:
    return zip_bytes({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>"})


def xlsx_bytes() -> bytes:
    return zip_bytes({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"})


def pdf_bytes(text: str = "Facture") -> bytes:
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\n% " + text.encode() + b"\ntrailer<<>>\n%%EOF\n"


def exe_bytes(dll: bool = False) -> bytes:
    data = bytearray(1024)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", data, 0x80 + 22, 0x2000 if dll else 0x0102)
    return bytes(data)


def ts(year, month, day, hour=12, minute=0) -> float:
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


def make_dataset(root: str | Path, seed: int = 42) -> dict:
    """Crée le jeu de test. Renvoie quelques chemins utiles aux tests."""
    random.seed(seed)
    root = Path(root)
    info: dict = {}
    dl = root / "Téléchargements"
    desk = root / "Bureau"
    old = root / "Vieux disque"

    # --- photos ---
    for i in range(6):
        _write(dl / f"IMG_2023071{i}_10223{i}.jpg",
               jpeg_bytes(color=(i * 30, 80, 90), date=f"2023:07:1{i} 10:22:3{i}", make="Canon",
                          model="Canon EOS 80D"), ts(2023, 7, 10 + i))
    for i in range(3):
        _write(old / "Vacances" / f"DSC0004{i}.JPG", jpeg_bytes(color=(1, 2, 3 + i), date=f"2019:08:0{3 + i} 15:00:00",
                                                                make="SONY", model="ILCE-6000"), ts(2019, 8, 3 + i))
    _write(old / "Lettres" / "Lettre résiliation.docx", docx_bytes(), ts(2018, 2, 2))
    _write(desk / "Été à Paris 🌞.jpg", jpeg_bytes(color=(250, 200, 10), date="2021:06:21 18:30:00",
                                                    make="Apple", model="iPhone 12"), ts(2021, 6, 21))
    info["photo_dup_src"] = _write(dl / "IMG_20230710_102230.jpg", jpeg_bytes(color=(0, 80, 90),
                                   date="2023:07:10 10:22:30", make="Canon", model="Canon EOS 80D"),
                                   ts(2023, 7, 10))
    # doublon exact (même contenu) dans un autre dossier
    _write(old / "Copie photos" / "IMG_20230710_102230 (1).jpg",
           (dl / "IMG_20230710_102230.jpg").read_bytes(), ts(2023, 7, 10))
    # --- captures d'écran ---
    _write(desk / "Screenshot 2024-03-12 143200.png", png_bytes((1920, 1080)), ts(2024, 3, 12))
    _write(desk / "Capture d'écran 2024-05-02 091500.png", png_bytes((1366, 768)), ts(2024, 5, 2))
    # --- autres images ---
    _write(dl / "logo.png", png_bytes((300, 200)), ts(2022, 1, 5))
    _write(dl / "maquette.psd", b"8BPS" + os.urandom(500), ts(2022, 2, 5))
    # mauvaise extension : un PNG nommé .jpg
    info["mismatch"] = _write(dl / "image_bizarre.jpg", png_bytes((100, 100)), ts(2022, 3, 3))
    # fichier sans extension (PDF)
    info["noext"] = _write(dl / "document_sans_extension", pdf_bytes("sans ext"), ts(2022, 4, 4))

    # --- vidéos ---
    _write(dl / "VID_20230801_101010.mp4", mp4_bytes(ts(2023, 8, 1, 10, 10)), ts(2023, 8, 1))
    _write(dl / "Inception.2010.1080p.BluRay.x264.mkv", mkv_bytes(), ts(2020, 1, 1))
    _write(dl / "Breaking.Bad.S01E02.720p.HDTV.mkv", mkv_bytes(), ts(2020, 1, 2))
    _write(dl / "Breaking.Bad.S01E02.720p.HDTV.fr.srt", b"1\n00:00:01,000 --> 00:00:02,000\nBonjour\n",
           ts(2020, 1, 2))
    _write(dl / "clip_drole.webm", b"\x1a\x45\xdf\xa3" + b"webm" + os.urandom(800), ts(2021, 9, 9))

    # --- musique ---
    tagged_mp3(dl / "track01.mp3", "Daft Punk", "Discovery", "One More Time", 1, ts(2015, 5, 5))
    tagged_mp3(dl / "AC-DC thunder.mp3", "AC/DC", "The Razors Edge", "Thunderstruck", 1, ts(2016, 6, 6))
    _write(dl / "Enregistrement 12.m4a", b"\x00\x00\x00\x18ftypM4A " + os.urandom(600), ts(2022, 6, 6))

    # --- documents ---
    _write(dl / "Facture EDF mars 2024.pdf", pdf_bytes(), ts(2024, 3, 20))
    _write(dl / "CV.docx", docx_bytes(), ts(2023, 1, 15))
    _write(desk / "notes.txt", "liste de courses\n".encode(), ts(2024, 1, 1))
    _write(desk / "budget.xlsx", xlsx_bytes(), ts(2024, 2, 1))
    _write(desk / "contacts.csv", b"nom;tel\nA;1\n", ts(2024, 2, 2))
    _write(dl / "livre.epub", zip_bytes({"mimetype": b"application/epub+zip", "content.opf": b"<x/>"}),
           ts(2020, 2, 2))
    _write(dl / "README", b"Ceci est un fichier texte sans extension.\n", ts(2021, 1, 1))

    # --- archives / logiciels ---
    _write(dl / "photos_vacances.zip", zip_bytes({"a.txt": b"a"}), ts(2022, 8, 8))
    _write(dl / "backup.7z", b"7z\xbc\xaf\x27\x1c" + os.urandom(100), ts(2022, 8, 9))
    _write(dl / "setup_vlc.exe", exe_bytes(), ts(2023, 3, 3))
    _write(dl / "police.ttf", b"\x00\x01\x00\x00\x00" + os.urandom(200), ts(2021, 3, 3))
    _write(dl / "raccourci.lnk", b"L\x00\x00\x00\x01\x14\x02\x00" + os.urandom(60), ts(2021, 3, 3))
    _write(dl / "inconnu.xyz", os.urandom(64), ts(2021, 3, 3))

    # --- projet de code (doit être déplacé en bloc) ---
    proj = old / "mon-site"
    _write(proj / "package.json", b'{"name": "mon-site"}', ts(2022, 5, 5))
    _write(proj / "src" / "index.js", b"console.log('hi')", ts(2022, 5, 5))
    _write(proj / "src" / "logo.png", png_bytes((32, 32)), ts(2022, 5, 5))
    (proj / ".git").mkdir(parents=True, exist_ok=True)
    _write(proj / ".git" / "HEAD", b"ref: refs/heads/main", ts(2022, 5, 5))
    info["project"] = proj

    # --- jeu installé (doit rester en place) ---
    game = old / "Jeux" / "SuperJeu"
    _write(game / "bin" / "game.exe", exe_bytes(), ts(2020, 1, 1))
    _write(game / "bin" / "engine.dll", exe_bytes(dll=True), ts(2020, 1, 1))
    _write(game / "data" / "music.ogg", b"OggS" + os.urandom(300), ts(2020, 1, 1))
    _write(game / "data" / "texture.png", png_bytes((16, 16)), ts(2020, 1, 1))
    info["game"] = game

    # --- sauvegardes de jeu (données d'application) ---
    saves = old / "Sauvegardes"
    for i in range(4):
        _write(saves / f"slot{i}.sav", os.urandom(128), ts(2021, 1, 1))
    info["saves"] = saves

    # --- dossier marqué « ne pas éclater » ---
    album = old / "Mariage Julie"
    _write(album / ".rangeo-keep", b"", ts(2020, 1, 1))
    _write(album / "photo1.jpg", jpeg_bytes(color=(9, 9, 9), date="2018:09:15 14:00:00"), ts(2018, 9, 15))
    info["kept"] = album

    # --- cas pièges ---
    _write(dl / "film_en_cours.mp4.crdownload", os.urandom(100), ts(2024, 1, 1))  # téléchargement en cours
    hidden = _write(dl / "cache_cache.dat", os.urandom(10), ts(2024, 1, 1))
    os.system(f'attrib +h "{hidden}"')
    readonly = _write(desk / "contrat_lecture_seule.pdf", pdf_bytes("RO"), ts(2023, 11, 11))
    os.chmod(readonly, 0o444)
    info["readonly"] = readonly
    deep = old / ("dossier_avec_un_nom_tres_long_" * 4) / ("sous_dossier_encore_plus_long_" * 4)
    info["long"] = _write(deep / ("fichier_au_nom_interminable_" * 3 + ".txt"), b"long path", ts(2020, 1, 1))
    return info


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Desktop",
                                                                 "Rangeo - dossier de test")
    make_dataset(target)
    print(f"Jeu de test créé dans : {target}")
