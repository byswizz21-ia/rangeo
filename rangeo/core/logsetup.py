"""Journaux techniques (fichiers tournants dans %APPDATA%\\Rangeo\\logs)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import appdirs


def setup_logging(debug: bool = False) -> None:
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s")
    handler = RotatingFileHandler(appdirs.logs_dir() / "rangeo.log", maxBytes=5 * 1024 * 1024, backupCount=5,
                                  encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if not getattr(sys, "frozen", False) and sys.stderr:
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(fmt)
        root.addHandler(console)

    def excepthook(exc_type, exc, tb):
        logging.getLogger("rangeo").critical("Erreur non gérée", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook
