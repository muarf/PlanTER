"""rfn.py — distances ferroviaires par PK le long du réseau RFN.

Calcule la distance (km) entre deux gares **le long des lignes physiques**
(par différence de PK sur une même ligne, sinon en passant par les jonctions
entre lignes), plutôt que par haversine × rail_factor.

Source des données :
- `config/sncf_pk_gares.csv` : ancres PK officielles par gare/ligne
  (une gare peut être ancrée sur plusieurs lignes, ex. Lyon-Perrache sur
  830000 et 750000) ;
- `data/rfn_lignes.json` : lignes RFN (records EXPLOITE) avec géométrie et
  PK de début/fin de tronçon — utilisées uniquement pour détecter les
  **connexions** entre lignes (extrémités de tronçons proches géométriquement).

Règle de distance :
- deux gares ancrées (CSV) sur la **même ligne** → |Δpk| ;
- sinon → plus court chemin dans le graphe des lignes connectées
  (arêtes le long d'une ligne = |Δpk|, arêtes de jonction = 0 km).

Les ancres issues de la géométrie (projections) ne sont PAS utilisées pour
choisir une « même ligne » : elles servent uniquement à détecter les
jonctions entre lignes (sinon courts-circuits, cf. spike7).
"""

from __future__ import annotations

import csv
import heapq
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PK_CSV = ROOT / "config" / "sncf_pk_gares.csv"
DEFAULT_RFN_JSON = ROOT / "data" / "rfn_lignes.json"

_PK_RE = re.compile(r"^(-?)(\d+)([+-])(\d+)$")

EARTH_R_KM = 6371.0088


def parse_pk(value: object) -> float | None:
    """PK `ABC+DEF` (km) ou `ABC-DEF` (négatif) → float km. None si invalide."""
    m = _PK_RE.match(str(value))
    if not m:
        return None
    v = float(m.group(2)) + float(m.group(4)) / 1000.0
    return -v if m.group(1) else v


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(a))


