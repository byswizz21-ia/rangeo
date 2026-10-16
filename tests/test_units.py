"""Tests unitaires : détection des types, analyse des noms, règles, protections."""

import datetime as dt
import os

from rangeo.core import filetypes as ft
from rangeo.core import metadata as md
from rangeo.core import rules
from rangeo.core.fsutil import sanitize_component, unique_name
from rangeo.core.models import FileEntry
from rangeo.core.protect import Protection

import dataset


def test_sniff_signatures():
    assert ft.sniff(dataset.jpeg_bytes()) == "jpg"
    assert ft.sniff(dataset.png_bytes()) == "png"
    assert ft.sniff(dataset.pdf_bytes()) == "pdf"
    assert ft.sniff(dataset.docx_bytes()) == "docx"
    assert ft.sniff(dataset.xlsx_bytes()) == "xlsx"
    assert ft.sniff(dataset.exe_bytes()) == "exe"
    assert ft.sniff(dataset.exe_bytes(dll=True)) == "dll"
    assert ft.sniff(dataset.mp4_bytes(0)) == "mp4"
    assert ft.sniff(dataset.mkv_bytes()) == "mkv"
    assert ft.sniff(b"7z\xbc\xaf\x27\x1c\x00\x04") == "7z"
    assert ft.sniff(b"Hello world, simple texte\n") == "txt"
    assert ft.sniff(b"\x00\x01\x02\x03\xff\xfe\x00\x00binaire") is None


def test_compatible_extensions():
    assert ft.compatible("jpeg", "jpg")
    assert ft.compatible("cr2", "tif")
    assert ft.compatible("mov", "mp4")
    assert ft.compatible("docx", "zip")
    assert ft.compatible("py", "txt")
    assert not ft.compatible("jpg", "png")
    assert not ft.compatible("jpg", "heic")
    assert not ft.compatible("pdf", "exe")


def test_series_and_movies():
    s = md.parse_series("Breaking.Bad.S01E02.720p.HDTV")
    assert s == {"series": "Breaking Bad", "season": 1, "episode": 2}
    assert md.parse_series("Friends - 1x05 - The One")["episode"] == 5
    assert md.parse_series("S02E10", parent_dir="Saison 2", grand_dir="The Office")["series"] == "The Office"
    assert md.parse_series("Résumé 1920x1080") is None
    m = md.parse_movie("Inception.2010.1080p.BluRay.x264", 1000)
    assert m == {"title": "Inception", "release_year": 2010}
    assert md.parse_movie("Blade Runner 2049 (2017) 1080p", 10)["title"] == "Blade Runner 2049"
    # une vidéo de vacances n'est PAS un film
    assert md.parse_movie("Vacances 2019", 50_000_000) is None
    assert md.parse_movie("Vacances (2019)", 50_000_000) is None


def test_dates_in_names():
    ts = md.date_from_name("IMG_20240312_143200")
    assert dt.datetime.fromtimestamp(ts) == dt.datetime(2024, 3, 12, 14, 32, 0)
    ts = md.date_from_name("Screenshot 2024-03-12 at 14.32.00")
    assert dt.datetime.fromtimestamp(ts).date() == dt.date(2024, 3, 12)
    assert md.date_from_name("IMG-20230105-WA0001") is not None
    assert md.date_from_name("facture 123456") is None
    assert md.date_from_name("version 2023.99.99") is None


def _entry(name="photo.jpg", kind="photo", meta=None, date=None, folder=r"C:\src\Vacances"):
    stem, ext = os.path.splitext(name)
    return FileEntry(dir=folder, name=name, size=1234, mtime=1_600_000_000, ctime=1_600_000_000,
                     ext=ext.lstrip(".").lower(), kind=kind, meta=meta or {},
                     date=date or dt.datetime(2023, 7, 14, 10).timestamp(), root=r"C:\src")


def test_templates():
    e = _entry(meta={"camera": "Canon EOS 80D"})
    d = rules.render_target_dir("Images/Photos/{annee}/{annee}-{mois} {mois_nom}", e, r"D:\Rangement", "fr")
    assert d == r"D:\Rangement\Images\Photos\2023\2023-07 Juillet"
    d = rules.render_target_dir("Pictures/{year}/{month_name}", e, r"D:\R", "en")
    assert d == r"D:\R\Pictures\2023\July"
    # variable manquante dans un groupe optionnel : le groupe disparaît
    movie = _entry("Film.mkv", "movie", {"title": "Le Film"})
    assert rules.render_target_dir("Films/{titre}[ ({annee_sortie})]", movie, "D:\\R", "fr") == r"D:\R\Films\Le Film"
    movie.meta["release_year"] = 1999
    assert rules.render_target_dir("Films/{titre}[ ({annee_sortie})]", movie, "D:\\R", "fr") == \
        r"D:\R\Films\Le Film (1999)"
    # valeurs nettoyées : « AC/DC » ne crée pas de sous-dossier
    song = _entry("a.mp3", "music", {"artist": "AC/DC", "album": "Back: in Black?"})
    assert rules.render_target_dir("Musique/{artiste}/{album}", song, "D:\\R", "fr") == \
        r"D:\R\Musique\AC-DC\Back - in Black"
    # pas d'album : valeur de secours
    song2 = _entry("b.mp3", "music", {"artist": "X"})
    assert rules.render_target_dir("Musique/{artiste}/{album}", song2, "D:\\R", "fr").endswith("Album inconnu")
    # renommage : si une variable manque, on garde le nom d'origine
    assert rules.render_name("{piste} - {titre}", song2, "fr") == "b.mp3"
    song2.meta.update(track=3, title="Titre")
    assert rules.render_name("{piste} - {titre}", song2, "fr") == "03 - Titre.mp3"
    assert rules.render_name("{nom}_{compteur}", song2, "fr", counter="007") == "b_007.mp3"
    # « .. » interdit : on ne sort jamais du dossier de destination
    assert rules.render_target_dir("../../Windows", song2, "D:\\R", "fr") == r"D:\R\Windows"
    assert rules.unknown_variables("{annee}/{inconnue}") == ["inconnue"]


