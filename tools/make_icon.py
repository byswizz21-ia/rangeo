"""Génère rangeo/resources/rangeo.ico (icône de l'exe) à partir du logo dessiné par l'application."""

from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> str:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    from rangeo.ui.icons import app_icon_pixmap

    QGuiApplication.instance() or QGuiApplication([])
    pm = app_icon_pixmap(256)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, "PNG")
    img = Image.open(io.BytesIO(bytes(buf.data())))
    out = os.path.join(ROOT, "rangeo", "resources", "rangeo.ico")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(os.path.join(ROOT, "rangeo", "resources", "rangeo.png"))
    return out


if __name__ == "__main__":
    print(main())
