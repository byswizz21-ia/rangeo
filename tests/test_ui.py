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
                "rangeo.ui.pages.rules", "rangeo.ui.pages.watch", "rangeo.ui.pages.settings"):
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

    # --- Surveillance ---
    watch = win.pages["watch"]
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
    for name in ("home", "sort", "duplicates", "rules", "watch", "history", "settings"):
        win.go(name)
        app.processEvents()

    assert not [e for e in errors if e[0] != "dialog"], errors
    assert not [e for e in errors if e[0] == "dialog"], errors
