"""Prépare rangeo/resources/geo/ à partir des données GeoNames (CC BY 4.0, https://www.geonames.org).

Usage : python tools/build_geodata.py <cities1000.txt>
Produit :
  cities.tsv.gz    lat, lon, nom, code pays, population (villes de plus de 1000 habitants)
  countries.json   {code: {"fr": nom, "en": nom}}
"""

from __future__ import annotations

import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "rangeo", "resources", "geo")


def main(src: str) -> None:
    os.makedirs(OUT, exist_ok=True)
    import math
    import re

    codes = set()
    rows = []
    big: dict[str, list[tuple[float, float]]] = {}
    with open(src, encoding="utf-8") as fh:
        for line in fh:
            p = line.split("\t")
            if len(p) > 14 and int(p[14] or 0) >= 100_000:
                big.setdefault(p[1], []).append((float(p[4]), float(p[5])))
    district = re.compile(r"^(.+?) \d{1,2}(?:e|er)?(?: |$)")

    def is_district(name: str, lat: float, lon: float) -> bool:
        """« Paris 16 Passy », « Lyon 03 » : arrondissement d'une grande ville proche."""
        m = district.match(name)
        if not m:
            return False
        for bla, blo in big.get(m.group(1), ()):
            if math.hypot(lat - bla, (lon - blo) * math.cos(math.radians(lat))) < 0.3:
                return True
        return False

    with open(src, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 15:
                continue
            if is_district(parts[1], float(parts[4]), float(parts[5])):
                continue
            name, lat, lon, fcode, cc = parts[1], float(parts[4]), float(parts[5]), parts[7], parts[8]
            # quartiers (PPLX) et lieux historiques / abandonnés exclus : on veut des villes
            if not cc or fcode in ("PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"):
                continue
            pop = int(parts[14] or 0)
            codes.add(cc)
            rows.append(f"{lat:.4f}\t{lon:.4f}\t{name}\t{cc}\t{pop}")
    with gzip.open(os.path.join(OUT, "cities.tsv.gz"), "wt", encoding="utf-8", compresslevel=9) as fh:
        fh.write("\n".join(rows))
    from babel import Locale

    fr, en = Locale("fr"), Locale("en")
    countries = {}
    for cc in sorted(codes):
        countries[cc] = {"fr": fr.territories.get(cc, cc), "en": en.territories.get(cc, cc)}
    with open(os.path.join(OUT, "countries.json"), "w", encoding="utf-8") as fh:
        json.dump(countries, fh, ensure_ascii=False, indent=0)
    with open(os.path.join(OUT, "LICENSE.txt"), "w", encoding="utf-8") as fh:
        fh.write("Données géographiques : GeoNames (https://www.geonames.org), licence CC BY 4.0.\n"
                 "Noms de pays : Unicode CLDR (licence Unicode).\n")
    print(len(rows), "villes,", len(countries), "pays")


if __name__ == "__main__":
    main(sys.argv[1])