def test_conditions():
    e = _entry("Facture EDF.pdf", "pdf")
    e.size = 5 * 1024 * 1024
    assert rules.eval_condition(e, {"field": "name", "op": "contains", "value": "facture"})
    assert rules.eval_condition(e, {"field": "ext", "op": "in_list", "value": "pdf, .docx"})
    assert rules.eval_condition(e, {"field": "size", "op": "gt", "value": "1 Mo"})
    assert not rules.eval_condition(e, {"field": "size", "op": "gt", "value": "10 Mo"})
    assert rules.eval_condition(e, {"field": "name", "op": "matches", "value": r"^facture\s+\w+"})
    assert rules.eval_condition(e, {"field": "date", "op": "in_year", "value": 2023})
    assert rules.eval_condition(e, {"field": "date", "op": "between", "value": ["2023-01-01", "2023-12-31"]})
    group = {"match": "any", "conditions": [{"field": "ext", "op": "equals", "value": "doc"},
                                            {"field": "kind", "op": "is", "value": "pdf"}]}
    assert rules.eval_condition(e, group)
    rule = {"match": "all", "conditions": [{"field": "name", "op": "matches", "value": "("}], "action": "move",
            "target": "x"}
    assert "rules.err_regex" in rules.validate_rule(rule)


def test_sanitize_and_unique():
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("a:b*c?") == "a -bc"
    assert sanitize_component("nom.  ") == "nom"
    assert len(sanitize_component("x" * 500)) <= 180
    assert unique_name("a.txt", {"a.txt"}) == "a (2).txt"
    assert unique_name("a.txt", {"a.txt", "a (2).txt"}) == "a (3).txt"


def test_protection():
    p = Protection()
    windir = os.environ.get("WINDIR", r"C:\Windows")
    assert p.source_problem(windir)
    assert p.source_problem(os.path.join(windir, "System32"))
    assert p.source_problem(os.environ["SystemDrive"] + "\\")
    assert p.source_problem(os.environ["USERPROFILE"]) == "protect.profile_root"
    assert p.source_problem(os.path.join(os.environ["USERPROFILE"], "Downloads")) is None
    assert p.is_protected(r"D:\$Recycle.Bin\S-1-5")
    assert p.is_protected(r"E:\System Volume Information")
    assert not p.is_protected(r"D:\Photos\Recovery")
    assert p.destination_problem(os.environ["APPDATA"])


def test_config_two_processes_do_not_erase_each_other(tmp_path):
    """L'interface et un rangement planifié enregistrent chacun leurs réglages : aucun ne perd ceux de l'autre."""
    from rangeo.core.config import Config

    gui = Config(tmp_path)
    gui.save()
    job = Config(tmp_path)  # le rangement planifié démarre pendant que l'interface est ouverte
    gui.set("conflict", "skip")
    gui.save_profile({**gui.profile("general"), "name": "Mon profil"})
    job.set("last_destination", r"D:\Rangé")  # ne doit pas remettre « conflict » ni le profil à l'ancienne valeur
    fresh = Config(tmp_path)
    assert fresh.get("conflict") == "skip"
    assert fresh.get("last_destination") == r"D:\Rangé"
    assert fresh.profile("general")["name"] == "Mon profil"
    # suppression d'un profil d'un côté, ajout de l'autre
    extra = {**gui.profile("general"), "id": "extra", "name": "Extra"}
    gui.save_profile(extra)
    job.load()
    job.delete_profile("extra")
    gui.set("theme", "dark")
    fresh = Config(tmp_path)
    assert "extra" not in [p["id"] for p in fresh.profiles]
    assert fresh.get("theme") == "dark"
    # remplacement complet (assistant) : seuls les nouveaux profils restent
    gui.replace_profiles([{**fresh.profile("general"), "id": "seul", "name": "Seul"}])
    assert [p["id"] for p in Config(tmp_path).profiles] == ["seul"]
