"""Parcours complet de l'interface (hors écran) : aucune erreur ne doit se produire."""

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import dataset  # noqa: E402
from conftest import snapshot  # noqa: E402


@pytest.fixture
def ui(home, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from rangeo.core.config import Config
    from rangeo.core.engine import Engine
    from rangeo.ui import theme, widgets
    from rangeo.ui.context import AppContext
    from rangeo.ui.i18n import kind_label, set_language
    from rangeo.ui.main_window import MainWindow

    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: errors.append(exc))
    # boîtes de dialogue : on répond « oui » automatiquement
    for mod in ("rangeo.ui.widgets", "rangeo.ui.pages.sort", "rangeo.ui.pages.history", "rangeo.ui.pages.duplicates",
                "rangeo.ui.pages.rules", "rangeo.ui.pages.watch", "rangeo.ui.pages.settings", "rangeo.ui.pages.rename",
                "rangeo.ui.pages.analysis", "rangeo.ui.pages.automation"):
        m = sys.modules.get(mod) or __import__(mod, fromlist=["x"])
        for name in ("confirm",):
            if hasattr(m, name):
                monkeypatch.setattr(m, name, lambda *a, **k: True)
        if hasattr(m, "info"):
            monkeypatch.setattr(m, "info", lambda *a, **k: None)
        if hasattr(m, "error"):
            monkeypatch.setattr(m, "error", lambda *a, **k: errors.append(("dialog", a[1:3])))
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.set("first_run_done", True, save=False)
    set_language("fr")
    theme.apply_theme(app, "light")
    engine = Engine(cfg, kind_label=kind_label)
    ctx = AppContext(cfg, engine)
    win = MainWindow(ctx)
    win.show()

    def wait(cond, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        raise AssertionError("délai dépassé")

    yield app, win, ctx, wait, errors
    ctx.watch.stop_all()
    win._quitting = True
    win.tray.hide()
    win.close()
    engine.close()
    app.processEvents()


def test_full_ui_flow(ui, tmp_path):
    app, win, ctx, wait, errors = ui
    src, dst = tmp_path / "src", tmp_path / "dst"
    dataset.make_dataset(src)
    before = snapshot(src)

    # --- Ranger : analyse → aperçu → rangement ---
    win.go("sort", {"sources": [str(src)]})
    page = win.pages["sort"]
    page.setup.to_folder.setChecked(True)
    page.setup.dest.setText(str(dst))
    page.analyze()
    wait(lambda: page.stack.currentWidget() is page.preview)
    assert page.plan.active_ops()
    page.preview.model.set_filters(text="IMG")
    page.preview.model.set_filters(text="", mode="problems")
    page.preview.model.set_filters(mode="all")
    page.preview.table.selectRow(0)
    page.preview._row_selected(page.preview.model.index(0, 0), None)
    page.run()
    wait(lambda: page.stack.currentWidget() is page.result)
    assert page.result.result.failed == 0
    sid = page.result.result.session_id

    # --- Historique : annulation ---
    win.go("history", {"session": sid})
    hist = win.pages["history"]
    assert hist.session.id == sid
    hist.undo()
    wait(lambda: not ctx.busy)
    assert snapshot(src) == before

    # --- Doublons ---
    dup = win.pages["duplicates"]
    win.go("duplicates", {"sources": [str(src)]})
    dup.search()
    wait(lambda: dup.stack.currentWidget() is dup.results_page)
    assert dup.groups
    dup.apply()
    wait(lambda: dup.stack.currentWidget() is dup.result_step)

    # --- Règles : modification enregistrée ---
    rules = win.pages["rules"]
    win.go("rules")
    rules.list.setCurrentRow(4)
    rules.editor.target.setText("Images/Mes photos/{annee}")
    rules._flush()
    rules._save_timer.timeout.emit()
    assert any(r["target"] == "Images/Mes photos/{annee}" for r in ctx.config.profile("general")["rules"])
    rules.tabs.setCurrentIndex(1)
    rules._fill_tree()

    # --- Doublons ressemblants ---
    dup.back()
    dup.mode.setCurrentIndex(dup.mode.findData("images"))
    dup.search()
    wait(lambda: dup.stack.currentWidget() is dup.results_page)

    # --- Renommer ---
    ren = win.pages["rename"]
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("Copie de rapport_final.pdf", "notes_reunion (1).txt"):
        (docs / name).write_bytes(name.encode())
    win.go("rename", {"sources": [str(docs)]})
    wait(lambda: bool(ren.entries))
    ren.separators.setCurrentIndex(ren.separators.findData("spaces"))
    ren.refresh_preview()
    assert sorted(i.new_name for i in ren.model.items) == ["notes reunion.txt", "rapport final.pdf"]
    ren.apply()
    wait(lambda: not ctx.busy and (docs / "rapport final.pdf").exists())

    # --- Analyse ---
    ana = win.pages["analysis"]
    win.go("analysis", {"path": str(tmp_path)})
    ana.start()
    wait(lambda: ana.stack.currentIndex() == 2)
    assert ana.report.total_files > 0
    if ana.report.root.children:
        ana._open_node(ana.report.root.children[0])
    ana._check_corrupt()
    wait(lambda: ana.stack.currentIndex() == 2 and not ctx.busy)

    # --- Automatisation (sans créer de vraie tâche Windows) ---
    auto = win.pages["watch"]
    win.go("watch", {"tab": 1})
    auto.schedule.fill()
    win.go("watch", {"tab": 2})
    auto.devices.fill()
    win.go("watch", {"tab": 0})

    # --- Surveillance ---
    watch = win.pages["watch"].watch
    win.go("watch")
    watch._add(str(tmp_path))
    assert ctx.config.get("watch")
    watch.remove(watch.rows[0])

    # --- Paramètres : changement de thème et de langue (reconstruction de l'interface) ---
    win.go("settings")
    ctx.config.set("theme", "dark")
    ctx.config.set("language", "en")
    win.rebuild()
    app.processEvents()
    for name in ("home", "sort", "duplicates", "rename", "analysis", "rules", "watch", "history", "settings"):
        win.go(name)
        app.processEvents()

    assert not [e for e in errors if e[0] != "dialog"], errors
    assert not [e for e in errors if e[0] == "dialog"], errors


def test_running_task_is_stopped_cleanly_on_quit():
    """Quitter pendant une tâche : elle est annulée à un point sûr et on attend sa fin."""
    from PySide6.QtWidgets import QApplication

    from rangeo.core.models import Cancelled, TaskControl
    from rangeo.ui import workers

    QApplication.instance() or QApplication([])

    seen = []

    def long_job(control):
        try:
            for _ in range(600):
                control.checkpoint()
                time.sleep(0.05)
        except Cancelled:
            seen.append("annulée proprement")
            raise

    control = TaskControl()
    control.pause()  # même en pause
    workers.run_task(long_job, control)
    time.sleep(0.2)
    assert workers.shutdown(10)
    assert seen == ["annulée proprement"]
