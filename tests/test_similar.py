"""Doublons ressemblants : images, musiques, vidéos (sans faux positifs)."""

import os

import media
from rangeo.core.models import TaskControl


def names(groups):
    return sorted(sorted(e.name for e in g.files) for g in groups)


def test_similar_images(engine, tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    a, b = media.picture(1), media.picture(2)
    media.save_jpeg(a, root / "A.jpg", 92)
    media.save_jpeg(a, root / "A_petit.jpg", 60, scale=0.5)
    a.save(root / "A_copie.png")
    media.save_jpeg(b, root / "B.jpg", 92)
    media.save_jpeg(media.picture(3), root / "C.jpg", 92)
    _scan, groups = engine.find_similar([str(root)], "images", "normal", TaskControl())
    assert names(groups) == [["A.jpg", "A_copie.png", "A_petit.jpg"]]
    keep = groups[0].keep
    assert keep.name in ("A.jpg", "A_copie.png")  # pleine résolution, pas la version réduite
    # 2e recherche : empreintes reprises du cache
    _scan, groups2 = engine.find_similar([str(root)], "images", "normal", TaskControl())
    assert names(groups2) == names(groups)


def test_same_song_different_formats(engine, tmp_path):
    root = tmp_path / "musique"
    root.mkdir()
    song, other = media.melody(10), media.melody(20)
    media.write_audio(str(root / "titre.mp3"), song, "libmp3lame", bitrate=128_000)
    media.write_audio(str(root / "titre.flac"), song, "flac")
    media.write_audio(str(root / "titre_22k.mp3"), song, "libmp3lame", out_rate=22050, bitrate=64_000)
    media.write_audio(str(root / "autre.mp3"), other, "libmp3lame", bitrate=128_000)
    _scan, groups = engine.find_similar([str(root)], "music", "normal", TaskControl())
    assert names(groups) == [["titre.flac", "titre.mp3", "titre_22k.mp3"]]
    assert groups[0].keep.name == "titre.flac"  # meilleure qualité


def test_similar_videos(engine, tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    media.write_video(str(root / "vacances.mp4"), 5, (320, 240), bitrate=800_000)
    media.write_video(str(root / "vacances_petit.mp4"), 5, (160, 120), bitrate=150_000)
    media.write_video(str(root / "autre.mp4"), 6, (320, 240))
    _scan, groups = engine.find_similar([str(root)], "videos", "normal", TaskControl())
    assert names(groups) == [["vacances.mp4", "vacances_petit.mp4"]]
    assert groups[0].keep.name == "vacances.mp4"


def test_similar_plan_moves_extra_copies(engine, tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    a = media.picture(7)
    media.save_jpeg(a, root / "grande.jpg", 92)
    media.save_jpeg(a, root / "petite.jpg", 50, scale=0.4)
    _scan, groups = engine.find_similar([str(root)], "images", "normal", TaskControl())
    plan = engine.duplicate_plan(groups, "move")
    result = engine.execute(plan, TaskControl(), kind="duplicates")
    assert result.failed == 0
    assert (root / "grande.jpg").exists() and not (root / "petite.jpg").exists()
    engine.undo(result.session_id, TaskControl())
    assert (root / "petite.jpg").exists()
    assert os.path.getsize(root / "petite.jpg") > 0
