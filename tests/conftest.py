import hashlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv("RANGEO_HOME", str(path))
    return path


@pytest.fixture
def engine(home):
    from rangeo.core.config import Config
    from rangeo.core.engine import Engine

    eng = Engine(Config())
    yield eng
    eng.close()


@pytest.fixture(autouse=True)
def safe_trash(request, monkeypatch, tmp_path):
    """Par défaut, la « corbeille » des tests est un dossier temporaire (pas la vraie corbeille)."""
    if request.node.get_closest_marker("realtrash"):
        return
    from rangeo.core import trash

    monkeypatch.setattr(trash, "can_use_recycle_bin", lambda path, size=None: False)
    monkeypatch.setattr(
        trash,
        "quarantine_target",
        lambda path, sid: str(tmp_path / "quarantaine" / str(sid) / os.path.splitdrive(path)[1].lstrip("\\")),
    )


def lp(path) -> str:
    path = str(path)
    return "\\\\?\\" + path if len(path) > 240 else path


def snapshot(root) -> dict:
    """{chemin relatif: (taille, sha1)} pour tous les fichiers (y compris cachés)."""
    out = {}
    root = os.path.abspath(str(root))
    for dirpath, _dirs, files in os.walk("\\\\?\\" + root):
        for name in files:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                digest = hashlib.sha1(fh.read()).hexdigest()
            rel = os.path.relpath(full.replace("\\\\?\\", ""), root)
            out[rel] = (os.path.getsize(full), digest)
    return out


def all_files(root) -> list[str]:
    return sorted(snapshot(root))
