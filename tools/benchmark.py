"""Mesure des performances : scan, analyse, plan, rangement, sur N fichiers générés.

Usage : python tools/benchmark.py [nombre_de_fichiers]
"""

from __future__ import annotations

import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

EXTS = ["pdf", "docx", "txt", "jpg", "png", "mp3", "mp4", "zip", "xlsx", "csv", "dat", "xyz", "py", "epub"]


def generate(root: str, n: int) -> None:
    import dataset

    png = dataset.png_bytes((8, 8))
    jpg = dataset.jpeg_bytes((8, 8), date="2022:05:04 10:00:00", make="Canon", model="EOS")
    payload = {"png": png, "jpg": jpg, "pdf": dataset.pdf_bytes(), "mp3": dataset.mp3_bytes(2)}
    per_dir = 200
    for i in range(n):
        d = os.path.join(root, f"dossier_{i // (per_dir * 20):03d}", f"sous_{i // per_dir:05d}")
        if i % per_dir == 0:
            os.makedirs(d, exist_ok=True)
        ext = EXTS[i % len(EXTS)]
        name = f"IMG_2022050{i % 9 + 1}_{i:07d}.{ext}" if ext in ("jpg", "png") else f"fichier_{i:07d}.{ext}"
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(payload.get(ext, b"contenu %d" % i))


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    work = os.path.join(ROOT, ".smoke", "bench")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    os.environ["RANGEO_HOME"] = os.path.join(work, "home")
    src = os.path.join(work, "src")
    t = time.perf_counter()
    generate(src, n)
    print(f"Génération de {n:,} fichiers : {time.perf_counter() - t:.1f} s")

    from rangeo.core.classify import classify
    from rangeo.core.config import Config
    from rangeo.core.engine import Engine, SortRequest
    from rangeo.core.models import TaskControl
    from rangeo.core.scanner import scan

    cfg = Config()
    eng = Engine(cfg)
    req = SortRequest(sources=[src], profile=cfg.profile("general"), destination=os.path.join(work, "dst"))
    opts = eng._scan_options(req)

    t = time.perf_counter()
    res = scan([src], opts, TaskControl())
    t_scan = time.perf_counter() - t
    print(f"Scan          : {t_scan:6.2f} s  ({len(res.entries) / t_scan:,.0f} fichiers/s)")

    t = time.perf_counter()
    classify(res.entries, eng._classify_options(), TaskControl(), eng.index)
    t_cls = time.perf_counter() - t
    print(f"Analyse       : {t_cls:6.2f} s  (métadonnées des photos/musiques/vidéos lues)")

    t = time.perf_counter()
    plan = eng.plan(req, res, TaskControl())
    t_plan = time.perf_counter() - t
    print(f"Plan          : {t_plan:6.2f} s  ({len(plan.active_ops()):,} opérations)")

    t = time.perf_counter()
    res2 = scan([src], opts, TaskControl())
    classify(res2.entries, eng._classify_options(), TaskControl(), eng.index)
    print(f"2e analyse    : {time.perf_counter() - t:6.2f} s  (avec le cache)")

    t = time.perf_counter()
    result = eng.execute(plan, TaskControl())
    t_exec = time.perf_counter() - t
    print(f"Rangement     : {t_exec:6.2f} s  ({result.files / t_exec:,.0f} fichiers/s, {result.failed} erreurs)")

    t = time.perf_counter()
    undo = eng.undo(result.session_id, TaskControl())
    print(f"Annulation    : {time.perf_counter() - t:6.2f} s  ({undo.undone:,} annulés, {undo.failed} erreurs)")
    eng.close()


if __name__ == "__main__":
    main()
