"""Démarrage de l'application (interface graphique, ligne de commande, installation)."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

log = logging.getLogger("rangeo")

SERVER_NAME = f"Rangeo-{getpass.getuser()}"


def _set_app_id() -> None:
    """Icône correcte dans la barre des tâches Windows."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Rangeo.App.1")
    except Exception:
        pass


def _send_to_running(message: str) -> bool:
    """Une seule instance à la fois : on transmet la demande à celle qui tourne déjà."""
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(400):
        sock.write(message.encode("utf-8"))
        sock.flush()
        sock.waitForBytesWritten(1000)
        sock.disconnectFromServer()
        return True
    return False


def _start_server(on_message):
    from PySide6.QtNetwork import QLocalServer

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    def handle():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.waitForReadyRead(500)
            data = bytes(conn.readAll()).decode("utf-8", "ignore")
            conn.disconnectFromServer()
            on_message(data or "show")

    server.newConnection.connect(handle)
    return server


def _uninstall_gui(app) -> int:
    from PySide6.QtWidgets import QCheckBox, QMessageBox

    from .core import winintegration
    from .core.config import Config
    from .ui import icons, theme
    from .ui.i18n import set_language, tr

    config = Config()
    set_language(config.get("language"))
    theme.apply_theme(app, config.get("theme"))
    _send_to_running("quit")
    box = QMessageBox()
    box.setWindowIcon(icons.app_icon())
    box.setWindowTitle("Rangeo")
    box.setIcon(QMessageBox.Question)
    box.setText(f"<b>{tr('install.uninstall_title')}</b>")
    box.setInformativeText(tr("install.uninstall_text"))
    data = QCheckBox(tr("install.remove_data"))
    box.setCheckBox(data)
    ok = box.addButton(tr("install.uninstall"), QMessageBox.AcceptRole)
    box.addButton(tr("common.cancel"), QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() is not ok:
        return 1
    ids = [j.get("id") for j in config.get("schedules", []) or []]
    winintegration.uninstall(remove_data=data.isChecked(), schedule_ids=ids)
    QMessageBox.information(None, "Rangeo", tr("install.uninstalled"))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .cli import COMMANDS

    if argv and argv[0] in COMMANDS:
        from .core.logsetup import setup_logging

        setup_logging(False)
        from . import cli

        return cli.main(argv)

    parser = argparse.ArgumentParser(prog="rangeo")
    parser.add_argument("--tray", action="store_true", help="démarrer réduit près de l'horloge")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--selftest", metavar="DOSSIER", help="autotest sans interface (diagnostic)")
    parser.add_argument("--open-sort", metavar="DOSSIER", help="ouvrir la page Ranger avec ce dossier")
    parser.add_argument("--uninstall", action="store_true", help="désinstaller Rangeo")
    args, _unknown = parser.parse_known_args(argv)

    from .core.logsetup import setup_logging

    setup_logging(args.debug)
    if args.selftest:
        from .core import selftest

        report = selftest.run(args.selftest)
        log.info("Autotest : %s", report)
        return 0 if report["ok"] else 1
    _set_app_id()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Rangeo")
    app.setOrganizationName("Rangeo")
    app.setQuitOnLastWindowClosed(False)
    if args.uninstall:
        return _uninstall_gui(app)
    message = f"sort:{os.path.normpath(args.open_sort)}" if args.open_sort else "show"
    if _send_to_running(message):
        return 0

    from . import __version__
    from .core.config import Config
    from .core.engine import Engine
    from .ui import icons, theme
    from .ui.context import AppContext
    from .ui.i18n import kind_label, set_language
    from .ui.main_window import MainWindow
    from .ui.wizard import FirstRunWizard

    log.info("Démarrage de Rangeo %s", __version__)
    config = Config()
    set_language(config.get("language"))
    theme.apply_theme(app, config.get("theme"))
    app.setWindowIcon(icons.app_icon())

    if not config.get("first_run_done"):
        wizard = FirstRunWizard(config)
        if wizard.exec() != wizard.DialogCode.Accepted:
            config.set("first_run_done", True)
        set_language(config.get("language"))
        theme.apply_theme(app, config.get("theme"))

    engine = Engine(config, kind_label=kind_label)
    try:
        engine.index.prune(365)
    except Exception:
        pass
    ctx = AppContext(config, engine)
    window = MainWindow(ctx)
    server = _start_server(window.handle_message)  # noqa: F841 - doit rester vivant
    if not args.tray:
        window.show()
    if args.open_sort:
        QTimer.singleShot(100, lambda: window.handle_message(message))
    QTimer.singleShot(300, ctx.watch.reload)
    QTimer.singleShot(700, window.check_recovery)
    code = app.exec()
    try:
        ctx.watch.stop_all()
    finally:
        engine.close()
    log.info("Arrêt de Rangeo")
    return code


if __name__ == "__main__":
    sys.exit(main())
