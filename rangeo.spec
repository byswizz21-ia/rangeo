# -*- mode: python ; coding: utf-8 -*-
# Fichier de construction PyInstaller : python -m PyInstaller rangeo.spec

from PyInstaller.utils.hooks import collect_submodules

hidden = (
    collect_submodules("watchdog.observers")
    + collect_submodules("send2trash")
    + collect_submodules("av")
    + ["pillow_heif", "win32com.shell.shell", "win32com.client", "pythoncom", "pywintypes", "win32file",
       "win32security", "win32api", "numpy", "yaml", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"]
)

# modules Qt inutiles (réduit fortement la taille)
excluded = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtWebSockets",
    "PySide6.QtHttpServer", "PySide6.QtGraphs", "tkinter", "unittest", "pytest", "PyInstaller",
]

a = Analysis(
    ["rangeo/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[("rangeo/i18n", "rangeo/i18n"), ("rangeo/resources", "rangeo/resources")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excluded,
    noarchive=False,
)
# fichiers Qt inutiles pour une interface en widgets
_DROP = ("opengl32sw.dll", "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qt6opengl", "qpdf.dll",
         "/plugins/tls/", "/plugins/networkinformation/", "/plugins/generic/", "libcrypto-3-x64", "libssl-3-x64")


def _keep(entry) -> bool:
    name = entry[0].replace("\\", "/").lower()
    if any(d in name for d in _DROP):
        return False
    if "/translations/" in name and name.endswith(".qm"):
        return name.rsplit("/", 1)[-1] in ("qtbase_fr.qm", "qtbase_en.qm")
    return True


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Rangeo",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="rangeo/resources/rangeo.ico",
    version="tools/version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Rangeo")
