"""Autotest (rangeo --selftest <dossier>) : vérifie que toutes les briques fonctionnent, y compris dans l'exe."""

from __future__ import annotations

import io
import json
import os
import struct
import time
import traceback


def _jpeg() -> bytes:
    from PIL import Image

    im = Image.new("RGB", (32, 24), (200, 100, 50))
    exif = Image.Exif()
    exif[271], exif[272] = "Canon", "Canon EOS 80D"
    exif.get_ifd(0x8769)[36867] = "2021:06:21 18:30:00"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _mp3(path: str) -> None:
    from mutagen.id3 import ID3, TALB, TIT2, TPE1, TRCK

    with open(path, "wb") as fh:
        fh.write((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 10)
    tags = ID3()
    tags.add(TPE1(encoding=3, text="Artiste Test"))
    tags.add(TALB(encoding=3, text="Album Test"))
    tags.add(TIT2(encoding=3, text="Titre"))
    tags.add(TRCK(encoding=3, text="3"))
    tags.save(path)


def run(folder: str) -> dict:
    from .config import Config
    from .engine import Engine, SortRequest
    from .models import TaskControl
    from . import trash

    report: dict = {"ok": False, "steps": {}}
    base = os.path.join(os.path.abspath(folder), f"rangeo-selftest-{int(time.time())}")
    src, dst = os.path.join(base, "src"), os.path.join(base, "dst")
    os.makedirs(src)
    try:
        with open(os.path.join(src, "IMG_20210621_183000.jpg"), "wb") as fh:
            fh.write(_jpeg())
        _mp3(os.path.join(src, "piste.mp3"))
        with open(os.path.join(src, "facture.pdf"), "wb") as fh:
            fh.write(b"%PDF-1.4\n%%EOF\n")
        report["steps"]["dataset"] = "ok"

        os.environ.setdefault("RANGEO_HOME", os.path.join(base, "home"))
        cfg = Config()
        eng = Engine(cfg)
        req = SortRequest(sources=[src], profile=cfg.profile("general"), destination=dst)
        scan, plan = eng.analyze(req, TaskControl())
        kinds = sorted(e.kind for e in scan.entries)
        report["steps"]["analyze"] = kinds
        assert kinds == ["music", "pdf", "photo"], kinds
        result = eng.execute(plan, TaskControl())
        assert result.failed == 0 and result.files == 3, result.summary()
        photo = os.path.join(dst, "Images", "Photos", "2021", "2021-06 Juin", "IMG_20210621_183000.jpg")
        music = os.path.join(dst, "Musique", "Artiste Test", "Album Test", "03 - Titre.mp3")
        assert os.path.isfile(photo) and os.path.isfile(music)
        report["steps"]["execute"] = "ok"
        undo = eng.undo(result.session_id, TaskControl())
        assert undo.failed == 0 and os.path.isfile(os.path.join(src, "piste.mp3"))
        report["steps"]["undo"] = "ok"

        probe = os.path.join(src, "corbeille.txt")
        with open(probe, "w") as fh:
            fh.write("x")
        info = trash.trash(probe, 0, 1)
        trash.restore(probe, info)
        assert os.path.isfile(probe)
        report["steps"]["trash"] = info.get("method")

        # --- V2 : géocodage, empreintes, renommage, analyse du disque ---
        from . import diskreport, geo, rename, similar

        assert geo.reverse(48.8584, 2.2945) == ("Paris", "FR")
        report["steps"]["geo"] = "ok"
        assert similar.image_signature(os.path.join(src, "IMG_20210621_183000.jpg")) is not None
        import av
        import numpy as np

        wav = os.path.join(src, "son.flac")
        with av.open(wav, "w", format="flac") as container:
            stream = container.add_stream("flac", rate=11025)
            stream.layout = "mono"
            t = np.arange(11025 * 8) / 11025
            pcm = (np.sin(2 * np.pi * 440 * t) * np.sin(2 * np.pi * 0.5 * t) * 20000).astype(np.int16)
            frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = 11025
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        assert similar.audio_fingerprint(wav) is not None
        report["steps"]["fingerprints"] = "ok"
        scan2 = eng.scan_and_classify(SortRequest(sources=[src], profile=cfg.profile(None), in_place=True), TaskControl())
        items = rename.preview(scan2.entries, rename.RenameOptions(case="upper"))
        assert any(i.changed for i in items)
        report["steps"]["rename"] = "ok"
        disk = diskreport.analyze_disk(src, TaskControl())
        assert disk.total_files >= 3
        report["steps"]["analysis"] = "ok"
        eng.close()
        report["ok"] = True
    except Exception as exc:  # rapport complet pour le diagnostic
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    with open(os.path.join(base, "selftest.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    report["folder"] = base
    return report


__all__ = ["run", "struct"]
