"""Génération de vraies photos, musiques et vidéos pour les tests (avec variantes)."""

from __future__ import annotations

import math
import random

import numpy as np


def picture(seed: int, size=(640, 480)):
    """Image « naturelle » déterministe : dégradé + formes."""
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    c1 = [rnd.randint(0, 255) for _ in range(3)]
    c2 = [rnd.randint(0, 255) for _ in range(3)]
    for y in range(h):
        t = y / h
        row = tuple(int(c1[k] * (1 - t) + c2[k] * t) for k in range(3))
        for x in range(w):
            px[x, y] = row
    draw = ImageDraw.Draw(img)
    for _ in range(14):
        x0, y0 = rnd.randint(0, w), rnd.randint(0, h)
        r = rnd.randint(20, 160)
        color = tuple(rnd.randint(0, 255) for _ in range(3))
        if rnd.random() < 0.5:
            draw.ellipse((x0 - r, y0 - r, x0 + r, y0 + r), fill=color)
        else:
            draw.rectangle((x0 - r, y0 - r // 2, x0 + r, y0 + r // 2), fill=color)
    return img


def save_jpeg(img, path, quality=90, scale=1.0):
    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    img.save(path, "JPEG", quality=quality)


def melody(seed: int, seconds: float = 30.0, rate: int = 44100) -> np.ndarray:
    rnd = random.Random(seed)
    notes = [rnd.choice([220, 247, 262, 294, 330, 349, 392, 440, 494, 523, 587, 659]) for _ in range(int(seconds * 4))]
    t = np.arange(int(rate / 4)) / rate
    parts = []
    for f in notes:
        env = np.minimum(1.0, np.minimum(t * 40, (t[-1] - t) * 20 + 0.05))
        tone = 0.5 * np.sin(2 * math.pi * f * t) + 0.25 * np.sin(2 * math.pi * 2 * f * t)
        parts.append(tone * env)
    sig = np.concatenate(parts)
    noise = np.random.default_rng(seed).normal(0, 0.02, sig.size)
    return np.clip(sig + noise, -1, 1).astype(np.float32)


def write_audio(path: str, samples: np.ndarray, codec: str, rate: int = 44100, out_rate: int | None = None,
                bitrate: int | None = None) -> None:
    import av

    out_rate = out_rate or rate
    fmt = {"libmp3lame": "mp3", "flac": "flac", "pcm_s16le": "wav"}[codec]
    with av.open(path, "w", format=fmt) as container:
        stream = container.add_stream(codec, rate=out_rate)
        stream.layout = "mono"
        if bitrate:
            stream.bit_rate = bitrate
        pcm = (samples * 32767).astype(np.int16).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        frame.sample_rate = rate
        resampler = av.AudioResampler(format=stream.codec_context.format.name, layout="mono", rate=out_rate)
        for f in resampler.resample(frame) + resampler.resample(None):
            for packet in stream.encode(f):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def write_video(path: str, seed: int, size=(320, 240), seconds: int = 4, fps: int = 10, bitrate: int = 400_000):
    import av

    rnd = random.Random(seed)
    base = picture(seed, (640, 480))
    with av.open(path, "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        stream.bit_rate = bitrate
        dx, dy = rnd.randint(2, 6), rnd.randint(2, 6)
        for i in range(seconds * fps):
            frame_img = base.crop((i * dx % 200, i * dy % 150, i * dx % 200 + 400, i * dy % 150 + 300)).resize(size)
            frame = av.VideoFrame.from_image(frame_img)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
