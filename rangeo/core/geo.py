"""Géocodage inverse hors ligne : coordonnées GPS → ville et pays.

Données GeoNames (villes de plus de 1000 habitants, licence CC BY 4.0), chargées
à la première utilisation et indexées par cases de 1° × 1°.
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import threading

from .appdirs import resource_path

log = logging.getLogger(__name__)

MAX_DISTANCE_KM = 40  # au-delà, on considère qu'il n'y a pas de ville connue (en mer, désert…)

_lock = threading.Lock()
_grid: dict[tuple[int, int], list[tuple[float, float, str, str, float]]] | None = None
_countries: dict[str, dict[str, str]] | None = None


def _load() -> None:
    global _grid, _countries
    with _lock:
        if _grid is not None:
            return
        grid: dict[tuple[int, int], list] = {}
        try:
            with gzip.open(resource_path("resources", "geo", "cities.tsv.gz"), "rt", encoding="utf-8") as fh:
                for line in fh:
                    lat, lon, name, cc, pop = line.rstrip("\n").split("\t")
                    la, lo = float(lat), float(lon)
                    weight = math.sqrt(max(int(pop or 0), 1000))
                    grid.setdefault((math.floor(la), math.floor(lo)), []).append((la, lo, name, cc, weight))
        except (OSError, ValueError):
            log.exception("Données géographiques illisibles")
        try:
            with open(resource_path("resources", "geo", "countries.json"), encoding="utf-8") as fh:
                _countries = json.load(fh)
        except (OSError, ValueError):
            _countries = {}
        _grid = grid
        log.info("Géocodage : %d villes chargées", sum(len(v) for v in grid.values()))


def _distance_km(la1: float, lo1: float, la2: float, lo2: float) -> float:
    x = math.radians(lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
    y = math.radians(la2 - la1)
    return 6371.0 * math.hypot(x, y)


def reverse(lat: float, lon: float) -> tuple[str, str] | None:
    """(ville, code pays) de la ville la plus « attirante » : proche et peuplée.

    Score = √population / (distance + 1)² : une photo prise à 4 km du centre de Paris est à « Paris »,
    une photo prise dans un village reste au village.
    """
    _load()
    best = None
    best_score = 0.0
    cx, cy = math.floor(lat), math.floor(lon)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            lo_cell = cy + dy
            if lo_cell >= 180:
                lo_cell -= 360
            elif lo_cell < -180:
                lo_cell += 360
            for la, lo, name, cc, weight in _grid.get((cx + dx, lo_cell), ()):
                d = _distance_km(lat, lon, la, lo)
                if d > MAX_DISTANCE_KM:
                    continue
                score = weight / (d + 1.0) ** 2
                if score > best_score:
                    best_score, best = score, (name, cc)
    return best


def country_name(code: str, lang: str = "fr") -> str:
    _load()
    names = (_countries or {}).get(code or "")
    if not names:
        return code or ""
    return names.get(lang) or names.get("en") or code


def enrich(meta: dict, lang: str = "fr") -> None:
    """Ajoute ville / pays à des métadonnées contenant des coordonnées GPS."""
    gps = meta.get("gps")
    if not gps or len(gps) != 2:
        return
    try:
        found = reverse(float(gps[0]), float(gps[1]))
    except (TypeError, ValueError):
        return
    if found:
        city, cc = found
        meta["city"] = city
        meta["country_code"] = cc
        meta["country"] = country_name(cc, lang)
