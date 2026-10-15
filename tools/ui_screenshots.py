"""Captures d'écran de toutes les pages (rendu hors écran) – sert à vérifier l'interface.

Usage : python tools/ui_screenshots.py <dossier_sortie> [fr|en] [light|dark]
"""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


def main() -> None:
    out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, ".smoke", "shots"))
    lang = sys.argv[2] if len(sys.argv) > 2 else "fr"
    mode = sys.argv[3] if len(sys.argv) > 3 else "light"
    work = os.path.join(ROOT, ".smoke", "ui")

    def force(func, path, _exc):
        os.chmod(path, 0o666)
        func(path)

    if os.path.exists(work):
        shutil.rmtree("\\\\?\\" + work, onexc=force)
    os.makedirs(out, exist_ok=True)
    os.environ["RANGEO_HOME"] = os.path.join(work, "home")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from dataset import make_dataset

    src = os.path.join(work, "Mon disque")
    make_dataset(src)

    from PySide6.QtWidgets import QApplication

    from rangeo.core.config import Config
    from rangeo.core.engine import Engine, SortRequest
    from rangeo.core.models import TaskControl
    from rangeo.ui import icons, theme
    from rangeo.ui.context import AppContext
    from rangeo.ui.i18n import kind_label, set_language
    from rangeo.ui.main_window import MainWindow

    app = QApplication([])
    cfg = Config()
    cfg.set("language", lang, save=False)
    cfg.set("theme", mode, save=False)
    cfg.set("first_run_done", True, save=False)
    cfg.set("watch", [{"path": src, "profile": "downloads", "enabled": True, "recursive": False, "delay": 5}],
            save=False)
    cfg.set("last_sources", [src], save=False)
    cfg.set("last_destination", os.path.join(work, "Rangement"))
    set_language(lang)
    theme.apply_theme(app, mode)
    engine = Engine(cfg, kind_label=kind_label)
    ctx = AppContext(cfg, engine)
    win = MainWindow(ctx)
    win.resize(1440, 900)
    win.show()

    def shot(name: str) -> None:
        app.processEvents()
        app.processEvents()
        win.grab().save(os.path.join(out, f"{name}.png"))

    # une vraie session pour remplir l'historique et l'accueil
    req = SortRequest(sources=[src], profile=cfg.profile("general"), destination=os.path.join(work, "Rangement"))
    scan, plan = engine.analyze(req, TaskControl())
    for name in ("home", "sort", "duplicates", "rules", "watch", "history", "settings"):
        win.go(name)
        shot(f"page_{name}")

    sort_page = win.pages["sort"]
    sort_page.scan, sort_page.plan = scan, plan
    sort_page.preview.set_plan(scan, plan)
    win.go("sort")
    sort_page.stack.setCurrentWidget(sort_page.preview)
    shot("sort_preview")
    sort_page.preview.tabs.setCurrentIndex(1)
    shot("sort_preview_left")
    sort_page.preview.tabs.setCurrentIndex(0)

    result = engine.execute(plan, TaskControl())
    sort_page.result.show_result(result, plan)
    sort_page.stack.setCurrentWidget(sort_page.result)
    shot("sort_result")
    ctx.sessionsChanged.emit()
    win.go("history")
    shot("history_after")
    win.go("home")
    shot("home_after")

    dup = win.pages["duplicates"]
    _scan, groups = engine.find_duplicates([os.path.join(work, "Rangement")], TaskControl())
    dup.groups = groups
    dup._fill()
    dup.stack.setCurrentWidget(dup.results_page)
    win.go("duplicates")
    shot("duplicates_results")

    rules = win.pages["rules"]
    win.go("rules")
    rules.list.setCurrentRow(4)
    shot("rules_editor")
    rules.tabs.setCurrentIndex(1)
    shot("rules_tree")
    win.close()
    engine.close()
    print("Captures :", out)


if __name__ == "__main__":
    main()
