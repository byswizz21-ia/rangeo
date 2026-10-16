"""Aperçu des médias : miniatures (images, vidéos) et petit lecteur audio / vidéo."""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QDialog, QSlider

from ..core import filetypes as ft
from . import icons, theme
from .i18n import fmt_duration, tr
from .widgets import button, hbox, label, vbox

VIDEO_EXT = ft.VIDEO_EXT
AUDIO_EXT = ft.AUDIO_EXT


def pil_to_pixmap(img) -> QPixmap:
    img = img.convert("RGB")
    data = img.tobytes()
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


def thumbnail(path: str, ext: str, width: int, height: int) -> QPixmap | None:
    """Miniature d'une image ou d'une vidéo (None si impossible)."""
    ext = (ext or "").lower()
    try:
        if ext in VIDEO_EXT:
            import av

            with av.open(path) as container:
                if not container.streams.video:
                    return None
                stream = container.streams.video[0]
                if container.duration:
                    container.seek(int(container.duration * 0.1), backward=True)
                for frame in container.decode(stream):
                    img = frame.to_image()
                    img.thumbnail((width * 2, height * 2))
                    return pil_to_pixmap(img).scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return None
        if ext in ("heic", "heif"):
            from PIL import Image

            from ..core.metadata import _ensure_pil

            _ensure_pil()
            with Image.open(path) as img:
                img.thumbnail((width * 2, height * 2))
                return pil_to_pixmap(img).scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0:
            size.scale(width * 2, height * 2, Qt.KeepAspectRatio)
            reader.setScaledSize(size)
        img = reader.read()
        if img.isNull():
            return None
        return QPixmap.fromImage(img).scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:
        return None


def is_playable(ext: str) -> bool:
    return (ext or "").lower() in VIDEO_EXT | AUDIO_EXT


class MediaPlayerDialog(QDialog):
    """Lecteur simple pour écouter / regarder un fichier avant de le ranger."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self.setWindowTitle(os.path.basename(path))
        self.setWindowIcon(icons.app_icon())
        is_video = os.path.splitext(path)[1][1:].lower() in VIDEO_EXT
        self.resize(760, 520 if is_video else 160)
        lay = vbox(self, (16, 14, 16, 14), 10)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        if is_video:
            self.video = QVideoWidget()
            self.video.setMinimumHeight(300)
            self.player.setVideoOutput(self.video)
            lay.addWidget(self.video, 1)
        else:
            lay.addWidget(label(os.path.basename(path), "SectionTitle"))
        row = hbox(spacing=8)
        self.play_btn = button("", "pause", on_click=self._toggle)
        row.addWidget(self.play_btn)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderMoved.connect(self.player.setPosition)
        row.addWidget(self.slider, 1)
        self.time = label("0 s", "Muted")
        row.addWidget(self.time)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(70)
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.7)
        row.addWidget(label("🔊"))
        row.addWidget(self.volume)
        lay.addLayout(row)
        self.error = label("", "Muted", wrap=True)
        lay.addWidget(self.error)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, d))
        self.player.positionChanged.connect(self._position)
        self.player.errorOccurred.connect(lambda *_: self.error.setText(tr("media.error")))
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def _toggle(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setIcon(icons.icon("play"))
        else:
            self.player.play()
            self.play_btn.setIcon(icons.icon("pause"))

    def _position(self, pos: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        self.time.setText(f"{fmt_duration(pos / 1000)} / {fmt_duration(self.player.duration() / 1000)}")

    def closeEvent(self, event) -> None:
        self.player.stop()
        super().closeEvent(event)


def play(path: str, parent=None) -> None:
    dlg = MediaPlayerDialog(path, parent)
    dlg.exec()


__all__ = ["thumbnail", "play", "is_playable", "theme"]