class RfnIndex:
    """Index des lignes RFN et des ancres PK des gares."""

    def __init__(
        self,
        pk_csv: Path = DEFAULT_PK_CSV,
        rfn_json: Path = DEFAULT_RFN_JSON,
        conn_max_km: float = 2.5,
    ):
        self.conn_max_km = conn_max_km
        self.csv_anchors: dict[str, dict[str, float]] = {}  # uic -> {ligne: pk}
        self.pos: dict[str, tuple[float, float]] = {}  # uic -> (lat, lon)
        self.lines: dict[str, list[tuple[float, float, float]]] = {}  # ligne -> [(pk, lat, lon)]
        self.ends: dict[str, list[tuple[float, float, float]]] = {}  # ligne -> extrémités des records
        self.connections: list[tuple[str, float, str, float]] = []  # (lA, pkA, lB, pkB)
        self._grid: dict[tuple[int, int], list[tuple[str, float, float, float]]] = {}
        self._cell = 0.03  # taille de cellule de la grille spatiale (degrés, ~3 km)
        self._conn_by_line: dict[str, list[tuple[float, float, str]]] = {}  # ligne -> [(pk, autre_ligne, autre_pk)]

        self._load_pk(pk_csv)
        self._load_lines(rfn_json)
        self._build_connections()
        self._index_connections()

    # ------------------------------------------------------------------ I/O
    def _load_pk(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"CSV PK introuvable : {path}")
        with open(path, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                pk = parse_pk(row.get("pk"))
                if pk is None:
                    continue
                uic = row.get("code_uic")
                ligne = row.get("code_ligne")
                self.csv_anchors.setdefault(uic, {})[ligne] = pk
                cgeo = row.get("c_geo", "")
                if uic not in self.pos and "," in cgeo:
                    try:
                        lat, lon = (float(x.strip()) for x in cgeo.split(",", 1))
                        self.pos[uic] = (lat, lon)
                    except ValueError:
                        pass

    def _load_lines(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"RFN lignes introuvable : {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        for rec in data:
            if rec.get("mnemo") != "EXPLOITE":
                continue
            deb = parse_pk(rec.get("pk_debut_r"))
            fin = parse_pk(rec.get("pk_fin_r"))
            if deb is None or fin is None:
                continue
            geom = rec.get("geo_shape", {}).get("geometry")
            if not geom:
                continue
            coords = geom.get("coordinates")
            if geom.get("type") != "LineString":
                coords = coords[0] if coords else None
            if not coords or len(coords) < 2:
                continue
            code = rec.get("code_ligne")
            points = self.lines.setdefault(code, [])
            n = len(coords)
            cum = [0.0]
            for i in range(1, n):
                cum.append(cum[-1] + _haversine(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0]))
            total = cum[-1]
            for i, c in enumerate(coords):
                pk = deb + (fin - deb) * (cum[i] / total) if total > 0 else deb
                points.append((pk, c[1], c[0]))
            # extrémités de ce record (début et fin géométrique)
            self.ends.setdefault(code, []).append(
                (deb, coords[0][1], coords[0][0])
            )
            self.ends[code].append((fin, coords[-1][1], coords[-1][0]))
        for points in self.lines.values():
            points.sort(key=lambda p: p[0])
        # grille spatiale pour les projections rapides (jonctions)
        for code, pts in self.lines.items():
            for pk, la, oa in pts:
                self._grid.setdefault((int(la / self._cell), int(oa / self._cell)), []).append(
                    (code, pk, la, oa)
                )

    # ------------------------------------------------------------ connexions
    def _build_connections(self) -> None:
        """Jonctions entre lignes = extrémités de tronçons proches
        géométriquement (seuil conn_max_km). Une connexion relie les deux
        lignes au PK de l'extrémité et au PK projeté de l'autre côté.

        Chaque extrémité est projetée sur les lignes dont la bbox élargie
        la contient (filtrage géométrique pour éviter O(lignes²))."""
        # bbox par ligne : (lat_min, lat_max, lon_min, lon_max)
        bbox: dict[str, tuple[float, float, float, float]] = {}
        for code, pts in self.lines.items():
            if not pts:
                continue
            lat_min = min(p[1] for p in pts)
            lat_max = max(p[1] for p in pts)
            lon_min = min(p[2] for p in pts)
            lon_max = max(p[2] for p in pts)
            bbox[code] = (lat_min, lat_max, lon_min, lon_max)

        eps = self.conn_max_km
        conns: set[tuple] = set()
        for code, ends in self.ends.items():
            if code not in self.lines:
                continue
            for pka, la, oa in ends:
                for other, (la_min, la_max, lo_min, lo_max) in bbox.items():
                    if other == code:
                        continue
                    if not (la_min - eps <= la <= la_max + eps and lo_min - eps <= oa <= lo_max + eps):
                        continue
                    pkb, d = self._project(other, la, oa)
                    if pkb is not None and d <= self.conn_max_km:
                        conns.add(tuple(sorted([(code, pka), (other, pkb)])))

        # --- jonctions ancrées aux gares (alignement PK) ---
        # Une gare à la jonction de deux lignes est ancrée sur sa ligne A au
        # PK `pkb` ; l'autre ligne B passe par cette gare à son extrémité de
        # tronçon `pka` (son PK propre, pas celui de A). On relie donc
        # (lignea, pkb) ↔ (code, pka). NB : on n'utilise PAS `pkb` des deux
        # côtés — les PK sont spécifiques à chaque ligne (ex. Angers :
        # 515000 à 342,95 mais 450000 à 306,26).
        stations: list[tuple[str, float, float, dict]] = [
            (uic, lat, lon, anchors)
            for uic, anchors in self.csv_anchors.items()
            if (pos := self.pos.get(uic))
            for lat, lon in [pos]
        ]
        station_conns: dict[frozenset, tuple] = {}
        eps_deg = eps / 80.0  # 2.5 km ≈ 0.03° (borne haute, pré-filtre grossier)
        for code, ends in self.ends.items():
            if code not in self.lines:
                continue
            for pka, la, oa in ends:
                for uic, sla, slo, anchors in stations:
                    if abs(la - sla) > eps_deg or abs(oa - slo) > eps_deg:
                        continue
                    if _haversine(la, oa, sla, slo) > eps:
                        continue
                    for lignea, pkb in anchors.items():
                        if lignea == code or lignea not in self.lines:
                            continue
                        key = frozenset({lignea, code})
                        station_conns[key] = (lignea, pkb, code, pka)

        # les connexions ancrées à une gare remplacent celles du bord de tronçon
        final = list(station_conns.values())
        for c in conns:
            pair = frozenset({c[0][0], c[1][0]})
            if pair not in station_conns:
                final.append((c[0][0], c[0][1], c[1][0], c[1][1]))
        self.connections = final

    def _index_connections(self) -> None:
        """Index connexions par ligne (construit une seule fois, utilisé par
        _dijkstra) : ligne -> [(pk, autre_ligne, autre_pk)]."""
        by_line: dict[str, list[tuple[float, float, str]]] = {}
        for la, pka, lb, pkb in self.connections:
            by_line.setdefault(la, []).append((pka, lb, pkb))
            by_line.setdefault(lb, []).append((pkb, la, pka))
        self._conn_by_line = by_line

    def _project(self, code: str, lat: float, lon: float) -> tuple[float | None, float]:
        cell = self._cell
        best_d, best_pk = math.inf, None
        gx0, gy0 = int(lat / cell), int(lon / cell)
        for gx in range(gx0 - 1, gx0 + 2):
            for gy in range(gy0 - 1, gy0 + 2):
                for lcode, pk, pla, plo in self._grid.get((gx, gy), ()):
                    if lcode != code:
                        continue
                    d = _haversine(pla, plo, lat, lon)
                    if d < best_d:
                        best_d, best_pk = d, pk
        return best_pk, best_d

    # ------------------------------------------------------------- distances
    def hop_km(self, uic_a: str | None, uic_b: str | None) -> float | None:
        """Distance (km) entre deux gares le long du réseau RFN, ou None si
        introuvable (fallback haversine côté appelant)."""
        if uic_a is None or uic_b is None or uic_a == uic_b:
            return 0.0 if uic_a == uic_b else None
        anchors_a = self.csv_anchors.get(uic_a)
        anchors_b = self.csv_anchors.get(uic_b)
        if not anchors_a or not anchors_b:
            return None
        common = set(anchors_a) & set(anchors_b)
        if common:
            best = min(abs(anchors_a[l] - anchors_b[l]) for l in common)
            return best
        return self._dijkstra(anchors_a, anchors_b)

    def _dijkstra(self, src: dict[str, float], dst: dict[str, float]) -> float | None:
        """Plus court chemin entre (ligne,pk) sources et cibles via le graphe
        des connexions. Les nœuds sont les points de connexion des lignes ;
        le déplacement le long d'une ligne se fait par |Δpk| depuis n'importe
        quel nœud, et la cible est atteinte en glissant le long de sa ligne."""
        by_line = self._conn_by_line

        # cibles groupées par ligne
        targets_by_line: dict[str, list[float]] = {}
        for ligne, pk in dst.items():
            targets_by_line.setdefault(ligne, []).append(pk)

        best = math.inf
        dist: dict[tuple[str, float], float] = {}
        pq: list[tuple[float, str, float]] = []
        for ligne, pk in src.items():
            dist[(ligne, pk)] = 0.0
            heapq.heappush(pq, (0.0, ligne, pk))

        while pq:
            d, ligne, pk = heapq.heappop(pq)
            if d >= best:
                continue
            if d > dist.get((ligne, pk), math.inf):
                continue
            # glissement le long de la ligne jusqu'aux cibles
            for tpk in targets_by_line.get(ligne, []):
                nd = d + abs(pk - tpk)
                if nd < best:
                    best = nd
            # glissement vers les connexions + passage sur l'autre ligne
            for pkc, other, pko in by_line.get(ligne, []):
                nd = d + abs(pk - pkc)
                if nd < dist.get((ligne, pkc), math.inf):
                    dist[(ligne, pkc)] = nd
                    heapq.heappush(pq, (nd, ligne, pkc))
                if nd < dist.get((other, pko), math.inf):
                    dist[(other, pko)] = nd
                    heapq.heappush(pq, (nd, other, pko))
        return best if math.isfinite(best) else None


def uic_from_stop_id(stop_id: str) -> str | None:
    """UIC extrait d'un stop_id GTFS (`StopArea:OCE87734004` → `87734004`)."""
    if stop_id.startswith("StopArea:OCE"):
        return stop_id[len("StopArea:OCE"):]
    if stop_id.startswith("StopPoint:OCETrainTER-"):
        return stop_id[len("StopPoint:OCETrainTER-"):].split(":")[0]
    if stop_id.startswith("OCE"):
        return stop_id[3:]
    return None
